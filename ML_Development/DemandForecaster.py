"""
==============================================================================
DemandForecaster.py  —  FR-01: Demand Prediction  |  MLDLC Phase 6 & 7
==============================================================================

PURPOSE
-------
Train, evaluate, and register a LightGBM regression model that predicts
ride demand per zone per 15-minute window.

ALGORITHM: LightGBM (Light Gradient Boosting Machine)
======================================================

WHAT IS GRADIENT BOOSTING?
---------------------------
An ensemble method that builds N decision trees SEQUENTIALLY.
Each new tree corrects the residual errors (mistakes) of all previous trees.

Step 1: Tree 1 predicts demand → errors = actual − predicted
Step 2: Tree 2 predicts the ERRORS of Tree 1
Step 3: Tree 3 predicts the remaining errors of Trees 1+2
...
Final prediction = sum of all N trees × learning_rate

WHY LIGHTGBM OVER XGBOOST, RANDOM FOREST, LSTM?
-------------------------------------------------
Compared to XGBoost:
  - Leaf-wise tree growth vs level-wise → faster, more accurate on tabular data
  - 10-20× faster training on large datasets
  - Less RAM usage via histogram binning

Compared to Random Forest:
  - RF averages N independent trees (bagging) → can't fix systematic errors
  - LightGBM sequentially corrects errors → typically lower MAE

Compared to LSTM (deep learning):
  - LSTM needs thousands of rows per series → we have 20 zones × limited windows
  - LightGBM works excellently with hand-crafted lag features
  - LSTM requires GPU and much longer training time
  - Interpretability: LightGBM gives feature importance; LSTM is a black box

HYPERPARAMETERS EXPLAINED
--------------------------
n_estimators    : Number of trees. Too few → underfit. Too many → overfit.
                  Use early_stopping to find optimal N automatically.
learning_rate   : Step size (shrinkage). Lower = more trees needed but better
                  generalization. 0.05 is a safe default.
max_depth       : Maximum depth of each tree. Depth=7 → up to 128 leaf nodes.
num_leaves      : Must be < 2^max_depth. Controls tree complexity.
                  num_leaves=63 with max_depth=7 is standard.
subsample       : 80% of rows sampled per tree (stochastic gradient boosting).
                  Reduces overfitting, speeds up training.
colsample_bytree: 80% of features sampled per tree.
                  Prevents any one feature from dominating.
min_child_samples: Min data points in a leaf. =20 prevents tiny leaf splits
                   that memorize noise.

EVALUATION METRICS
------------------
MAE  (Mean Absolute Error):   avg(|actual − predicted|)
  → "On average, I'm off by X rides per 15-min window"
  → Target: MAE < 4.0 rides
  → Intuitive, robust to outliers, directly interpretable

RMSE (Root Mean Square Error): sqrt(avg((actual − predicted)²))
  → Penalizes large errors more heavily than MAE
  → RMSE > MAE always (equal only if all errors are identical)
  → Target: RMSE < 6.0 rides

MAPE (Mean Absolute Percentage Error): avg(|actual − predicted| / actual)
  → Scale-independent: 15% error means ±15% off actual count
  → Problematic when actual = 0 (division by zero) → use +1 smoothing

BASELINE COMPARISON
--------------------
Always compare against a naive baseline:
  naive_pred = demand from same window LAST WEEK (lag=672 for 15-min freq)
  If LightGBM MAE > naive MAE → model is useless, debug features/leakage.

MLFLOW INTEGRATION
------------------
Every training run logs:
  - Parameters: all hyperparameters
  - Metrics: MAE, RMSE, MAPE per run AND per zone
  - Artifacts: trained model file, feature importance plot
  - Model registry: model promoted to Staging/Production based on MAE gate
==============================================================================
"""

import os
import logging
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import lightgbm as lgb
import mlflow
import mlflow.lightgbm
import joblib
from sklearn.metrics import mean_absolute_error, mean_squared_error
from mlflow.models.signature import infer_signature

from FeatureEngineer import FeatureEngineer

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────
TARGET            = "demand"
MAE_GATE          = 4.0       # model must beat this to enter Staging
MLFLOW_URI        = "http://127.0.0.1:5001"
EXPERIMENT_NAME   = "FR01_demand_forecasting"
MODEL_REGISTRY_NAME = "demand_forecaster_fr01"


