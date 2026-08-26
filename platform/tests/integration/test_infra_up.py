"""PLAN §6.5 (Infrastructure Executor), step 5 — THE FIRST TEST. Nothing else is
trusted until this goes green: it proves Postgres is reachable and migrated, Redis is
reachable, and — the actual geospatial premise the entire system depends on — a radius
query against the Redis geo index returns exactly the in-radius drivers, nearest
first, with distances an independent haversine calculation agrees with.

Environment note: this Redis build (tporadowski/redis 5.0.14, a real Windows-native
Redis fork — see PROGRESS.md) predates GEOSEARCH (Redis 6.2+). `GEORADIUS` is used
instead — the same underlying geo-index feature, older command name, fully sufficient
for this system's access pattern (radius search from a point, sorted, distance-
annotated, COUNT-limited). If this ever runs against Redis 7 in `compose` mode,
GEOSEARCH is a one-line swap behind `libs`'s driver index repository.
"""
from __future__ import annotations

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from libs.common.config import get_settings
from libs.geo.haversine import haversine_m
from libs.persistence.engine import make_engine
from libs.persistence.migrate import run_migrations

PUNE_LAT, PUNE_LNG = 18.5204, 73.8567


@pytest.mark.asyncio
async def test_postgres_reachable_and_migrated() -> None:
    settings = get_settings()
    applied = await run_migrations(settings)
    # idempotent: either this run applied V001, or a prior run already did — both are fine.
    engine = make_engine(settings)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1

            versions = (
                await conn.execute(text("SELECT version FROM schema_migrations"))
            ).scalars().all()
            assert "V001__core" in versions, f"V001 not applied; schema_migrations={versions}"
    finally:
        await engine.dispose()
    _ = applied  # not asserted on directly — idempotent re-runs are expected


@pytest.mark.asyncio
async def test_redis_reachable_set_get_roundtrip() -> None:
    settings = get_settings()
    redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    try:
        assert await redis.ping() is True
        await redis.set("infra_test_key", "hello", ex=30)
        assert await redis.get("infra_test_key") == "hello"
        await redis.delete("infra_test_key")
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_geosearch_proves_the_geospatial_premise() -> None:
    """THE test. If this fails, everything built on top of it is worthless — which is
    exactly why PLAN §6.5 puts it first, before any service code exists."""
    settings = get_settings()
    redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    key = "test:drivers:city:pune"
    try:
        await redis.delete(key)

        # driver_near: ~30m from the rider point -> in radius
        await redis.geoadd(key, (73.8570, 18.5206, "driver_near"))
        # driver_mid: ~1.4km away -> in radius (< 3km)
        await redis.geoadd(key, (73.8680, 18.5280, "driver_mid"))
        # driver_far: well outside 3km -> must NOT be returned
        await redis.geoadd(key, (73.9500, 18.6000, "driver_far"))

        # GEORADIUS (Redis <6.2 name for GEOSEARCH's radius-from-point mode; see module
        # docstring). ASC = nearest first. COUNT bounds the result set exactly as the
        # matching service's candidate retrieval will.
        raw = await redis.execute_command(
            "GEORADIUS", key, PUNE_LNG, PUNE_LAT, 3, "km", "ASC", "COUNT", 20, "WITHDIST"
        )
        results = [(name, float(dist)) for name, dist in raw]

        names = [r[0] for r in results]
        assert names == ["driver_near", "driver_mid"], (
            f"expected exactly the 2 in-radius drivers, nearest first; got {names}"
        )

        # Cross-check each returned distance against an independent haversine
        # calculation — within 1%, proving the index's notion of distance is correct,
        # not just its membership test.
        independent = {
            "driver_near": haversine_m(PUNE_LAT, PUNE_LNG, 18.5206, 73.8570) / 1000.0,
            "driver_mid": haversine_m(PUNE_LAT, PUNE_LNG, 18.5280, 73.8680) / 1000.0,
        }
        for name, redis_km in results:
            expected_km = independent[name]
            rel_error = abs(redis_km - expected_km) / expected_km
            assert rel_error < 0.01, (
                f"{name}: redis reported {redis_km}km, independent haversine says "
                f"{expected_km}km ({rel_error:.2%} off)"
            )
    finally:
        await redis.delete(key)
        await redis.aclose()
