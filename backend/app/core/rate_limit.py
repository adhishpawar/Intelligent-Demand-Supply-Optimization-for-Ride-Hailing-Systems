"""
Redis-backed fixed-window rate limiter for inference routes (API-09).

Without this, `/forecasts/*` is free compute for anyone with a valid token --
each call runs a real model inference. A fixed window (INCR + EXPIRE) is not
as smooth as a sliding-window or token-bucket limiter, but it is one round
trip, atomic via Lua, and sufficient for "stop obvious abuse" rather than
precise traffic shaping.

Fails OPEN, not closed: if Redis is unreachable, requests are allowed through
rather than the whole API going down because a rate limiter's backing store
hiccupped. That is a deliberate trade -- availability of the advisory ML
platform over strict limit enforcement, consistent with the design doc's NFR
that no single ML component failure should degrade the product.
"""

from __future__ import annotations

import logging
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status

from app.core.security import Principal, get_principal

logger = logging.getLogger(__name__)

_LUA_INCR_WITH_TTL = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return current
"""


class RateLimiter:
    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client
        self._script = self._redis.register_script(_LUA_INCR_WITH_TTL)

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        try:
            count = await self._script(keys=[key], args=[window_seconds])
        except Exception as exc:  # noqa: BLE001 - fail open, see module docstring
            logger.warning("rate limiter unavailable, failing open: %s", exc)
            return True
        return int(count) <= limit


def rate_limit(*, limit: int, window_seconds: int, scope: str):
    """Dependency factory: per-principal, per-scope rate limit.

    `scope` namespaces the counter (e.g. "forecast:predict") so different
    routes don't share a budget by accident.
    """

    async def _checker(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> None:
        limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
        if limiter is None:
            return
        key = f"ratelimit:{scope}:{principal.user_id}"
        allowed = await limiter.allow(key, limit=limit, window_seconds=window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"rate limit exceeded: {limit} requests per {window_seconds}s for {scope}",
            )

    return _checker
