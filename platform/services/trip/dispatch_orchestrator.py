"""The backstop dispatch loop (PLAN A3/A4: never trust a single path). The explicit
reject/create paths already trigger an immediate `dispatch_step` for low latency; this
loop exists for the cases those miss — a process restart between create and first
dispatch, an offer that timed out with nobody watching, a trip whose overall matching
deadline (90s) has been breached regardless of attempts remaining.
"""
from __future__ import annotations

import asyncio
import logging

from libs.common.ids import utcnow
from services.trip.repository import TripRepository
from services.trip.service import TripService

logger = logging.getLogger("trip.dispatch_orchestrator")


class DispatchOrchestrator:
    def __init__(self, trip_service: TripService, sessionmaker, interval_s: float = 2.0) -> None:
        self._svc = trip_service
        self._sessionmaker = sessionmaker
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
                logger.exception("dispatch orchestrator tick failed")

    async def _tick(self) -> None:
        now = utcnow()
        async with self._sessionmaker() as session:
            repo = TripRepository(session)
            expired_ids = await repo.find_trips_past_deadline(now)
            needing_dispatch_ids = await repo.find_trips_needing_dispatch(now)

        for trip_id in expired_ids:
            await self._svc.exhaust_if_deadline_passed(trip_id)
        for trip_id in needing_dispatch_ids:
            if trip_id in expired_ids:
                continue
            await self._svc.dispatch_step(trip_id)
