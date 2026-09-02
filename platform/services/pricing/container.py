from __future__ import annotations

from typing import AsyncIterator

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.common.config import Settings
from libs.persistence.engine import make_engine, make_sessionmaker

_redis: Redis | None = None
_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def configure(settings: Settings) -> Redis:
    global _redis, _engine, _sessionmaker
    _redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    _engine = make_engine(settings)
    _sessionmaker = make_sessionmaker(_engine)
    return _redis


async def shutdown() -> None:
    if _redis:
        await _redis.aclose()
    if _engine:
        await _engine.dispose()


def get_redis() -> Redis:
    assert _redis is not None
    return _redis


async def get_session() -> AsyncIterator[AsyncSession]:
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session
