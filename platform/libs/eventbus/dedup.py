"""Consumer-side idempotency (PLAN §3.3, mechanism 2): every event carries a unique
`event_id`; a consumer must dedupe on it via a short-TTL Redis set *before* applying
any effect, so at-least-once delivery becomes a safe no-op on redelivery.
"""
from __future__ import annotations

from redis.asyncio import Redis

_DEDUP_TTL_SECONDS = 7 * 24 * 3600  # >= the shortest topic retention in the catalog


class RedisDedup:
    def __init__(self, redis: Redis, namespace: str) -> None:
        self._redis = redis
        self._namespace = namespace

    async def seen_before(self, event_id: str) -> bool:
        """Atomically marks event_id as seen and returns whether it was ALREADY seen
        (i.e. a redelivery). Uses SET NX so the check-and-mark is a single round trip,
        never a race between two consumer instances in the same group."""
        key = f"dedup:{self._namespace}:{event_id}"
        was_new = await self._redis.set(key, "1", nx=True, ex=_DEDUP_TTL_SECONDS)
        return not was_new
