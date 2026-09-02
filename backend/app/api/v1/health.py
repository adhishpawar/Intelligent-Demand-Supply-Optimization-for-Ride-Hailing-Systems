"""Liveness/readiness -- unauthenticated, unversioned in path, standard practice.

Liveness answers "is the process up". Readiness answers "can it actually serve
traffic" -- degraded (model unloaded) is reported here rather than crashing,
per the design doc's "recommendation staleness must be visible ... never
silently wrong" and the equivalent principle for the service itself.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readiness(request: Request) -> dict:
    registry = request.app.state.model_registry
    db_ok = True
    try:
        db = request.app.state.db
        async with db.session_factory() as session:
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False

    model_ok = registry.is_loaded
    return {
        "status": "ready" if (db_ok and model_ok) else "degraded",
        "database": "ok" if db_ok else "unreachable",
        "model": "loaded" if model_ok else (registry.load_error or "not loaded"),
    }