class DemandForecaster:
    """
    Trains and evaluates a LightGBM demand forecasting model.

    Usage
    -----
    >>> fc = DemandForecaster()
    >>> fc.run("Data_Processing/features.csv")
    """

    # ── Default hyperparameters ─────────────────────────────
    DEFAULT_PARAMS = dict(
        n_estimators      = 1000,    # will be reduced by early stopping
        learning_rate     = 0.05,
        max_depth         = 7,
        num_leaves        = 63,
        subsample         = 0.80,
        colsample_bytree  = 0.80,
        min_child_samples = 20,
        reg_alpha         = 0.1,     # L1 regularization
        reg_lambda        = 0.1,     # L2 regularization
        random_state      = 42,
        n_jobs            = -1,
        verbose           = -1,
    )

    def __init__(self, params: dict = None):
        self.params    = {**self.DEFAULT_PARAMS, **(params or {})}
        self.model_    = None
        self.feature_cols_ = FeatureEngineer.get_feature_columns()

    def run(self, features_csv: str) -> None:
        """Full train → evaluate → save → MLflow pipeline."""
        logger.info("Loading feature matrix …")
        df = pd.read_csv(features_csv, parse_dates=["window_start"])

        logger.info("Splitting train / test …")
        fe = FeatureEngineer()
        train_df, test_df = fe.time_based_split(df)

        logger.info("Training LightGBM …")
        self._setup_mlflow()
        model, run_id, metrics = self._train(train_df, test_df)

        logger.info("Saving model …")
        os.makedirs("models", exist_ok=True)
        joblib.dump(model, "models/demand_model.joblib")

        logger.info("Evaluating per-zone performance …")
        self._per_zone_eval(model, test_df)

        logger.info("Plotting feature importance …")
        self._plot_feature_importance(model, run_id)

        logger.info("Comparing against naive baseline …")
        self._baseline_comparison(test_df)

        logger.info(f"\n{'='*55}")
        logger.info(f"  FINAL RESULTS")
        logger.info(f"  MAE  = {metrics['mae']:.4f}  (target < {MAE_GATE})")
        logger.info(f"  RMSE = {metrics['rmse']:.4f}")
        logger.info(f"  MAPE = {metrics['mape']:.1f}%")
        status = "✓ PASSED — promoting to MLflow Staging" if metrics["mae"] < MAE_GATE \
                 else "✗ FAILED — not promoted"
        logger.info(f"  MAE Gate: {status}")
        logger.info(f"{'='*55}\n")

        if metrics["mae"] < MAE_GATE:
            self._promote_model()

    # ──────────────────────────────────────────────────────────
    # PRIVATE
    # ──────────────────────────────────────────────────────────

    def _setup_mlflow(self):
        """Configure MLflow tracking server and experiment."""
        try:
            mlflow.set_tracking_uri(MLFLOW_URI)
            mlflow.set_experiment(EXPERIMENT_NAME)
            logger.info(f"MLflow tracking URI: {MLFLOW_URI}")
        except Exception:
            # Fallback: log locally to ./mlruns/
            mlflow.set_tracking_uri("./mlruns")
            mlflow.set_experiment(EXPERIMENT_NAME)
            logger.warning("MLflow server not reachable — logging to ./mlruns/")

    def _train(self, train_df: pd.DataFrame, test_df: pd.DataFrame):
        """
        Train LightGBM with early stopping and full MLflow logging.

        EARLY STOPPING
        --------------
        We set n_estimators=1000 but pass early_stopping(50):
        If the validation MAE doesn't improve for 50 consecutive trees,
        training stops automatically. This prevents overfitting AND
        saves training time.
        """
        X_train = train_df[self.feature_cols_]
        y_train = train_df[TARGET]
        X_test  = test_df[self.feature_cols_]
        y_test  = test_df[TARGET]

        model = lgb.LGBMRegressor(**self.params)

        with mlflow.start_run() as run:
            run_id = run.info.run_id

            # ── Log all hyperparameters ──
            mlflow.log_params(self.params)
            mlflow.log_param("train_rows",    len(train_df))
            mlflow.log_param("test_rows",     len(test_df))
            mlflow.log_param("n_features",    len(self.feature_cols_))
            mlflow.log_param("n_zones",       train_df["zone_id"].nunique())
            mlflow.log_param("window_freq",   "15min")
            mlflow.log_param("target",        TARGET)

            # ── Train ──
            model.fit(
                X_train, y_train,
                eval_set=[(X_test, y_test)],
                eval_metric="mae",
                callbacks=[
                    lgb.early_stopping(stopping_rounds=50, verbose=False),
                    lgb.log_evaluation(period=100),
                ],
            )

            # ── Predict ──
            preds = np.maximum(0.0, model.predict(X_test))   # clamp negatives

            # ── Compute metrics ──
            mae  = mean_absolute_error(y_test, preds)
            rmse = np.sqrt(mean_squared_error(y_test, preds))
            # MAPE with +1 smoothing to avoid division-by-zero
            mape = np.mean(np.abs((y_test.values - preds) / (y_test.values + 1))) * 100

            # ── Log metrics ──
            mlflow.log_metric("mae",           round(mae,  4))
            mlflow.log_metric("rmse",          round(rmse, 4))
            mlflow.log_metric("mape",          round(mape, 2))
            mlflow.log_metric("best_iteration",model.best_iteration_)

            # ── Log model to registry ──
            signature = infer_signature(X_train, model.predict(X_train))
            mlflow.lightgbm.log_model(
                lgb_model           = model,
                artifact_path       = "lgbm_model",
                signature           = signature,
                registered_model_name = MODEL_REGISTRY_NAME,
            )

            logger.info(
                f"Run {run_id[:8]} | "
                f"MAE={mae:.3f} | RMSE={rmse:.3f} | MAPE={mape:.1f}% | "
                f"Trees={model.best_iteration_}"
            )

        self.model_ = model
        return model, run_id, {"mae": mae, "rmse": rmse, "mape": mape}

    def _per_zone_eval(self, model, test_df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute MAE per zone. Identifies zones the model struggles with.
        High-MAE zones = irregular demand patterns → need extra features.
        """
        X_test = test_df[self.feature_cols_]
        preds  = np.maximum(0.0, model.predict(X_test))
        test_df = test_df.copy()
        test_df["pred"] = preds

        zone_metrics = (
            test_df.groupby("zone_id")
            .apply(lambda g: pd.Series({
                "mae"       : mean_absolute_error(g[TARGET], g["pred"]),
                "rmse"      : np.sqrt(mean_squared_error(g[TARGET], g["pred"])),
                "mean_demand": g[TARGET].mean(),
                "n_windows" : len(g),
            }))
            .round(3)
            .sort_values("mae", ascending=False)
        )
        logger.info("\nPer-zone MAE (top 5 worst):\n" + zone_metrics.head(5).to_string())
        zone_metrics.to_csv("models/per_zone_metrics.csv")
        return zone_metrics

    def _plot_feature_importance(self, model, run_id: str) -> None:
        """
        Plot feature importance and log to MLflow.

        TYPE='gain' measures how much each feature reduces prediction error
        when it's used in a split. More gain = more important feature.

        WHY THIS MATTERS:
        - Validates our intuition: demand_lag1 should be #1
        - Identifies useless features to remove (< 1% importance)
        - Helps debug unexpected results
        """
        os.makedirs("eda_outputs", exist_ok=True)
        importance = pd.Series(
            model.feature_importances_,
            index=self.feature_cols_
        ).sort_values(ascending=True)

        fig, ax = plt.subplots(figsize=(10, 8))
        importance.plot(kind="barh", ax=ax, color="#2E75B6")
        ax.set_title("Feature Importance (Gain) — FR-01 Demand Model", fontsize=13)
        ax.set_xlabel("Importance (Gain)")
        ax.axvline(importance.mean(), color="red", linestyle="--", label="Mean importance")
        ax.legend()
        plt.tight_layout()

        path = f"eda_outputs/feature_importance_{run_id[:8]}.png"
        fig.savefig(path, dpi=150)
        plt.close()

        with mlflow.start_run(run_id=run_id):
            mlflow.log_artifact(path, "plots")
        logger.info(f"Feature importance saved: {path}")

    def _baseline_comparison(self, test_df: pd.DataFrame) -> None:
        """
        Compare model vs naive baselines.

        BASELINE 1: Last 15-min demand (demand_lag1)
          → Simplest possible prediction. Model must beat this.

        BASELINE 2: Historical mean for (zone, hour)
          → "At 8am in zone 5, average demand is 12 rides."

        IF MODEL MAE > BASELINE MAE: Something is wrong.
        Common causes:
          - Data leakage in features (too-good training, bad test)
          - Feature list mismatch between train and test
          - Wrong time-based split
        """
        X_test = test_df[self.feature_cols_]
        actual = test_df[TARGET].values

        # Baseline 1: lag1 prediction
        lag1_preds = test_df["demand_lag1"].fillna(0).values
        lag1_mae   = mean_absolute_error(actual, lag1_preds)

        # Baseline 2: zone×hour historical mean
        zone_hour_mean = (
            test_df.groupby(["zone_id", "hour"])[TARGET].transform("mean")
        ).values
        hist_mae = mean_absolute_error(actual, zone_hour_mean)

        # Model MAE
        model_preds = np.maximum(0, self.model_.predict(X_test))
        model_mae   = mean_absolute_error(actual, model_preds)

        logger.info(
            f"\nBaseline Comparison:\n"
            f"  Naive lag1 MAE     : {lag1_mae:.3f}\n"
            f"  Historical mean MAE: {hist_mae:.3f}\n"
            f"  LightGBM model MAE : {model_mae:.3f}  ← should be lowest\n"
        )

    def _promote_model(self) -> None:
        """
        Promote the latest model version to 'Staging' in MLflow registry.
        Production promotion should be done manually after user testing.
        """
        try:
            from mlflow.tracking import MlflowClient
            client = MlflowClient()
            versions = client.get_latest_versions(MODEL_REGISTRY_NAME, stages=["None"])
            if versions:
                v = versions[0].version
                client.transition_model_version_stage(
                    name    = MODEL_REGISTRY_NAME,
                    version = v,
                    stage   = "Staging",
                )
                logger.info(f"Model v{v} promoted to MLflow Staging.")
        except Exception as e:
            logger.warning(f"MLflow promotion failed (likely offline): {e}")
            logger.info("Manually promote via: mlflow ui --port 5001")


# ──────────────────────────────────────────────────────────────────────────────
# STANDALONE RUN
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    fc = DemandForecaster()
    fc.run("Data_Processing/features.csv")