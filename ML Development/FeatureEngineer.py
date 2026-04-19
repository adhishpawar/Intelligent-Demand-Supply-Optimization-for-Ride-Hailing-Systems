class FeatureEngineer:
    def create_time_window(self, df, freq='15T'):
        df = df.set_index('request_time').sort_index()
        demand = (
            df.groupby('zone_id')
            .resample(freq)['trip_id']
            .count()
            .reset_index()
            .rename(columns = {'trip_id' : 'demand',
                               'request_time' : 'window_start'})
            
        )   
        return demand

    def add_temporal_festures(self, df):
        df['hour'] = df['window_start'].dt.hour
        df['day_of_week'] = df['window_start'].dt.dayofweek
        df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)
        df['month'] = df['window_start'].dt.month
        df['is_peak'] = df['hour'].isin([7, 8, 9, 17, 18, 19]).astype(int)
        return df
    
    def add_lag_features(self, df):
        df = df.sort_values(['zone_id', 'window_start'])
        for lag in [1, 2, 4, 8]: # 15, 30, 60, 120 min ago
            df[f'demand_lag{lag}'] = (
                df.groupby('zone_id')['demand']
                .shift(lag)
            )

        # Rolling stats
        df['demand_roll3'] = (
            df.groupby('zone_id')['demand']
            .transform(lambda x: x.shift(1).rolling(3).mean())
        )

        df['demand_roll8'] = (
            df.groupby('zone_id')['demand']
            .transform(lambda x: x.shift(1).rolling(8).mean())
        )

        return df.dropna()
    
    def add_external_festures(self, df, weather_df, event_df):
        ## Merge weather score by zone + time window
        df = df.merge(weather_df[['zone_id', 'window_start', 'weather_score']],
                      on = ['zone_id', 'window_start'], how = 'left')
        
        df = df.merge(event_df[['zone_id', 'window_start', 'event_score']],
                      on = ['zone_id', 'window_start'], how = 'left')

        df[['weather_score', 'event_score']] = (
            df[['weather_score', 'event_score']].fillna(0)
        )

        return df
