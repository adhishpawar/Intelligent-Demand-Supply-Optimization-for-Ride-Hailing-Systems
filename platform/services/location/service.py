"""Business logic. Redis (via DriverIndexRepository) is the synchronous, authoritative
"where is this driver right now" path — this is what the HTTP handler awaits before
responding, keeping the <1s propagation SLA. The Kafka publish and the Postgres
history write are both best-effort, fire-and-forget follow-ups (PLAN §2.1: "Cassandra
write is async and can lag; it's for history/audit, not real-time decisions" — same
principle applies to the Postgres stand-in).
"""
from __future__ import annotations

import time

from libs.common.errors import ConflictError, NotFoundError
from libs.contracts.events import DriverLocation, DriverStatus
from libs.contracts.topics import Topic
from libs.eventbus.bus import EventBus
from libs.geo.driver_index import INGEST_ACCEPTED, INGEST_REJECTED_NOT_ONLINE, INGEST_REJECTED_OUT_OF_ORDER, DriverIndexRepository
from libs.geo.geohash import encode as geohash_encode


class LocationService:
    def __init__(self, index: DriverIndexRepository, event_bus: EventBus) -> None:
        self._index = index
        self._bus = event_bus

    async def go_online_or_offline(self, driver_id: str, status: str, city_id: str) -> int:
        if status == "ONLINE" and not city_id:
            raise ConflictError("cannot go online without a registered city_id")
        seq = await self._index.set_status(driver_id, status, city_id)
        await self._bus.publish(
            Topic.DRIVER_STATUS.value,
            driver_id,
            DriverStatus(driver_id=driver_id, status=status, city_id=city_id).model_dump(mode="json"),
        )
        return seq

    async def ingest_ping(
        self, driver_id: str, lat: float, lng: float, heading: float, speed_kmh: float
    ) -> tuple[bool, str | None]:
        state = await self._index.get_driver_state(driver_id)
        if state is None or "city_id" not in state:
            raise NotFoundError(
                "driver has no known city — go online at least once before pinging", driver_id=driver_id
            )
        city_id = state["city_id"]

        result = await self._index.ingest_ping(driver_id, city_id, lat, lng, heading, speed_kmh, time.time())

        if result == INGEST_REJECTED_NOT_ONLINE:
            return False, "driver is not ONLINE — ping ignored"
        if result == INGEST_REJECTED_OUT_OF_ORDER:
            return False, "ping is older than the last accepted ping — ignored to avoid teleporting the driver backwards"
        assert result == INGEST_ACCEPTED

        # Fire-and-forget: publish for downstream consumers (never on the sync path
        # the HTTP response depends on — PLAN A4, "Kafka carries durability/fan-out,
        # never a user-visible blocking dependency").
        await self._bus.publish(
            Topic.DRIVER_LOCATION.value,
            driver_id,
            DriverLocation(
                driver_id=driver_id,
                lat=lat,
                lng=lng,
                heading=heading,
                speed_kmh=speed_kmh,
                status="ONLINE_AVAILABLE",
                city_id=city_id,
                geohash7=geohash_encode(lat, lng),
            ).model_dump(mode="json"),
        )
        return True, None

    async def get_live_state(self, driver_id: str) -> dict | None:
        return await self._index.get_driver_state(driver_id)
