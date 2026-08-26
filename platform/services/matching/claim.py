"""The three-layer driver-assignment claim (PLAN §6.1 finding A2 — the single most
important fix in the whole review: the HLD's `SETNX ride_lock:<tripId>` locks the
*uncontended* resource. Two riders near each other who both rank the same driver
first each hold their own uncontended lock on their own trip id and can both dispatch
to that driver simultaneously — the lock has to be on the *driver*, the contended
resource.

Layer 1 (this module): `SET NX` on `driver_claim:<driverId>` with the trip id as the
value and a TTL. Whichever dispatcher wins this claim is the only one allowed to
offer that driver for the next `claim_ttl_seconds`.
Layer 2 (services/trip/repository.py `accept_offer`): `UPDATE trips SET ... WHERE
trip_id=:id AND status='MATCHING' AND version=:v` — zero rows updated means someone
else's transition landed first; the accept returns 409.
Layer 3 (infra/sql/V001__core.sql `ux_trips_one_active_per_driver`): a partial unique
index in Postgres. Even if Redis and the optimistic version check both somehow fail,
the database physically refuses to give one driver two active trips.

PLAN amendment AM-03: `claim_ttl_seconds` MUST be greater than `offer_ttl_seconds` (25s
vs 15s by default) — otherwise the claim could expire and a second dispatcher could
claim the same driver for a different trip WHILE the first offer is still legally
outstanding, then have both an accept and a timeout race on the same driver at once.
"""
from __future__ import annotations

from redis.asyncio import Redis

_CLAIM_KEY_PREFIX = "driver_claim:"


class DriverClaimError(Exception):
    """Raised when the caller tries to release/renew a claim it doesn't hold."""


class DriverClaimService:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def try_claim(self, driver_id: str, trip_id: str, ttl_seconds: int) -> bool:
        """Atomic compare-and-claim. Returns True iff this call won the claim.

        Round 12 stakeholder council: `ttl_seconds` moved from a constructor-time
        value to a per-call one -- `claim_ttl_seconds` is now DB-backed and
        per-city (AS-05), but `get_dispatcher_for_city` (services/matching/
        container.py) caches one `Dispatcher`/`DriverClaimService` per city
        forever. A constructor-baked TTL would have been read once at first
        dispatch and never again, silently defeating the whole "admin changes it,
        takes effect immediately" promise this DB-config pattern exists for. A
        `Redis SET ... EX` is inherently per-call anyway -- this only makes the
        class honest about that."""
        key = f"{_CLAIM_KEY_PREFIX}{driver_id}"
        return bool(await self._redis.set(key, trip_id, nx=True, ex=ttl_seconds))

    async def release(self, driver_id: str, trip_id: str) -> None:
        """Releases the claim ONLY if it's still held for this exact trip (a Lua
        compare-and-delete, so a dispatcher can never accidentally release a claim a
        newer dispatch cycle for a DIFFERENT trip now legitimately holds on the same
        driver after this one's TTL already lapsed)."""
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('DEL', KEYS[1])
        end
        return 0
        """
        await self._redis.eval(script, 1, f"{_CLAIM_KEY_PREFIX}{driver_id}", trip_id)

    async def is_claimed(self, driver_id: str) -> bool:
        return await self._redis.exists(f"{_CLAIM_KEY_PREFIX}{driver_id}") == 1
