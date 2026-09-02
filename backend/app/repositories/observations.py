"""Repositories for `demand_observations` (feature-store source) and
`forecasts` (persisted predictions, closing DATA-02).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DemandObservation, Forecast


def _as_utc(value: datetime) -> datetime:
    """Normalize a naive datetime (the ML layer's convention) to UTC-aware.

    asyncpg requires tz-aware Python datetimes for a `TIMESTAMP WITH TIME
    ZONE` bind parameter; the ML layer works in naive timestamps throughout
    (training data has no timezone). This is the one place that boundary is
    crossed for writes, so every caller in this module can pass either.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class DemandObservationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_upsert(self, rows: list[dict]) -> int:
        """Idempotent bulk load, used by the seed script and any future
        ingestion job. `ON CONFLICT DO NOTHING` on the (city, zone, window)
        unique key means re-running ingestion for an overlapping range is
        always safe.
        """
        if not rows:
            return 0
        stmt = pg_insert(DemandObservation).values(rows)
        stmt = stmt.on_conflict_do_nothing(constraint="uq_demand_obs_key")
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount or 0

    async def latest_window(self, city_id: str) -> datetime | None:
        stmt = select(func.max(DemandObservation.window_start)).where(
            DemandObservation.city_id == city_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def count_for_city(self, city_id: str) -> int:
        stmt = select(func.count()).select_from(DemandObservation).where(
            DemandObservation.city_id == city_id
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def history_for_zone(
        self, city_id: str, zone_index: int, *, limit: int = 200
    ) -> list[DemandObservation]:
        stmt = (
            select(DemandObservation)
            .where(DemandObservation.city_id == city_id, DemandObservation.zone_index == zone_index)
            .order_by(DemandObservation.window_start.desc())
            .limit(limit)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        rows.reverse()
        return rows


class ForecastRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        tenant_id: str,
        city_id: str,
        zone_index: int,
        window_start: datetime,
        predicted_demand: float,
        model_version: str,
        degraded: bool,
        requested_by: str | None,
    ) -> Forecast:
        row = Forecast(
            tenant_id=tenant_id,
            city_id=city_id,
            zone_index=zone_index,
            window_start=_as_utc(window_start),
            predicted_demand=predicted_demand,
            model_version=model_version,
            degraded=degraded,
            requested_by=requested_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def record_many(self, rows: list[dict]) -> None:
        if not rows:
            return
        normalized = [{**r, "window_start": _as_utc(r["window_start"])} for r in rows]
        self._session.add_all([Forecast(**r) for r in normalized])
        await self._session.flush()

    async def history(
        self, city_id: str, zone_index: int, tenant_id: str, *, limit: int = 100
    ) -> list[Forecast]:
        stmt = (
            select(Forecast)
            .where(
                Forecast.city_id == city_id,
                Forecast.zone_index == zone_index,
                Forecast.tenant_id == tenant_id,
            )
            .order_by(Forecast.window_start.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
