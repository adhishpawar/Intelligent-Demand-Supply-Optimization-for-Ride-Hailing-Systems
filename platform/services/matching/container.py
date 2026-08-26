from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.common.config import Settings, get_settings
from libs.geo.cities import CityConfig, get_city
from libs.geo.driver_index import DriverIndexRepository
from services.matching.claim import DriverClaimService
from services.matching.dispatcher import Dispatcher
from services.matching.scoring import WeightedScoreStrategy

_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_redis: Redis | None = None
_dispatchers: dict[str, Dispatcher] = {}  # one scorer per city (city-tunable weights, PLAN §4.1)


async def configure(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    global _sessionmaker, _redis
    _sessionmaker = sessionmaker
    _redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)


async def shutdown() -> None:
    if _redis:
        await _redis.aclose()


async def get_session() -> AsyncIterator[AsyncSession]:
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session


def get_redis() -> Redis:
    assert _redis is not None
    return _redis


def get_dispatcher_for_city(city_id: str, settings: Settings) -> Dispatcher:
    assert _redis is not None
    if city_id not in _dispatchers:
        city: CityConfig = get_city(city_id)
        index = DriverIndexRepository(_redis)
        claims = DriverClaimService(_redis)
        scorer = WeightedScoreStrategy(city)
        _dispatchers[city_id] = Dispatcher(index, claims, scorer)
    return _dispatchers[city_id]


def get_dispatcher_dep(settings: Settings = Depends(get_settings)):
    def _resolve(city_id: str) -> Dispatcher:
        return get_dispatcher_for_city(city_id, settings)

    return _resolve
