from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.common.config import Settings, get_settings
from libs.eventbus.bus import EventBus, make_event_bus
from libs.geo.driver_index import DriverIndexRepository
from services.location.repository import PgLocationHistoryRepository
from services.location.service import LocationService

_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_redis: Redis | None = None
_event_bus: EventBus | None = None


async def configure(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    global _sessionmaker, _redis, _event_bus
    _sessionmaker = sessionmaker
    _redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    _event_bus = make_event_bus(settings)
    await _event_bus.start()


async def shutdown() -> None:
    if _event_bus:
        await _event_bus.stop()
    if _redis:
        await _redis.aclose()


async def get_session() -> AsyncIterator[AsyncSession]:
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session


def get_location_service() -> LocationService:
    assert _redis is not None and _event_bus is not None
    return LocationService(DriverIndexRepository(_redis), _event_bus)


async def get_history_repo(session: AsyncSession = Depends(get_session)) -> PgLocationHistoryRepository:
    return PgLocationHistoryRepository(session)
