"""Shared fixtures for backend tests.

Runs against a REAL Postgres database (`rideops_ml_test`), not SQLite or
mocks -- the JSON columns, unique constraints, and `ON CONFLICT` upserts this
schema relies on are Postgres-specific, and a SQLite-backed test suite would
pass while hiding exactly the kind of defect this whole audit was about (code
that looks right, verified against the wrong thing).

Each test function gets a fresh schema (`Base.metadata.drop_all` +
`create_all` in an autouse fixture) so tests are independent without needing a
real migration run per test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.base import Base
from app.main import create_app

TEST_DATABASE_URL = "postgresql+asyncpg://ridehail:ridehail_dev_pw@localhost:5433/rideops_ml_test"


def make_test_settings(**overrides) -> Settings:
    defaults = dict(
        database_url=TEST_DATABASE_URL,
        redis_url="redis://localhost:6380/2",
        jwt_secret="test-secret-not-for-production",
        environment="test",
        access_token_ttl_seconds=3600,
        forecast_cache_ttl_seconds=1,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest_asyncio.fixture
async def app_settings() -> Settings:
    return make_test_settings()


@pytest_asyncio.fixture
async def test_app(app_settings: Settings):
    application = create_app(app_settings)

    async with application.router.lifespan_context(application):
        db = application.state.db
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        yield application

        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(test_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
