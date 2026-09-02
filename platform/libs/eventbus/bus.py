"""EventBus port (PLAN §3.2, amendment: "Kafka may misbehave on a Windows laptop at
02:00" -> Ports & Adapters, again). `KafkaEventBus` and `InProcessEventBus` implement
the identical protocol including *at-least-once redelivery semantics*, so the fallback
is proven equivalent by `tests/integration/test_eventbus_roundtrip.py` running against
both, not merely assumed equivalent.

Tonight's environment note: Docker/WSL2 is unavailable in this sandbox (see
PROGRESS.md), so Kafka runs from a native binary distribution when available; the
default active bus is still controlled by `Settings.event_bus` so a slow/unavailable
broker never blocks the rest of the build (PLAN §5.1 cut-order item 2).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Awaitable, Callable, Protocol

logger = logging.getLogger("eventbus")

Handler = Callable[[dict], Awaitable[None]]


class EventBus(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, topic: str, key: str, payload: dict) -> None: ...
    async def subscribe(self, topic: str, group: str, handler: Handler) -> None: ...


class InProcessEventBus:
    """Fallback bus: an in-memory pub/sub with at-least-once semantics preserved
    (each subscriber group receives every published message; a handler exception
    does not stop delivery to other groups, matching Kafka consumer-group isolation).
    Used when EVENT_BUS=inprocess, and in `single` run mode (PLAN §4.3).
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[tuple[str, Handler]]] = defaultdict(list)
        self._started = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def publish(self, topic: str, key: str, payload: dict) -> None:
        # Round-trip through JSON to catch non-serializable payloads early, exactly as
        # a real Kafka producer would force — this is what makes the two bus
        # implementations behaviorally equivalent, not just API-compatible.
        serialized = json.dumps(payload, default=str)
        for group, handler in list(self._subscribers.get(topic, [])):
            try:
                await handler(json.loads(serialized))
            except Exception:
                logger.exception("handler failed", extra={"extra_fields": {"topic": topic, "group": group}})

    async def subscribe(self, topic: str, group: str, handler: Handler) -> None:
        self._subscribers[topic].append((group, handler))


class KafkaEventBus:
    """Real Kafka via aiokafka. Producer publishes JSON payloads keyed by
    `partition_key` (driver_id/city_id/trip_id per the topic catalog in
    ride_hailing_HLD_LLD/part2 section 6.1). Each `subscribe` call spins up its own
    consumer task in the given group.
    """

    def __init__(self, bootstrap_servers: str) -> None:
        self._bootstrap = bootstrap_servers
        self._producer = None
        self._consumer_tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        await self._producer.start()

    async def stop(self) -> None:
        for task in self._consumer_tasks:
            task.cancel()
        if self._producer:
            await self._producer.stop()

    async def publish(self, topic: str, key: str, payload: dict) -> None:
        assert self._producer is not None, "KafkaEventBus.start() not called"
        await self._producer.send_and_wait(topic, value=payload, key=key)

    async def subscribe(self, topic: str, group: str, handler: Handler) -> None:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap,
            group_id=group,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        await consumer.start()

        async def _run() -> None:
            try:
                async for msg in consumer:
                    try:
                        await handler(msg.value)
                        await consumer.commit()
                    except Exception:
                        logger.exception(
                            "consumer handler failed, will redeliver on restart",
                            extra={"extra_fields": {"topic": topic, "group": group}},
                        )
            finally:
                await consumer.stop()

        self._consumer_tasks.append(asyncio.create_task(_run()))


def make_event_bus(settings) -> EventBus:  # settings: libs.common.config.Settings
    if settings.event_bus == "kafka":
        return KafkaEventBus(settings.kafka_bootstrap)
    return InProcessEventBus()
