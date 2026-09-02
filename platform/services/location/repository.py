"""Postgres side of the location service: the driver's registered city (looked up
once, cached into the Redis hash by the service layer) and the async history sink
(PLAN §4.4 — Postgres implementation of `LocationHistoryRepository` by default,
`CassandraLocationHistoryRepository` written separately against the identical CQL
shape and enabled via `LOCATION_HISTORY_BACKEND=cassandra`).
"""
from __future__ import annotations

from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.ids import utcnow
from libs.geo.geohash import encode as geohash_encode


class LocationHistoryRepository(Protocol):
    async def record(self, driver_id: str, lat: float, lng: float, trip_id: str | None) -> None: ...


class PgLocationHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, driver_id: str, lat: float, lng: float, trip_id: str | None) -> None:
        await self._session.execute(
            text(
                "INSERT INTO location_history (driver_id, ts, lat, lng, geohash7, trip_id) "
                "VALUES (:driver_id, :ts, :lat, :lng, :geohash7, :trip_id)"
            ),
            {
                "driver_id": driver_id,
                "ts": utcnow(),
                "lat": lat,
                "lng": lng,
                "geohash7": geohash_encode(lat, lng),
                "trip_id": trip_id,
            },
        )
        await self._session.commit()


async def get_driver_city(session: AsyncSession, driver_id: str) -> str | None:
    row = (
        await session.execute(
            text("SELECT city_id FROM driver_profiles WHERE driver_id = :id"), {"id": driver_id}
        )
    ).first()
    return row[0] if row else None


async def set_driver_status_row(session: AsyncSession, driver_id: str, status: str) -> None:
    await session.execute(
        text("UPDATE driver_profiles SET status = :status, updated_at = now() WHERE driver_id = :id"),
        {"status": status, "id": driver_id},
    )
    await session.commit()
