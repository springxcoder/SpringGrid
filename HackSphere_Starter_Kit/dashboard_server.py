"""Serve the SpringGrid dashboard and connect it to the existing Python engine.

Run from this folder with: python3 dashboard_server.py
Then open: http://127.0.0.1:8000
"""

import json
import contextlib
import io
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

import pandas as pd

import logic
import predict as prediction_engine
from predict import predict


ROOT = Path(__file__).resolve().parent
REQUIRED_COLUMNS = [
    "timestamp",
    "ambient_temp_c",
    "humidity_pct",
    "solar_irradiance_wm2",
]
DEFAULT_ENDPOINT = (
    "https://api.catalyst.zoho.in/quickml/v1/project/"
    "104139000000013078/endpoints/predict"
)
PREDICTION_LOCK = Lock()


class DashboardHandler(SimpleHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/health":
            return self._json(200, {"ok": True, "engine": "SpringGrid Python pipeline"})
        if self.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/forecast":
            return self._json(404, {"error": "Endpoint not found."})

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length > 2_000_000:
                return self._json(413, {"error": "The telemetry upload is too large."})
            body = self.rfile.read(content_length) if content_length else b"{}"
            request = json.loads(body.decode("utf-8"))
            rows = request.get("rows")
            filename = request.get("filename")

            if rows is None:
                source = ROOT / "sample_test_input.csv"
                input_frame = pd.read_csv(source, usecols=REQUIRED_COLUMNS).head(24)
                filename = source.name
            else:
                if not isinstance(rows, list) or not rows:
                    return self._json(400, {"error": "Provide at least one telemetry row."})
                input_frame = pd.DataFrame(rows).head(24)

            missing = [column for column in REQUIRED_COLUMNS if column not in input_frame.columns]
            if missing:
                return self._json(400, {"error": "Missing required fields: " + ", ".join(missing)})

            input_frame = input_frame[REQUIRED_COLUMNS].copy()
            parsed_timestamps = pd.to_datetime(input_frame["timestamp"], errors="coerce")
            for column in REQUIRED_COLUMNS[1:]:
                input_frame[column] = pd.to_numeric(input_frame[column], errors="coerce")
            if parsed_timestamps.isna().any() or input_frame[REQUIRED_COLUMNS[1:]].isna().any().any():
                return self._json(400, {"error": "Some rows have invalid timestamps or missing/non-numeric weather values."})
            if input_frame.empty:
                return self._json(400, {"error": "No usable telemetry rows were found."})
            # Keep timestamps as strings for JSON serialization in the QuickML request.
            input_frame["timestamp"] = parsed_timestamps.dt.strftime("%Y-%m-%d %H:%M:%S")

            prediction_log = io.StringIO()
            with PREDICTION_LOCK:
                with contextlib.redirect_stdout(prediction_log):
                    predictions = predict(input_frame)
            endpoint_configured = prediction_engine.QUICKML_ENDPOINT_URL != DEFAULT_ENDPOINT
            fell_back = "Warning: QuickML endpoint call failed" in prediction_log.getvalue()
            if endpoint_configured and not fell_back:
                engine = "QuickML prediction"
            elif endpoint_configured:
                engine = "Local estimate · QuickML unavailable"
            else:
                engine = "Local starter estimate"
            dispatch = logic.apply_transition_logic(input_frame, predictions)
            forecast = []
            for index, signal in enumerate(dispatch):
                row = input_frame.iloc[index]
                forecast.append({
                    "timestamp": pd.to_datetime(row["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                    "demand": round(float(signal["demand"]), 2),
                    "grid_mode": signal["grid_mode"],
                    "overload_alert": bool(signal["overload_alert"]),
                    "ambient_temp_c": float(row["ambient_temp_c"]),
                    "humidity_pct": float(row["humidity_pct"]),
                    "solar_irradiance_wm2": float(row["solar_irradiance_wm2"]),
                })

            return self._json(200, {
                "forecast": forecast,
                "filename": filename,
                "engine": engine,
            })
        except json.JSONDecodeError:
            return self._json(400, {"error": "The request body must be valid JSON."})
        except Exception as exc:
            self.log_error("Forecast failed: %s", exc)
            return self._json(500, {"error": "The Python forecast pipeline could not process this telemetry."})

    def log_message(self, format_string, *args):
        # Keep local development output concise while retaining API failures.
        if self.path.startswith("/api/"):
            super().log_message(format_string, *args)


if __name__ == "__main__":
    handler = lambda *args, **kwargs: DashboardHandler(*args, directory=str(ROOT), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 8000), handler)
    print("SpringGrid dashboard ready at http://127.0.0.1:8000")
    print("Press Ctrl+C to stop the dashboard server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSpringGrid dashboard stopped.")
    finally:
        server.server_close()
