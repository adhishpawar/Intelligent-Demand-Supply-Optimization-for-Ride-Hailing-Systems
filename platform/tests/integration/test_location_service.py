"""Proves the two Redis-level correctness fixes from PLAN's adversarial review
(section 6.2, findings A6 and B4) against the real driver index — not mocked, the
actual Lua scripts against the actual Redis instance.
"""
from __future__ import annotations

import time

import pytest
from redis.asyncio import Redis

from libs.common.config import get_settings
from libs.geo.driver_index import INGEST_ACCEPTED, INGEST_REJECTED_NOT_ONLINE, INGEST_REJECTED_OUT_OF_ORDER, DriverIndexRepository

TEST_DRIVER = "test-driver-loc-svc"
TEST_CITY = "test-city"


@pytest.fixture
async def index():
    settings = get_settings()
    redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    repo = DriverIndexRepository(redis)
    await redis.delete(f"driver:{TEST_DRIVER}", f"drivers:city:{TEST_CITY}")
    await redis.srem("drivers:online", TEST_DRIVER)
    yield repo
    await redis.delete(f"driver:{TEST_DRIVER}", f"drivers:city:{TEST_CITY}")
    await redis.srem("drivers:online", TEST_DRIVER)
    await redis.aclose()


@pytest.mark.asyncio
async def test_ping_rejected_while_offline(index: DriverIndexRepository) -> None:
    # never went online — a ping must be rejected and must not create geo membership
    result = await index.ingest_ping(TEST_DRIVER, TEST_CITY, 18.52, 73.85, 0, 0, time.time())
    assert result == INGEST_REJECTED_NOT_ONLINE

    state = await index.search_one_city(TEST_CITY, 18.52, 73.85, 3.0, 20)
    assert TEST_DRIVER not in [c.driver_id for c in state]


@pytest.mark.asyncio
async def test_out_of_order_ping_is_rejected(index: DriverIndexRepository) -> None:
    await index.set_status(TEST_DRIVER, "ONLINE", TEST_CITY)

    now = time.time()
    first = await index.ingest_ping(TEST_DRIVER, TEST_CITY, 18.52, 73.85, 0, 0, now)
    assert first == INGEST_ACCEPTED

    # a delayed, OLDER ping arrives after a newer one already landed
    stale = await index.ingest_ping(TEST_DRIVER, TEST_CITY, 18.99, 74.10, 0, 0, now - 5)
    assert stale == INGEST_REJECTED_OUT_OF_ORDER

    # the driver's recorded position must still be the newer one, not the stale one
    state = await index.get_driver_state(TEST_DRIVER)
    assert float(state["lat"]) == pytest.approx(18.52)


@pytest.mark.asyncio
async def test_zombie_ping_after_offline_does_not_resurrect_driver(index: DriverIndexRepository) -> None:
    """PLAN finding B4-3: a ping issued before an offline toggle but delivered after
    it must never re-add the driver to the geo index."""
    await index.set_status(TEST_DRIVER, "ONLINE", TEST_CITY)
    await index.ingest_ping(TEST_DRIVER, TEST_CITY, 18.52, 73.85, 0, 0, time.time())

    await index.set_status(TEST_DRIVER, "OFFLINE", TEST_CITY)

    # the "zombie" ping: in-flight before offline, arriving after
    result = await index.ingest_ping(TEST_DRIVER, TEST_CITY, 18.53, 73.86, 0, 0, time.time())
    assert result == INGEST_REJECTED_NOT_ONLINE

    candidates = await index.search_one_city(TEST_CITY, 18.52, 73.85, 5.0, 20)
    assert TEST_DRIVER not in [c.driver_id for c in candidates]

    online_ids = await index.list_online_driver_ids()
    assert TEST_DRIVER not in online_ids


@pytest.mark.asyncio
async def test_status_change_and_geo_membership_are_atomic(index: DriverIndexRepository) -> None:
    """PLAN finding B4-2 (torn state): status and geo membership change together via
    one Lua EVAL. This test cannot directly inject a crash mid-script (Redis EVAL is
    inherently atomic — that IS the fix), so it asserts the observable contract: right
    after set_status(ONLINE), the driver is immediately a legal ingest target."""
    seq1 = await index.set_status(TEST_DRIVER, "ONLINE", TEST_CITY)
    result = await index.ingest_ping(TEST_DRIVER, TEST_CITY, 18.52, 73.85, 0, 0, time.time())
    assert result == INGEST_ACCEPTED

    seq2 = await index.set_status(TEST_DRIVER, "OFFLINE", TEST_CITY)
    assert seq2 == seq1 + 1  # state_seq is monotonic across toggles
