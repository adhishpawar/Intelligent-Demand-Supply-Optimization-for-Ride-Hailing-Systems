"""Async retry with backoff for failed charges (`ride_hailing_HLD_LLD.md` section 3.6:
"on failure -> retry with backup method, else flag trip for manual collection... ride
is never blocked on payment success"). Backoff is attempt-count-based (30s * attempt),
capped by MAX_RETRY_ATTEMPTS in service.py — beyond that the trip stays PAID_PENDING
for manual/ops collection, which is the correct terminal-ish state per the HLD (not a
crash, not a silent loss, just flagged).

This loop ALSO reconciles trips whose charge was never even attempted — found live
during this session's own testing: a `ride.completed` Kafka message can go
unprocessed by a consumer group that has churned through many restarts (this dev
session hard-killed the payment service process repeatedly), and relying on Kafka
delivery alone for something as consequential as "did this rider get charged" is
exactly the single-path trust PLAN §3.3/AM-08 warns against. Never trusting one path
is the whole point of a reconciler — same principle as the Redis<->Postgres
reconciler design for driver state (AM-05), applied here to the payment pipeline.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from services.payment.service import MAX_RETRY_ATTEMPTS, PaymentService

logger = logging.getLogger("payment.retry_loop")


class RetryLoop:
    def __init__(self, sessionmaker, service: PaymentService, interval_s: float = 10.0) -> None:
        self._sessionmaker = sessionmaker
        self._service = service
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run_forever(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval_s)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("payment retry loop iteration failed")

    async def _tick(self) -> None:
        await self._retry_failed()
        await self._reconcile_never_attempted()

    async def _retry_failed(self) -> None:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    text("SELECT trip_id FROM payments WHERE status = 'FAILED' AND attempt_count < :max LIMIT 20"),
                    {"max": MAX_RETRY_ATTEMPTS},
                )
            ).all()
        for (trip_id,) in rows:
            async with self._sessionmaker() as session:
                try:
                    result = await self._service.retry_pending(session, str(trip_id))
                    logger.info("payment retry", extra={"extra_fields": {"trip_id": str(trip_id), "result": result}})
                except Exception:
                    logger.exception("payment retry failed", extra={"extra_fields": {"trip_id": str(trip_id)}})

    async def _reconcile_never_attempted(self) -> None:
        """Trips COMPLETED more than a grace period ago with no payment row at all —
        the ride.completed event was lost, delayed past a rebalance, or never
        delivered. A short grace period avoids racing the primary consumer path for
        a trip that just completed a moment ago."""
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT t.trip_id FROM trips t
                        LEFT JOIN payments p ON p.trip_id = t.trip_id
                        WHERE t.status = 'COMPLETED' AND p.payment_id IS NULL
                          AND t.completed_at < now() - interval '10 seconds'
                        LIMIT 20
                        """
                    )
                )
            ).all()
        for (trip_id,) in rows:
            async with self._sessionmaker() as session:
                try:
                    result = await self._service.charge_trip(session, str(trip_id))
                    logger.warning(
                        "reconciled a trip whose ride.completed event was never processed",
                        extra={"extra_fields": {"trip_id": str(trip_id), "result": result}},
                    )
                except Exception:
                    logger.exception("payment reconciliation failed", extra={"extra_fields": {"trip_id": str(trip_id)}})
