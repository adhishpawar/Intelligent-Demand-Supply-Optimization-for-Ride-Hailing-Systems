import lightgbm as lgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import mean_absolute_error, mean_squared_error
import numpy as np, mlflow
import pandas as pd

class DemandForecaster:
    FEATURE_COLS = [
        'zone_id', 'hour', 'day_of_week', 'is_weekend', 'month', 'is_peak',
        'demand_lag1', 'demand_lag2', 'demand_lag4', 'demand_lag8',
        'demand_roll3', 'demand_roll8',
        'weather_score', 'event_score'
    ]

    TARGET = 'demand'

    def __init__(self):
        self.model = lgb.LGBMRegressor(
            n_estimators = 500,
            learning_rate = 0.05,
            max_depth = 7,
            num_leaves= 63,
            subsample = 0.8,
            colsample_bytree = 0.8,
            min_child_samples = 20,
            random_state=42,
            n_jobs=-1
        )

    def time_based_split(self, df, test_days = 7):
        split_point = df['window_start'].max() - pd.Timedelta(days = test_days)
        train = df[df['window_start'] <= split_point]
        test =  df[df['window_start'] > split_point]
        return train, test
    
    def train(self, df):
        train_df, test_df = self.time_based_split(df)

        X_train = train_df[self.FEATURE_COLS]
        y_train = train_df[self.TARGET]
        X_test  = test_df[self.FEATURE_COLS]
        y_test =   test_df[self.TARGET]

        with mlflow.start_run():
            mlflow.log_params(self.model.get_params())

            self.model.fit(
                X_train,
                y_train,
                eval_set = [(X_test, y_test)],
                callbacks = [lgb.early_stopping(50),
                             lgb.log_evaluation(100)]
            )

            preds = self.model.predict(X_test)
            mae = mean_absolute_error(y_test, preds)
            rmse = np.sqrt(mean_squared_error(y_test, preds))

            mlflow.log_metric('mae', mae)
            mlflow.log_metric('rmse', rmse)
            mlflow.sklearn.log_model(self.model, 'demand_model')

            print(f'MAE: {mae:.2f}')
            print(f'RMSE: {rmse:.2f}')
        return self.model
