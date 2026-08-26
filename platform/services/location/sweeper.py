"""PLAN C3 line item: staleness eviction. A driver whose app crashed or lost
connectivity never calls `set_status(OFFLINE)` — they simply stop pinging. Left alone,
they would sit in the geo index forever, matchable but unreachable.

Two independent layers handle this (belt and braces, same philosophy as the driver
claim in PLAN A2):
  1. `DriverIndexRepository.search_one_city` already filters out anyone whose last
     ping is older than `max_ping_age_s` at READ time — so a rider is never offered a
     phantom driver even between sweeps.
  2. This sweeper runs on a slower cadence and does the actual cleanup: it forces
     `set_status(OFFLINE)` (the same atomic Lua path a real toggle uses) for anyone
     stale, so `driver_profiles.status`, the geo set, and the online-drivers set all
     converge back to reality without waiting for the driver to reconnect.
"""
from __future__ import annotations

import asyncio
import logging

from libs.geo.driver_index import DriverIndexRepository

logger = logging.getLogger("location.sweeper")

DEFAULT_MAX_PING_AGE_S = 45.0
DEFAULT_INTERVAL_S = 20.0


class StalenessSweeper:
    def __init__(
        self, index: DriverIndexRepository, *, max_ping_age_s: float = DEFAULT_MAX_PING_AGE_S, interval_s: float = DEFAULT_INTERVAL_S
    ) -> None:
        self._index = index
        self._max_ping_age_s = max_ping_age_s
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        self.evicted_count = 0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run_forever(self) -> None:
        import time

        while True:
            try:
                await asyncio.sleep(self._interval_s)
                online_ids = await self._index.list_online_driver_ids()
                now = time.time()
                for driver_id in online_ids:
                    state = await self._index.get_driver_state(driver_id)
                    if not state:
                        continue
                    last_ping = float(state.get("last_ping_ts", 0))
                    if now - last_ping > self._max_ping_age_s:
                        city_id = state.get("city_id", "")
                        await self._index.set_status(driver_id, "OFFLINE", city_id)
                        self.evicted_count += 1
                        logger.info(
                            "evicted stale driver",
                            extra={"extra_fields": {"driver_id": driver_id, "stale_for_s": now - last_ping}},
                        )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("staleness sweeper iteration failed")
