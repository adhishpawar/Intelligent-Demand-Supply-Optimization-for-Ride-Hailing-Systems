from flask import Flask, request, jsonify
import joblib, numpy as np, pandas as pd
from datetime import datetime

app = Flask(__name__)
model = joblib.load('models/demand_model.joblib')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status':'ok', 'model_version': '2.1'})

@app.route('/predict/demand', methods=['POST'])
def predict_demand():
    """
    POST /predict/demand
    BODY: {zone_id: int, window_start: 'YYYY-MM-DD HH:MM:SS'}
    Returns: {zone_id, window_start, predicted_demand, confidance}
    """

    data = request.get_json()

    zone_id = data['zone_id']
    ts = pd.Timestamp(data['window_start'])

    festures = {
        'zone_id':       zone_id,
        'hour':          ts.hour,
        'day_of_week':   ts.dayofweek,
        'is_weekend':    int(ts.dayofweek >= 5),
        'month':         ts.month,
        'is_peak':       int(ts.hour in [7,8,9,17,18,19]),
        'demand_lag1':   data.get('demand_lag1', 0),
        'demand_lag2':   data.get('demand_lag2', 0),
        'demand_lag4':   data.get('demand_lag4', 0),
        'demand_lag8':   data.get('demand_lag8', 0),
        'demand_roll3':  data.get('demand_roll3', 0),
        'demand_roll8':  data.get('demand_roll8', 0),
        'weather_score': data.get('weather_score', 0),
        'event_score':   data.get('event_score', 0),
    }

    X = pd.DataFrame([festures])
    predicted = float(model.predict(X) [0])
    predicted = max(0, round(predicted, 1)) # no negative damand

    return jsonify({
        'zone_id':          zone_id,
        'window_start':     str(ts),
        'predicted_demand': predicted,
        'unit':             'rides per 15 minutes'
    })

@app.route('/predict/batch', methods = ['POST'])
def predict_batch():
    """" Predict for all zones for next N windows"""
    data = request.get_json()
    results = []
    for zone_data in data['zones']:
        X = pd.DataFrame([[zone_data]])
        pred = max(0, float(model.predict(X) [0]))
        results.append({'zone_id' : zone_data['zone_id'], 'demand' : pred})
    return jsonify({'predictions' : results})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)