"""Session-wide test hygiene.

Round 2 stakeholder council (tech lead) added real per-phone OTP-request rate
limiting (services/identity/service.py) after finding `/v1/auth/otp/request`
completely unbounded -- a real SMS-cost-abuse / inbox-flood vector. That limit
counts against real wall-clock time in the `otp_codes` table.

This repo's dev Postgres is long-lived across an entire session with no
per-test-run reset (native-binary infra, not throwaway containers -- see
PROGRESS.md's infra pivot notes), and several test files intentionally reuse a
handful of fixed seeded phone numbers (tools/seed.py) across many test functions.
Repeatedly running the suite (or the suite + tools/demo.py + manual browser
sessions) within the rate limit's window otherwise trips it for reasons that have
nothing to do with what any individual test is checking -- a real flake this
session hit twice while building the feature. Clearing `otp_codes` once per test
session (dev-only historical rows, never source data) fixes that without touching
or weakening the production rate limit itself.
"""
from __future__ import annotations

import asyncio

import asyncpg
import pytest

from libs.common.config import get_settings


@pytest.fixture(scope="session", autouse=True)
def _clear_otp_history() -> None:
    async def _clear() -> None:
        s = get_settings()
        conn = await asyncpg.connect(
            host=s.postgres_host, port=s.postgres_port, database=s.postgres_db,
            user=s.postgres_user, password=s.postgres_password,
        )
        try:
            await conn.execute("DELETE FROM otp_codes")
        finally:
            await conn.close()

    asyncio.run(_clear())
