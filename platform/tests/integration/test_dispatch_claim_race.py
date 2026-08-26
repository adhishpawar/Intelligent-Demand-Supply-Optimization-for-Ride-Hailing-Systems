"""PLAN §8 Chairman synthesis, "the #1 risk to watch": the dispatch race is completely
invisible in a successful single-rider demo and only appears under concurrency. This
test creates the contention deliberately — two riders near the same single online
driver, dispatched concurrently for two DIFFERENT trips — and asserts exactly one
claims the driver. This directly proves PLAN finding A2 is fixed: the HLD's
`SETNX ride_lock:<tripId>` would have let BOTH riders' dispatch cycles succeed (each
trip's lock is uncontended); the fix claims the DRIVER, which is the contended
resource.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from libs.common.config import get_settings
from libs.geo.cities import get_city
from libs.geo.driver_index import DriverIndexRepository
from libs.persistence.engine import make_engine, make_sessionmaker
from services.matching.claim import DriverClaimService
from services.matching.dispatcher import Dispatcher
from services.matching.scoring import WeightedScoreStrategy

PUNE_LAT, PUNE_LNG = 18.5204, 73.8567


@pytest.mark.asyncio
async def test_two_concurrent_dispatches_claim_the_same_driver_exactly_once() -> None:
    settings = get_settings()
    redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)

    driver_id = str(uuid.uuid4())
    trip_a = str(uuid.uuid4())
    trip_b = str(uuid.uuid4())

    try:
        # Seed one ONLINE driver directly (bypassing the identity/location services
        # for test isolation) with real rows the dispatcher's validation query reads.
        async with sessionmaker() as session:
            await session.execute(
                text(
                    "INSERT INTO users (user_id, role, phone, name, rating_avg) "
                    "VALUES (:id, 'DRIVER', :phone, 'Race Test Driver', 4.8)"
                ),
                {"id": driver_id, "phone": f"+91{uuid.uuid4().int % 10**10:010d}"},
            )
            await session.execute(
                text(
                    "INSERT INTO driver_profiles (driver_id, status, city_id, acceptance_rate) "
                    "VALUES (:id, 'ONLINE', 'pune', 0.95)"
                ),
                {"id": driver_id},
            )
            await session.commit()

        index = DriverIndexRepository(redis)
        await index.set_status(driver_id, "ONLINE", "pune")
        await index.ingest_ping(driver_id, "pune", PUNE_LAT + 0.001, PUNE_LNG + 0.001, 0, 0, __import__("time").time())

        city = get_city("pune")
        claims = DriverClaimService(redis, settings.claim_ttl_seconds)
        scorer = WeightedScoreStrategy(city)
        dispatcher = Dispatcher(index, claims, scorer)

        async def dispatch_for(trip_id: str):
            async with sessionmaker() as session:
                return await dispatcher.dispatch_once(
                    session,
                    trip_id=trip_id,
                    city=city,
                    pickup_lat=PUNE_LAT,
                    pickup_lng=PUNE_LNG,
                    excluded_driver_ids=set(),
                    radius_km=3.0,
                    count=20,
                    at=datetime.now(timezone.utc),
                )

        # Two riders near the same driver, dispatched concurrently for two different
        # trips -- this is the exact scenario PLAN finding A2 identifies.
        result_a, result_b = await asyncio.gather(dispatch_for(trip_a), dispatch_for(trip_b))

        winners = [r.driver_id for r in (result_a, result_b) if r.driver_id is not None]
        assert winners == [driver_id], (
            f"expected exactly one dispatch to claim the driver; got results "
            f"a={result_a.driver_id} b={result_b.driver_id}"
        )
        losers = [r for r in (result_a, result_b) if r.driver_id is None]
        assert len(losers) == 1, "the losing dispatch must return no driver, not a duplicate claim"
    finally:
        async with sessionmaker() as session:
            await session.execute(text("DELETE FROM driver_profiles WHERE driver_id = :id"), {"id": driver_id})
            await session.execute(text("DELETE FROM users WHERE user_id = :id"), {"id": driver_id})
            await session.commit()
        await redis.delete(f"driver:{driver_id}", "drivers:city:pune", f"driver_claim:{driver_id}")
        await redis.srem("drivers:online", driver_id)
        await redis.aclose()
        await engine.dispose()
