"""
PostgreSQL-backed implementation of `app.ml.service.FeatureStore` (closes ML-11).

WHAT THIS REPLACES
-------------------
The original Flask endpoint read `demand_lag1`, `demand_lag2`, etc. straight
out of the request body (`data.get('demand_lag1', 0)`). In practice every real
caller sent 0 for all of them, so every prediction was calendar-only regardless
of actual recent demand -- and nothing stopped a caller from sending fabricated
values to manipulate a score.

This queries `demand_observations` -- the same table the training-time feature
builder's lag/rolling logic is semantically equivalent to -- for the real
recent history of a (city, zone) and computes the identical statistics
`app.ml.features.add_autoregressive_features` computes for training, just for
one row instead of a whole series.

WINDOW ALIGNMENT
----------------
`window_start` passed in must already be floored to a 15-minute boundary
(`app.ml.service.align_to_window`); this module assumes that invariant rather
than re-checking it, since the service layer is the single call site.
"""

from __future__ import annotations

from datetime import timedelta, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DemandObservation
from app.ml.contract import LAG_WINDOWS, ROLL_WINDOWS, WINDOW_MINUTES

_MAX_LAG = max(LAG_WINDOWS)
_MAX_ROLL = max(ROLL_WINDOWS)
_LOOKBACK_WINDOWS = max(_MAX_LAG, _MAX_ROLL) + 1


class PostgresFeatureStore:
    """Synchronous-looking Protocol, backed by an already-open async session.

    `app.ml.service.FeatureStore` is a sync Protocol because scoring itself is
    CPU-bound and synchronous; this class front-loads the async DB read via
    `preload()` before prediction, then serves from an in-memory cache during
    the (sync) scoring call. See `ForecastingService` for how the two are
    sequenced.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int], list[tuple[pd.Timestamp, int]]] = {}

    async def preload(self, session: AsyncSession, city_id: str, zone_index: int, window_start: pd.Timestamp) -> None:
        """Fetch the recent observation history needed for one prediction.

        `window_start` is a tz-naive pandas Timestamp (the ML contract's
        convention -- training data has no timezone). The `demand_observations`
        column is `TIMESTAMP WITH TIME ZONE`, and asyncpg's binary protocol
        requires tz-aware Python datetimes for that column type, unlike a
        text-inlined INSERT where Postgres itself does the (UTC-assuming)
        coercion. So: treat naive as UTC-wall-clock at the query boundary, and
        strip tzinfo again once rows come back, keeping every naive Timestamp
        upstream of this module UTC-equivalent and internally consistent.
        """
        window_start_utc = window_start.tz_localize(timezone.utc) if window_start.tzinfo is None else window_start
        lower_bound = window_start_utc - timedelta(minutes=WINDOW_MINUTES * (_LOOKBACK_WINDOWS + 2))
        stmt = (
            select(DemandObservation.window_start, DemandObservation.demand)
            .where(
                DemandObservation.city_id == city_id,
                DemandObservation.zone_index == zone_index,
                DemandObservation.window_start < window_start_utc,
                DemandObservation.window_start >= lower_bound,
            )
            .order_by(DemandObservation.window_start.desc())
        )
        rows = (await session.execute(stmt)).all()
        self._cache[(city_id, zone_index)] = [
            (pd.Timestamp(r[0]).tz_localize(None) if pd.Timestamp(r[0]).tzinfo is None else pd.Timestamp(r[0]).tz_convert(None), r[1])
            for r in rows
        ]

    async def preload_many(
        self, session: AsyncSession, city_id: str, zone_indices: list[int], window_start: pd.Timestamp
    ) -> None:
        for zi in zone_indices:
            await self.preload(session, city_id, zi, window_start)

    def _series_for(self, city_id: str, zone_index: int) -> list[tuple[pd.Timestamp, int]]:
        return self._cache.get((city_id, zone_index), [])

    def autoregressive_features(self, zone_id: int, window_start: pd.Timestamp) -> dict[str, float]:
        """Compute lag/rolling/expanding-mean features from cached history.

        `zone_id` here is matched against whichever `(city_id, zone_index)` key
        was most recently preloaded for it -- callers must preload per city
        before scoring, which `ForecastingService` does.
        """
        # Find the cache entry for this zone_index regardless of city (a given
        # scoring pass is always scoped to one city, so this is unambiguous).
        series = None
        for (_, zi), values in self._cache.items():
            if zi == zone_id:
                series = values
                break
        if not series:
            return {}

        window = pd.Timedelta(minutes=WINDOW_MINUTES)
        by_offset: dict[int, int] = {}
        for ts, demand in series:
            offset = round((window_start - ts) / window)
            if offset > 0:
                by_offset.setdefault(offset, demand)

        features: dict[str, float] = {}
        for k in LAG_WINDOWS:
            if k in by_offset:
                features[f"demand_lag{k}"] = float(by_offset[k])

        for w in ROLL_WINDOWS:
            values = [by_offset[o] for o in range(1, w + 1) if o in by_offset]
            if values:
                features[f"demand_roll{w}"] = float(sum(values) / len(values))

        std3_values = [by_offset[o] for o in range(1, 4) if o in by_offset]
        if len(std3_values) >= 2:
            series_pd = pd.Series(std3_values, dtype="float64")
            features["demand_std3"] = float(series_pd.std())

        all_values = [d for _, d in series]
        if all_values:
            features["zone_mean_demand"] = float(sum(all_values) / len(all_values))

        return features
