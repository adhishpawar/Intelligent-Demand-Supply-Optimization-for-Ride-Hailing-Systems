from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.common.config import Settings, get_settings
from libs.persistence.unit_of_work import UnitOfWork
from services.trip.matching_client import MatchingClient
from services.trip.service import TripService

_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_redis: Redis | None = None
_trip_service: TripService | None = None


def configure(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    global _sessionmaker, _redis, _trip_service
    _sessionmaker = sessionmaker
    _redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    matching_client = MatchingClient(f"http://localhost:{settings.matching_port}", settings.internal_assertion_secret)
    _trip_service = TripService(lambda: UnitOfWork(sessionmaker), _redis, matching_client, settings)


async def shutdown() -> None:
    if _redis:
        await _redis.aclose()


def get_trip_service() -> TripService:
    assert _trip_service is not None, "container.configure() not called"
    return _trip_service


def get_redis() -> Redis:
    assert _redis is not None
    return _redis


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    assert _sessionmaker is not None
    return _sessionmaker
