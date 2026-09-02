"""
Model artifact registry: load, verify, and describe the served model.

DESIGN POSITION
---------------
The prompt's model-registry section says not to adopt MLflow reflexively. This
codebase already logs training runs to MLflow (see `training.py`), which is the
right tool for *experiment tracking*. But the serving path must not depend on
an MLflow server being reachable -- that would make a 99.9%-availability
advisory service depend on an internal tool's uptime. So serving reads a plain
artifact + sidecar metadata JSON from disk, and MLflow remains the offline
record. The metadata file is the contract between the two.

WHAT THE SIDECAR CARRIES
------------------------
Enough to answer "what exactly is scoring my traffic?" without loading the
model: contract version, feature columns, training metrics, row counts, and the
training timestamp. `/api/v1/models/current` returns it verbatim, which is what
makes the deployed model auditable rather than folklore.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib

from app.ml.contract import CONTRACT_VERSION, FEATURE_COLUMNS, verify_contract

logger = logging.getLogger(__name__)


class ModelNotLoadedError(RuntimeError):
    """Raised when inference is attempted with no model available."""


@dataclass(frozen=True)
class ModelMetadata:
    model_name: str
    model_version: str
    contract_version: str
    feature_columns: list[str]
    trained_at: str
    metrics: dict[str, float] = field(default_factory=dict)
    training_rows: int = 0
    test_rows: int = 0
    algorithm: str = "lightgbm"
    notes: str = ""

    @classmethod
    def from_file(cls, path: Path) -> "ModelMetadata":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            model_name=raw.get("model_name", "demand_forecaster_fr01"),
            model_version=str(raw.get("model_version", "unknown")),
            contract_version=raw.get("contract_version", "unknown"),
            feature_columns=list(raw.get("feature_columns", [])),
            trained_at=raw.get("trained_at", ""),
            metrics={k: float(v) for k, v in (raw.get("metrics") or {}).items()},
            training_rows=int(raw.get("training_rows", 0)),
            test_rows=int(raw.get("test_rows", 0)),
            algorithm=raw.get("algorithm", "lightgbm"),
            notes=raw.get("notes", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "contract_version": self.contract_version,
            "feature_columns": self.feature_columns,
            "trained_at": self.trained_at,
            "metrics": self.metrics,
            "training_rows": self.training_rows,
            "test_rows": self.test_rows,
            "algorithm": self.algorithm,
            "notes": self.notes,
        }


def write_metadata(
    path: Path,
    *,
    model_version: str,
    metrics: dict[str, float],
    training_rows: int,
    test_rows: int,
    model_name: str = "demand_forecaster_fr01",
    algorithm: str = "lightgbm",
    notes: str = "",
) -> ModelMetadata:
    """Write the sidecar that serving will verify against. Called by training."""
    meta = ModelMetadata(
        model_name=model_name,
        model_version=model_version,
        contract_version=CONTRACT_VERSION,
        feature_columns=list(FEATURE_COLUMNS),
        trained_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        metrics={k: round(float(v), 6) for k, v in metrics.items()},
        training_rows=training_rows,
        test_rows=test_rows,
        algorithm=algorithm,
        notes=notes,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")
    return meta


class ModelRegistry:
    """Thread-safe holder for the currently-served model and its metadata.

    Loading is explicit (`load()`) rather than at import, so the app can start,
    report itself un-ready, and be diagnosed -- instead of crashing on import
    because an artifact is missing, which is what the old Flask module did.
    """

    def __init__(self, model_path: Path, metadata_path: Path) -> None:
        self._model_path = model_path
        self._metadata_path = metadata_path
        self._lock = threading.RLock()
        self._model: Any | None = None
        self._metadata: ModelMetadata | None = None
        self._load_error: str | None = None

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._model is not None

    @property
    def load_error(self) -> str | None:
        with self._lock:
            return self._load_error

    @property
    def metadata(self) -> ModelMetadata | None:
        with self._lock:
            return self._metadata

    def load(self) -> bool:
        """Attempt to load and verify the artifact. Returns success.

        Never raises on a missing artifact: an un-loaded model is a documented
        degraded state that readiness reports and inference refuses, not a
        startup crash.
        """
        with self._lock:
            self._load_error = None
            if not self._model_path.exists():
                self._load_error = f"model artifact not found at {self._model_path}"
                logger.warning("model load skipped: %s", self._load_error)
                return False
            if not self._metadata_path.exists():
                self._load_error = (
                    f"model metadata not found at {self._metadata_path}; refusing to "
                    f"serve an artifact whose feature contract cannot be verified"
                )
                logger.error("model load refused: %s", self._load_error)
                return False
            try:
                metadata = ModelMetadata.from_file(self._metadata_path)
                verify_contract(metadata.contract_version, metadata.feature_columns)
                model = joblib.load(self._model_path)
            except Exception as exc:  # noqa: BLE001 - surfaced via readiness
                self._load_error = f"{type(exc).__name__}: {exc}"
                logger.exception("model load failed")
                self._model = None
                self._metadata = None
                return False

            self._model = model
            self._metadata = metadata
            logger.info(
                "model loaded: %s v%s (contract %s, %d features)",
                metadata.model_name,
                metadata.model_version,
                metadata.contract_version,
                len(metadata.feature_columns),
            )
            return True

    def get_model(self) -> Any:
        with self._lock:
            if self._model is None:
                raise ModelNotLoadedError(
                    self._load_error or "no demand model is currently loaded"
                )
            return self._model

    def describe(self) -> dict[str, Any]:
        with self._lock:
            return {
                "loaded": self._model is not None,
                "load_error": self._load_error,
                "artifact_path": str(self._model_path),
                "serving_contract_version": CONTRACT_VERSION,
                "metadata": self._metadata.to_dict() if self._metadata else None,
            }
