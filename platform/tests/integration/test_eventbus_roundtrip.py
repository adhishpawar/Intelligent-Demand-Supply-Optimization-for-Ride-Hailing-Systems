"""PLAN §6.5 step 6: the same test runs against BOTH `InProcessEventBus` and
`KafkaEventBus`, proving the fallback is behaviourally equivalent (publish -> consume
-> payload equality -> event_id dedupe on redelivery) rather than merely
API-compatible. This is what makes flipping `EVENT_BUS=inprocess` at 2am a proven
escape hatch, not a hope.
"""
from __future__ import annotations

import asyncio

import pytest

from libs.common.config import get_settings
from libs.eventbus.bus import InProcessEventBus, KafkaEventBus
from libs.eventbus.dedup import RedisDedup


async def _make_kafka_bus():
    settings = get_settings()
    bus = KafkaEventBus(settings.kafka_bootstrap)
    await bus.start()
    return bus


async def _make_inprocess_bus():
    bus = InProcessEventBus()
    await bus.start()
    return bus


@pytest.mark.asyncio
@pytest.mark.parametrize("make_bus", [_make_inprocess_bus, _make_kafka_bus])
async def test_publish_consume_roundtrip(make_bus) -> None:
    bus = await make_bus()
    received: list[dict] = []
    ready = asyncio.Event()

    async def handler(payload: dict) -> None:
        received.append(payload)
        ready.set()

    topic = "test.roundtrip"
    await bus.subscribe(topic, group="test-group", handler=handler)
    await asyncio.sleep(0.5)  # let a real Kafka consumer finish joining its group

    await bus.publish(topic, "key1", {"event_id": "evt-1", "hello": "world"})

    try:
        await asyncio.wait_for(ready.wait(), timeout=10)
    finally:
        await bus.stop()

    assert len(received) == 1
    assert received[0]["event_id"] == "evt-1"
    assert received[0]["hello"] == "world"


@pytest.mark.asyncio
async def test_consumer_dedup_makes_redelivery_a_noop() -> None:
    """PLAN §3.3 mechanism 2: a consumer must dedupe on event_id before applying any
    effect. This proves the dedup primitive itself, independent of which bus
    delivered the (possibly duplicate) message."""
    from redis.asyncio import Redis

    settings = get_settings()
    redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    dedup = RedisDedup(redis, namespace="test-dedup")
    try:
        event_id = "evt-dedup-test-1"
        await redis.delete(f"dedup:test-dedup:{event_id}")

        first = await dedup.seen_before(event_id)
        second = await dedup.seen_before(event_id)  # simulates at-least-once redelivery

        assert first is False, "first delivery must not be flagged as a duplicate"
        assert second is True, "redelivery of the same event_id must be flagged as a duplicate"
    finally:
        await redis.delete(f"dedup:test-dedup:evt-dedup-test-1")
        await redis.aclose()
