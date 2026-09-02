import pandas as pd
import numpy as np
from sklearn.cluster import KMeans

class DataPreProcessor:
    def __init__(self, n_zones=20):
        self.n_zones = n_zones
        self.kmeans = KMeans(n_clusters=n_zones, random_state=42)
    
    def load_and_clean(self, filepath):
        # Count total rows
        total_rows = sum(1 for _ in open(filepath)) - 1
        # Read only 25%
        df = pd.read_csv(
            filepath,
            nrows=int(total_rows * 0.25),
            parse_dates=['tpep_pickup_datetime']
        )
        # df = pd.read_csv(filepath, parse_dates=['tpep_pickup_datetime'])

        #Remove invalid trips
        df = df[(df['trip_distance'] > 0.1) & (df['trip_distance'] < 100)]
        df = df[(df['fare_amount'] > 0) & (df['fare_amount'] < 500)]
        df = df.dropna(subset=['pickup_longitude', 'pickup_latitude'])

        # GPS bounds for NYC
        df = df[(df['pickup_latitude'].between(40.4, 41.0)) &
                (df['pickup_longitude'].between(-74.3, -73.6))]
        
        return df
    
    def transform_festure(self, df):
        df = df.copy()

        # Miles to km
        df['distance_km'] = df['trip_distance'] * 1.60934

        # Duration in Minutes
        # df['duration_min'] = pd.to_numeric(
        #     df['trip_duration'] / 60, errors='coerce'
        # )

        # Calculate duration from timestamps
        df['duration_min'] = (
            (df['tpep_dropoff_datetime'] - df['tpep_pickup_datetime'])
            .dt.total_seconds() / 60
        )

        # Remove invalid durations
        df = df[(df['duration_min'] > 1) & (df['duration_min'] < 300)]

        # Synthetic request time (2-7 min becore pickup)
        offset = np.random.randint(2, 8, size=len(df))
        df['request_time'] = (df['tpep_pickup_datetime'] - pd.to_timedelta(offset, unit='m'))

        # Log Transformation Skewed feature
        for col in ['distance_km', 'fare_amount', 'duration_min']:
            df[col] = np.log1p(df[col])
        return df
    
    def create_zones(self, df):
        coords = df[['pickup_latitude', 'pickup_longitude']].values
        df['zone_id'] = self.kmeans.fit_predict(coords)
        return df
                
