from __future__ import annotations

from typing import AsyncIterator

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.common.config import Settings

_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_redis: Redis | None = None


def configure(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> Redis:
    global _sessionmaker, _redis
    _sessionmaker = sessionmaker
    _redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    return _redis


async def shutdown() -> None:
    if _redis:
        await _redis.aclose()


async def get_session() -> AsyncIterator[AsyncSession]:
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    assert _sessionmaker is not None
    return _sessionmaker


def get_redis() -> Redis:
    assert _redis is not None
    return _redis
