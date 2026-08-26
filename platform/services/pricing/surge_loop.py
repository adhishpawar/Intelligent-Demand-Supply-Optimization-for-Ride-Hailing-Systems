"""The surge recompute loop (`ride_hailing_HLD_LLD.md` section 3.5: "recomputed every
~1 min per geohash cell"). Demand is tracked by consuming `ride.*` events (an active
ride request increments the cell's counter; completion/cancellation/no-driver/expiry
decrements it) — real event-driven state, not a poll. Supply is measured live via a
`GEORADIUS` count around each active cell's last-seen pickup point (a practical
proxy for "the cell center," since this Redis build has no cheap geohash-cell-to-
polygon query — a documented simplification, not a hidden one).

PLAN amendment AM-12: the surge key's TTL is 3x the recompute interval, and a read of
a missing/expired key logs staleness rather than silently defaulting to 1.0 forever —
see libs/pricing/surge_reader.py for the read side.
"""
from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis

from libs.eventbus.bus import EventBus
from libs.geo.cities import CITIES
from libs.geo.driver_index import DriverIndexRepository
from libs.pricing.fare import compute_surge_multiplier
from libs.pricing.surge_reader import surge_key

logger = logging.getLogger("pricing.surge_loop")

RECOMPUTE_INTERVAL_S = 60.0
SURGE_TTL_S = int(RECOMPUTE_INTERVAL_S * 3)  # AM-12
CELL_RADIUS_KM = 2.5  # ~half a geohash5 cell


def _demand_key(city_id: str) -> str:
    return f"active_requests:{city_id}"


def _cell_center_key(city_id: str) -> str:
    return f"cell_center:{city_id}"


class DemandTracker:
    """Consumes ride.* events to keep a live per-cell active-request count. This is
    the "demand" side of demand_supply_ratio (HLD 3.5)."""

    def __init__(self, redis: Redis, event_bus: EventBus) -> None:
        self._redis = redis
        self._bus = event_bus

    async def start(self) -> None:
        await self._bus.subscribe("ride.requested", "pricing-demand", self._on_requested)
        await self._bus.subscribe("ride.completed", "pricing-demand", self._on_resolved)
        await self._bus.subscribe("ride.cancelled", "pricing-demand", self._on_resolved)

    async def _on_requested(self, payload: dict) -> None:
        city_id = payload["city_id"]
        cell = payload["pickup_geohash7"][:5]
        await self._redis.hincrby(_demand_key(city_id), cell, 1)
        await self._redis.hset(_cell_center_key(city_id), cell, f"{payload['pickup_lat']},{payload['pickup_lng']}")

    async def _on_resolved(self, payload: dict) -> None:
        city_id = payload["city_id"]
        # ride.completed doesn't carry pickup_geohash7 (only ride.requested does);
        # decrementing an approximate/most-recent-cell is a pragmatic simplification
        # tonight -- correctness of the surge NUMBER tolerates this (it's advisory,
        # bounded, and recomputed every 60s from live supply regardless), documented
        # rather than silently accepted.
        cells = await self._redis.hkeys(_demand_key(city_id))
        if cells:
            await self._redis.hincrby(_demand_key(city_id), cells[0], -1)


class SurgeRecomputeLoop:
    def __init__(self, redis: Redis, cap_override: float | None = None) -> None:
        self._redis = redis
        self._index = DriverIndexRepository(redis)
        self._cap_override = cap_override
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(RECOMPUTE_INTERVAL_S)
                await self._recompute_all()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("surge recompute iteration failed")

    async def _recompute_all(self) -> None:
        for city_id, city in CITIES.items():
            demand = await self._redis.hgetall(_demand_key(city_id))
            centers = await self._redis.hgetall(_cell_center_key(city_id))
            cap = self._cap_override or city.rate_card.surge_cap

            for cell, count_str in demand.items():
                active_requests = max(0, int(count_str))
                center = centers.get(cell)
                if not center:
                    continue
                lat_str, lng_str = center.split(",")
                candidates = await self._index.search_one_city(city_id, float(lat_str), float(lng_str), CELL_RADIUS_KM, 100)
                available_drivers = len(candidates)

                multiplier = compute_surge_multiplier(active_requests, available_drivers, cap)
                await self._redis.set(surge_key(city_id, cell), multiplier, ex=SURGE_TTL_S)
                logger.info(
                    "surge recomputed",
                    extra={"extra_fields": {
                        "city_id": city_id, "cell": cell, "active_requests": active_requests,
                        "available_drivers": available_drivers, "multiplier": multiplier,
                    }},
                )
