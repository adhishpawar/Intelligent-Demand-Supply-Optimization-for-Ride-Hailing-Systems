"""The transactional outbox (PLAN §1.2(c) / §3.2). `write_outbox_event` is called
INSIDE the same DB transaction as the state change it accompanies — never after
commit — which is what makes "DB write + event publish" atomic without a distributed
transaction. The relay (`OutboxRelay`) is a separate process/task that polls
unpublished rows and hands them to the EventBus.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from libs.common.ids import new_id, utcnow
from libs.contracts.topics import Topic
from libs.eventbus.bus import EventBus

logger = logging.getLogger("outbox")


async def write_outbox_event(
    conn: AsyncConnection | AsyncSession, *, topic: Topic, partition_key: str, payload: dict, event_id: str | None = None
) -> str:
    event_id = event_id or payload.get("event_id") or new_id()
    await conn.execute(
        text(
            """
            INSERT INTO outbox_events (event_id, topic, partition_key, schema_version, payload)
            VALUES (:event_id, :topic, :partition_key, :schema_version, CAST(:payload AS JSONB))
            """
        ),
        {
            "event_id": event_id,
            "topic": topic.value,
            "partition_key": partition_key,
            "schema_version": payload.get("schema_version", 1),
            "payload": _to_json(payload),
        },
    )
    return event_id


def _to_json(payload: dict) -> str:
    import json

    return json.dumps(payload, default=str)


class OutboxRelay:
    """PLAN amendment AM-08: uses `SELECT ... FOR UPDATE SKIP LOCKED` so N relay
    instances can run concurrently without double-publishing, and exposes
    `lag_seconds` (age of the oldest unpublished row) for the admin console / health
    endpoint — a stalled relay must be a visible red number, not a silent outage.
    """

    def __init__(self, sessionmaker, event_bus: EventBus, *, batch_size: int = 50, poll_interval_s: float = 0.5) -> None:
        self._sessionmaker = sessionmaker
        self._bus = event_bus
        self._batch_size = batch_size
        self._poll_interval_s = poll_interval_s
        self._running = False
        self._last_lag_seconds = 0.0

    @property
    def lag_seconds(self) -> float:
        return self._last_lag_seconds

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            try:
                published = await self._relay_once()
            except Exception:
                logger.exception("outbox relay iteration failed")
                published = 0
            if published == 0:
                await asyncio.sleep(self._poll_interval_s)

    def stop(self) -> None:
        self._running = False

    async def _relay_once(self) -> int:
        async with self._sessionmaker() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        text(
                            """
                            SELECT event_id, topic, partition_key, payload, created_at
                            FROM outbox_events
                            WHERE published_at IS NULL
                            ORDER BY created_at
                            LIMIT :n
                            FOR UPDATE SKIP LOCKED
                            """
                        ),
                        {"n": self._batch_size},
                    )
                ).mappings().all()

                if not rows:
                    self._last_lag_seconds = 0.0
                    return 0

                self._last_lag_seconds = (utcnow() - rows[0]["created_at"]).total_seconds()

                for row in rows:
                    await self._bus.publish(row["topic"], row["partition_key"], dict(row["payload"]))
                    await session.execute(
                        text("UPDATE outbox_events SET published_at = now() WHERE event_id = :id"),
                        {"id": row["event_id"]},
                    )
                return len(rows)
