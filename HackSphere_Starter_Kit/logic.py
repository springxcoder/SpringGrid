import pandas as pd
from predict import predict 

def apply_transition_logic(input_df, predictions):
    results = []
    
    for i, row in input_df.iterrows():
        timestamp = pd.to_datetime(row['timestamp'])
        hour = timestamp.hour
        demand = predictions[i]

        if demand > 160 or (18 <= hour <= 22):
            grid_mode = "Renewable Storage Mode"
        else:
            grid_mode = "Main Grid Mode"

        overload_alert = bool(demand >= 200)
            
        results.append({
            "timestamp": timestamp.strftime("%H:%M"),
            "demand": round(float(demand), 2),
            "grid_mode": grid_mode,
            "overload_alert": overload_alert
        })
        
    return results


if __name__ == "__main__":

    input_df = pd.read_csv("sample_test_input.csv")
    
    raw_predictions = predict(input_df)
    
    final_data = apply_transition_logic(input_df, raw_predictions)
    
    for row in final_data[:5]:
        print(row)