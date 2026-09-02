"""Reads the live surge value the Pricing service's recompute loop writes (PLAN
amendment AM-12: TTL = 3x the recompute interval, so an absent/expired key is treated
as staleness to log, not silently as "no surge" — see services/pricing/surge_loop.py
for the writer side).
"""
from __future__ import annotations

import logging

from redis.asyncio import Redis

logger = logging.getLogger("pricing.surge_reader")


def surge_key(city_id: str, geohash7: str) -> str:
    return f"surge:{city_id}:{geohash7[:5]}"  # geohash5 cell (~5km) — HLD 3.4 keys by geohash cell


async def read_surge_multiplier(redis: Redis, city_id: str, geohash7: str, *, default: float = 1.0) -> float:
    key = surge_key(city_id, geohash7)
    value = await redis.get(key)
    if value is None:
        logger.info("surge key absent/expired — using default", extra={"extra_fields": {"key": key}})
        return default
    return float(value)
