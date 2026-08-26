from __future__ import annotations

from redis.asyncio import Redis

from libs.common.config import Settings

_redis: Redis | None = None


def configure(settings: Settings) -> Redis:
    global _redis
    _redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    return _redis


async def shutdown() -> None:
    if _redis:
        await _redis.aclose()


def get_redis() -> Redis:
    assert _redis is not None
    return _redis
