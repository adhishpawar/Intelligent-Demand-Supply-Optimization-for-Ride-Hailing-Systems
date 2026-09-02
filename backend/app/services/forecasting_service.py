"""
Forecasting application service.

Composes: ModelRegistry (what's loaded) + PostgresFeatureStore (real recent
history) + DemandForecastService (scoring) + ForecastRepository (persistence,
DATA-02) + a Redis cache respecting the NFR "staleness must be visible/expire,
never silently wrong" -- a cache hit past its TTL is never returned; it simply
misses and recomputes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import pandas as pd
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ModelUnavailableError, NotFoundError
from app.ml.feature_store import PostgresFeatureStore
from app.ml.registry import ModelNotLoadedError, ModelRegistry
from app.ml.service import DemandForecastService, align_to_window
from app.repositories.cities import ZoneRepository
from app.repositories.observations import ForecastRepository

logger = logging.getLogger(__name__)


class ForecastingService:
    def __init__(
        self,
        session: AsyncSession,
        registry: ModelRegistry,
        settings: Settings,
        redis_client: aioredis.Redis | None = None,
    ) -> None:
        self._session = session
        self._registry = registry
        self._settings = settings
        self._redis = redis_client
        self._zones = ZoneRepository(session)
        self._forecasts = ForecastRepository(session)

    def _cache_key(self, city_id: str, zone_index: int, window_start: pd.Timestamp) -> str:
        return f"forecast:{city_id}:{zone_index}:{window_start.isoformat()}"

    async def _cached(self, key: str) -> dict | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(key)
        except Exception as exc:  # noqa: BLE001 - cache is an optimization, not a dependency
            logger.warning("forecast cache read failed: %s", exc)
            return None
        return json.loads(raw) if raw else None

    async def _store_cache(self, key: str, value: dict) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(
                key, json.dumps(value, default=str), ex=self._settings.forecast_cache_ttl_seconds
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("forecast cache write failed: %s", exc)

    async def _verify_zone(self, city_id: str, tenant_id: str, zone_index: int) -> None:
        zone = await self._zones.get_by_index(city_id, tenant_id, zone_index)
        if zone is None:
            raise NotFoundError(f"zone_id {zone_index} not found in this city")

    async def predict(
        self,
        *,
        tenant_id: str,
        city_id: str,
        zone_index: int,
        window_start: datetime | None,
        requested_by: str | None,
        persist: bool = True,
    ) -> dict:
        await self._verify_zone(city_id, tenant_id, zone_index)
        window = align_to_window(window_start) if window_start else _default_next_window()

        cache_key = self._cache_key(city_id, zone_index, window)
        cached = await self._cached(cache_key)
        if cached is not None:
            return cached

        store = PostgresFeatureStore()
        await store.preload(self._session, city_id, zone_index, window)

        try:
            service = DemandForecastService(self._registry, self._settings, feature_store=store)
            result = service.predict_one(zone_id=zone_index, window_start=window)
        except ModelNotLoadedError as exc:
            raise ModelUnavailableError(str(exc)) from exc

        if persist:
            await self._forecasts.record(
                tenant_id=tenant_id,
                city_id=city_id,
                zone_index=zone_index,
                window_start=result["window_start"],
                predicted_demand=result["predicted_demand"],
                model_version=result["model_version"],
                degraded=result["degraded"],
                requested_by=requested_by,
            )
            await self._session.commit()

        await self._store_cache(cache_key, result)
        return result

    async def predict_horizon(
        self,
        *,
        tenant_id: str,
        city_id: str,
        zone_index: int,
        windows: int,
        requested_by: str | None,
    ) -> list[dict]:
        await self._verify_zone(city_id, tenant_id, zone_index)
        start = _default_next_window()
        capped = max(1, min(windows, self._settings.max_forecast_horizon_windows))

        store = PostgresFeatureStore()
        await store.preload(self._session, city_id, zone_index, start)

        service = DemandForecastService(self._registry, self._settings, feature_store=store)
        try:
            results = service.predict_horizon(zone_id=zone_index, start=start, windows=capped)
        except ModelNotLoadedError as exc:
            raise ModelUnavailableError(str(exc)) from exc

        await self._forecasts.record_many(
            [
                {
                    "tenant_id": tenant_id,
                    "city_id": city_id,
                    "zone_index": zone_index,
                    "window_start": r["window_start"],
                    "predicted_demand": r["predicted_demand"],
                    "model_version": r["model_version"],
                    "degraded": r["degraded"],
                    "requested_by": requested_by,
                }
                for r in results
            ]
        )
        await self._session.commit()
        return results

    async def predict_city(
        self, *, tenant_id: str, city_id: str, zone_ids: list[int], requested_by: str | None
    ) -> list[dict]:
        window = _default_next_window()
        store = PostgresFeatureStore()
        await store.preload_many(self._session, city_id, zone_ids, window)

        service = DemandForecastService(self._registry, self._settings, feature_store=store)
        try:
            results = service.predict_many(zone_ids=zone_ids, window_start=window)
        except ModelNotLoadedError as exc:
            raise ModelUnavailableError(str(exc)) from exc

        await self._forecasts.record_many(
            [
                {
                    "tenant_id": tenant_id,
                    "city_id": city_id,
                    "zone_index": r["zone_id"],
                    "window_start": r["window_start"],
                    "predicted_demand": r["predicted_demand"],
                    "model_version": r["model_version"],
                    "degraded": r["degraded"],
                    "requested_by": requested_by,
                }
                for r in results
            ]
        )
        await self._session.commit()
        return results

    async def history(self, *, tenant_id: str, city_id: str, zone_index: int, limit: int) -> list:
        await self._verify_zone(city_id, tenant_id, zone_index)
        return await self._forecasts.history(city_id, zone_index, tenant_id, limit=limit)

    def health(self) -> dict:
        return self._registry.describe()


def _default_next_window() -> pd.Timestamp:
    from app.ml.service import default_window

    return default_window()
