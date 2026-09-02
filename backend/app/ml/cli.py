"""
Training CLI for the FR-01 demand model.

    cd backend
    python -m app.ml.cli train --test-days 5
    python -m app.ml.cli train --sample-rows 2000000   # fast iteration
    python -m app.ml.cli inspect                       # what is on disk now

Kept separate from the library code so the training path is importable (and
testable) without argparse, and runnable without importing FastAPI.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from app.core.config import get_settings
from app.ml.registry import ModelRegistry
from app.ml.training import build_features, load_trips, train

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fr01")


def _cmd_train(args: argparse.Namespace) -> int:
    settings = get_settings()
    trips_csv = Path(args.trips_csv) if args.trips_csv else settings.data_dir / "trips_cleaned.csv"

    trips = load_trips(trips_csv)
    if args.sample_rows and args.sample_rows < len(trips):
        # Take the most RECENT rows, not a random sample: a random sample would
        # punch holes in every zone's time series and make the lag features
        # meaningless. A contiguous recent tail is a valid shorter history.
        trips = trips.sort_values("request_time").tail(args.sample_rows).reset_index(drop=True)
        logger.info("sampled most recent %s trip rows", f"{len(trips):,}")

    features = build_features(trips)
    if features.empty:
        logger.error("feature matrix is empty -- nothing to train on")
        return 1

    if args.features_out:
        out = Path(args.features_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        features.to_csv(out, index=False)
        logger.info("wrote feature matrix -> %s", out)

    result = train(
        features,
        model_path=settings.demand_model_path,
        metadata_path=settings.demand_model_metadata_path,
        test_days=args.test_days,
        mlflow_uri=args.mlflow_uri,
    )

    print(json.dumps({
        "model_version": result.model_version,
        "metrics": {k: round(v, 4) for k, v in result.metrics.items()},
        "baselines": {k: round(v, 4) for k, v in result.baselines.items()},
        "training_rows": result.training_rows,
        "test_rows": result.test_rows,
        "beats_baseline": result.beats_baseline,
        "passed_mae_gate": result.passed_gate,
        "model_path": str(result.model_path),
    }, indent=2))

    if not result.beats_baseline:
        logger.error(
            "model does NOT beat the naive baseline -- refusing to report success. "
            "Investigate features/split before deploying this artifact."
        )
        return 2
    return 0


def _cmd_inspect(_args: argparse.Namespace) -> int:
    settings = get_settings()
    registry = ModelRegistry(settings.demand_model_path, settings.demand_model_metadata_path)
    registry.load()
    print(json.dumps(registry.describe(), indent=2))
    return 0 if registry.is_loaded else 1


def _cmd_predict(args: argparse.Namespace) -> int:
    """One-off prediction, to prove the serving path end-to-end from a shell."""
    from app.ml.service import DemandForecastService

    settings = get_settings()
    registry = ModelRegistry(settings.demand_model_path, settings.demand_model_metadata_path)
    if not registry.load():
        logger.error("cannot predict: %s", registry.load_error)
        return 1
    service = DemandForecastService(registry, settings)
    result = service.predict_one(
        zone_id=args.zone_id,
        window_start=pd.Timestamp(args.window_start),
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fr01", description="FR-01 demand model tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    p_train = sub.add_parser("train", help="build features and train the demand model")
    p_train.add_argument("--trips-csv", default=None, help="override cleaned trips CSV path")
    p_train.add_argument("--test-days", type=int, default=5, help="chronological holdout, in days")
    p_train.add_argument("--sample-rows", type=int, default=None, help="use only the N most recent trips")
    p_train.add_argument("--features-out", default=None, help="also write the feature matrix here")
    p_train.add_argument("--mlflow-uri", default=None, help="MLflow tracking URI (best effort)")
    p_train.set_defaults(func=_cmd_train)

    p_inspect = sub.add_parser("inspect", help="show the currently-deployable artifact")
    p_inspect.set_defaults(func=_cmd_inspect)

    p_predict = sub.add_parser("predict", help="score a single (zone, window)")
    p_predict.add_argument("--zone-id", type=int, required=True)
    p_predict.add_argument("--window-start", required=True, help="e.g. 2015-01-30T18:00:00")
    p_predict.set_defaults(func=_cmd_predict)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
