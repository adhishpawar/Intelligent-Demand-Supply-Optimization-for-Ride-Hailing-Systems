"""
FR-01 demand model training.

RELATIONSHIP TO THE ORIGINAL CODE
---------------------------------
`ML_Development/DemandForecaster.py` remains the research/notebook-facing
implementation. This module is the productionised training path, and differs in
four ways that matter operationally:

1. It builds features through `app.ml.features`, the same module serving uses,
   so training/serving skew is structurally impossible rather than merely
   discouraged.
2. It reads the trips CSV in chunks, projecting to the three columns the
   aggregation actually needs. The original loaded a 2.3 GB file entirely into
   memory to count rows per window -- fine on a workstation, an OOM on a
   container with a memory limit.
3. MLflow logging is best-effort and wrapped. The original called
   `mlflow.start_run()` unguarded, so an unreachable tracking server aborted a
   completed training run and threw the model away.
4. It writes the metadata sidecar that serving verifies. Without it, an artifact
   is unservable by design (see `registry.ModelRegistry.load`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd

from app.ml.contract import CONTRACT_VERSION, FEATURE_COLUMNS, TARGET_COLUMN
from app.ml.features import (
    add_autoregressive_features,
    add_calendar_features,
    add_external_features,
    aggregate_demand,
    assemble_frame,
    time_based_split,
)
from app.ml.registry import write_metadata

logger = logging.getLogger(__name__)

# Columns genuinely required to aggregate demand. Everything else in
# trips_cleaned.csv is irrelevant to FR-01 and costs memory to load.
_TRIP_COLUMNS = ["request_time", "zone_id", "trip_id"]
_CHUNK_ROWS = 1_000_000

DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 1000,
    "learning_rate": 0.05,
    "max_depth": 7,
    "num_leaves": 63,
    "subsample": 0.80,
    "colsample_bytree": 0.80,
    "min_child_samples": 20,
    "reg_alpha": 0.1,
    "reg_lambda": 0.1,
    "random_state": 42,
    "n_jobs": -1,
    "verbose": -1,
}

MAE_GATE = 4.0


@dataclass
class TrainingResult:
    model_version: str
    metrics: dict[str, float]
    baselines: dict[str, float]
    training_rows: int
    test_rows: int
    beats_baseline: bool
    passed_gate: bool
    model_path: Path
    metadata_path: Path


def load_trips(csv_path: Path, chunk_rows: int = _CHUNK_ROWS) -> pd.DataFrame:
    """Stream the cleaned trips CSV, keeping only the aggregation columns."""
    if not csv_path.exists():
        raise FileNotFoundError(f"cleaned trips file not found: {csv_path}")

    chunks: list[pd.DataFrame] = []
    total = 0
    reader: Iterable[pd.DataFrame] = pd.read_csv(
        csv_path,
        usecols=_TRIP_COLUMNS,
        parse_dates=["request_time"],
        chunksize=chunk_rows,
    )
    for chunk in reader:
        total += len(chunk)
        chunks.append(chunk)
    df = pd.concat(chunks, ignore_index=True)
    logger.info("loaded %s trip rows from %s", f"{total:,}", csv_path.name)
    return df


def build_features(
    trips: pd.DataFrame,
    weather: pd.DataFrame | None = None,
    events: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """trips -> (zone, window) feature matrix with the target attached."""
    demand = aggregate_demand(trips)
    logger.info(
        "aggregated to %s (zone, window) rows across %d zones",
        f"{len(demand):,}",
        demand["zone_id"].nunique(),
    )
    demand = add_calendar_features(demand)
    demand = add_autoregressive_features(demand)
    demand = add_external_features(demand, weather=weather, events=events)

    before = len(demand)
    demand = demand.dropna(subset=list(FEATURE_COLUMNS)).reset_index(drop=True)
    logger.info(
        "dropped %s rows with incomplete lag history (%s remain)",
        f"{before - len(demand):,}",
        f"{len(demand):,}",
    )
    return demand


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    err = actual - predicted
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err**2)))
    # +1 smoothing: a zero-demand window is common and would divide by zero.
    mape = float(np.mean(np.abs(err) / (actual + 1.0)) * 100.0)
    return {"mae": mae, "rmse": rmse, "mape": mape}


def _baselines(test_df: pd.DataFrame) -> dict[str, float]:
    """Naive predictors the model must beat to be worth deploying.

    If the model cannot beat "demand 15 minutes ago", the features or the split
    are wrong and shipping it would be worse than shipping nothing.
    """
    actual = test_df[TARGET_COLUMN].to_numpy(dtype=float)
    lag1 = test_df["demand_lag1"].fillna(0).to_numpy(dtype=float)
    zone_hour_mean = (
        test_df.groupby(["zone_id", "hour"])[TARGET_COLUMN].transform("mean").to_numpy(dtype=float)
    )
    return {
        "naive_lag1_mae": float(np.mean(np.abs(actual - lag1))),
        "zone_hour_mean_mae": float(np.mean(np.abs(actual - zone_hour_mean))),
    }


def _log_to_mlflow(params: dict[str, Any], metrics: dict[str, float], extra: dict[str, Any]) -> None:
    """Best-effort experiment tracking. Never fails the training run."""
    try:
        import mlflow

        mlflow.set_tracking_uri(str(extra.get("mlflow_uri") or "./mlruns"))
        mlflow.set_experiment("FR01_demand_forecasting")
        with mlflow.start_run():
            mlflow.log_params(params)
            mlflow.log_params({k: v for k, v in extra.items() if k != "mlflow_uri"})
            mlflow.log_metrics(metrics)
    except Exception as exc:  # noqa: BLE001 - tracking is not on the critical path
        logger.warning("MLflow logging skipped (%s: %s)", type(exc).__name__, exc)


def train(
    features: pd.DataFrame,
    model_path: Path,
    metadata_path: Path,
    *,
    test_days: int = 5,
    params: dict[str, Any] | None = None,
    mlflow_uri: str | None = None,
) -> TrainingResult:
    """Train, evaluate against baselines, and persist artifact + metadata."""
    import lightgbm as lgb

    resolved = {**DEFAULT_PARAMS, **(params or {})}
    train_df, test_df = time_based_split(features, test_days=test_days)
    logger.info("split: train=%s test=%s rows", f"{len(train_df):,}", f"{len(test_df):,}")

    x_train, y_train = assemble_frame(train_df), train_df[TARGET_COLUMN]
    x_test, y_test = assemble_frame(test_df), test_df[TARGET_COLUMN]

    model = lgb.LGBMRegressor(**resolved)
    model.fit(
        x_train,
        y_train,
        eval_set=[(x_test, y_test)],
        eval_metric="mae",
        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)],
    )

    # Demand cannot be negative; clamping is part of the prediction contract and
    # is applied identically in serving (see app.ml.service).
    preds = np.maximum(0.0, model.predict(x_test))
    metrics = _metrics(y_test.to_numpy(dtype=float), preds)
    baselines = _baselines(test_df)
    metrics["best_iteration"] = float(model.best_iteration_ or resolved["n_estimators"])

    beats_baseline = metrics["mae"] < min(baselines.values())
    passed_gate = metrics["mae"] < MAE_GATE

    model_version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    write_metadata(
        metadata_path,
        model_version=model_version,
        metrics={**metrics, **baselines},
        training_rows=len(train_df),
        test_rows=len(test_df),
        notes=(
            f"contract={CONTRACT_VERSION}; beats_baseline={beats_baseline}; "
            f"mae_gate={MAE_GATE}; passed_gate={passed_gate}"
        ),
    )

    _log_to_mlflow(
        resolved,
        {**metrics, **baselines},
        {
            "mlflow_uri": mlflow_uri,
            "contract_version": CONTRACT_VERSION,
            "n_features": len(FEATURE_COLUMNS),
            "train_rows": len(train_df),
            "test_rows": len(test_df),
            "test_days": test_days,
        },
    )

    logger.info(
        "trained v%s | MAE=%.3f RMSE=%.3f MAPE=%.1f%% | lag1=%.3f zone-hour=%.3f | "
        "beats_baseline=%s gate=%s",
        model_version,
        metrics["mae"],
        metrics["rmse"],
        metrics["mape"],
        baselines["naive_lag1_mae"],
        baselines["zone_hour_mean_mae"],
        beats_baseline,
        passed_gate,
    )

    return TrainingResult(
        model_version=model_version,
        metrics=metrics,
        baselines=baselines,
        training_rows=len(train_df),
        test_rows=len(test_df),
        beats_baseline=beats_baseline,
        passed_gate=passed_gate,
        model_path=model_path,
        metadata_path=metadata_path,
    )
