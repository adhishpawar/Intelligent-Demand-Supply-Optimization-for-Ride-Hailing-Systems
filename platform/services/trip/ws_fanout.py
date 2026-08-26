"""WebSocket fan-out via Redis pub/sub (PLAN amendment AM-07): even with a single
service instance tonight, publishing goes through Redis pub/sub rather than an
in-process list of open sockets, so horizontal scaling later is a zero-line change —
any instance can publish, any instance's WS handler (subscribed to the same channel)
delivers it. Costs ~15 lines tonight; costs a rewrite later if skipped.
"""
from __future__ import annotations

import json

from redis.asyncio import Redis


def trip_channel(trip_id: str) -> str:
    return f"trip:{trip_id}:stream"


def driver_channel(driver_id: str) -> str:
    return f"driver:{driver_id}:stream"


async def publish_trip_update(redis: Redis, trip_id: str, data: dict) -> None:
    await redis.publish(trip_channel(trip_id), json.dumps(data, default=str))


async def publish_driver_offer(redis: Redis, driver_id: str, data: dict) -> None:
    payload = {"type": "OFFER", **data}
    await redis.publish(driver_channel(driver_id), json.dumps(payload, default=str))
