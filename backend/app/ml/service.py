"""
Demand forecasting service -- the ML side of the application layer.

This sits between the HTTP controllers and the model. Controllers do not touch
`registry`, do not build DataFrames, and do not know LightGBM exists; they call
methods here. That separation is what lets the same forecasting logic be reused
by the background workers (fleet rebalancing, hotspot detection) without going
through HTTP, which the prompt's "separate ML from HTTP concerns" section asks
for and which the original Flask module violated by doing feature assembly
inline in the route handler.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import numpy as np
import pandas as pd

from app.core.config import Settings
from app.ml.contract import WINDOW_MINUTES
from app.ml.features import assemble_row
from app.ml.registry import ModelRegistry

logger = logging.getLogger(__name__)


class FeatureStore(Protocol):
    """Source of autoregressive features for a zone at serving time.

    Deliberately an interface: today it is backed by recent demand rows in
    Postgres, but the design doc's target is a streaming feature store. Callers
    depend on this protocol, not on the implementation.
    """

    def autoregressive_features(self, zone_id: int, window_start: pd.Timestamp) -> dict[str, float]:
        ...


class NullFeatureStore:
    """Degraded-mode store: returns nothing, so neutral defaults apply.

    Used when no feature backend is configured. Predictions are then driven by
    calendar features alone, which is materially worse but still directionally
    useful -- and, critically, still honest, because the response carries
    `degraded: true` so the caller can decide whether to use it.
    """

    def autoregressive_features(self, zone_id: int, window_start: pd.Timestamp) -> dict[str, float]:
        return {}


@dataclass(frozen=True)
class ForecastPoint:
    zone_id: int
    window_start: datetime
    predicted_demand: float
    degraded: bool
    model_version: str


class DemandForecastService:
    def __init__(
        self,
        registry: ModelRegistry,
        settings: Settings,
        feature_store: FeatureStore | None = None,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._store: FeatureStore = feature_store or NullFeatureStore()

    # -- internals ---------------------------------------------------------
    def _model_version(self) -> str:
        meta = self._registry.metadata
        return meta.model_version if meta else "unloaded"

    def _score(self, frame: pd.DataFrame) -> np.ndarray:
        model = self._registry.get_model()
        started = time.perf_counter()
        raw = model.predict(frame)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms > self._settings.inference_timeout_ms:
            # Log rather than abort: the row is already scored, and throwing it
            # away would turn a slow prediction into no prediction. The metric
            # is what drives the SLA conversation.
            logger.warning(
                "inference exceeded budget: %.1fms > %dms (rows=%d)",
                elapsed_ms,
                self._settings.inference_timeout_ms,
                len(frame),
            )
        # Demand is a non-negative count. Clamping matches training-time
        # evaluation, so reported metrics describe what serving actually does.
        return np.maximum(0.0, raw)

    # -- public API --------------------------------------------------------
    def predict_one(
        self,
        zone_id: int,
        window_start: pd.Timestamp,
        external: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        autoregressive = self._store.autoregressive_features(zone_id, window_start)
        degraded = not autoregressive
        row = assemble_row(
            zone_id=zone_id,
            window_start=window_start,
            autoregressive=autoregressive,
            external=external,
        )
        value = float(self._score(row)[0])
        return {
            "zone_id": int(zone_id),
            "window_start": window_start.to_pydatetime(),
            "predicted_demand": round(value, 2),
            "unit": f"ride requests per {WINDOW_MINUTES} minutes",
            "degraded": degraded,
            "model_version": self._model_version(),
        }

    def predict_horizon(
        self,
        zone_id: int,
        start: pd.Timestamp,
        windows: int,
        external: dict[str, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Forecast consecutive windows ahead for one zone (FR-01's horizon)."""
        capped = max(1, min(int(windows), self._settings.max_forecast_horizon_windows))
        return [
            self.predict_one(
                zone_id=zone_id,
                window_start=start + pd.Timedelta(minutes=WINDOW_MINUTES * i),
                external=external,
            )
            for i in range(capped)
        ]

    def predict_many(
        self,
        zone_ids: list[int],
        window_start: pd.Timestamp,
        external: dict[str, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Score many zones for one window in a single model call.

        The original `/predict/batch` looped per zone and, separately, built its
        DataFrame as `pd.DataFrame([[zone_data]])` -- a 1x1 frame holding a dict
        object, which cannot score at all. This builds one correctly-shaped
        frame and calls the model once, which is also what makes a
        whole-city refresh affordable.
        """
        if not zone_ids:
            return []
        capped = zone_ids[: self._settings.max_batch_zones]

        rows: list[pd.DataFrame] = []
        degraded_flags: list[bool] = []
        for zid in capped:
            autoregressive = self._store.autoregressive_features(zid, window_start)
            degraded_flags.append(not autoregressive)
            rows.append(
                assemble_row(
                    zone_id=zid,
                    window_start=window_start,
                    autoregressive=autoregressive,
                    external=external,
                )
            )

        frame = pd.concat(rows, ignore_index=True)
        values = self._score(frame)
        version = self._model_version()
        return [
            {
                "zone_id": int(zid),
                "window_start": window_start.to_pydatetime(),
                "predicted_demand": round(float(val), 2),
                "unit": f"ride requests per {WINDOW_MINUTES} minutes",
                "degraded": flag,
                "model_version": version,
            }
            for zid, val, flag in zip(capped, values, degraded_flags)
        ]

    def health(self) -> dict[str, Any]:
        return self._registry.describe()


def align_to_window(ts: datetime) -> pd.Timestamp:
    """Floor a timestamp to the start of its 15-minute aggregation window.

    Serving must score the same window boundaries training aggregated on;
    predicting for 18:07 is meaningless when the model was fit on 18:00/18:15.
    """
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return stamp.floor(f"{WINDOW_MINUTES}min")


def default_window(now: datetime | None = None) -> pd.Timestamp:
    base = now or datetime.now(timezone.utc)
    return align_to_window(base + timedelta(minutes=WINDOW_MINUTES))
