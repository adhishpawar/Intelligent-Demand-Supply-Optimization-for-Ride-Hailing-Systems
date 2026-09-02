from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common.config import get_settings
from libs.common.http_errors import install_error_handlers
from libs.common.logging import configure_logging
from libs.eventbus.bus import make_event_bus
from services.pricing import container
from services.pricing.router import router
from services.pricing.surge_loop import DemandTracker, SurgeRecomputeLoop

configure_logging("pricing")

_surge_loop: SurgeRecomputeLoop | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _surge_loop
    settings = get_settings()
    redis = container.configure(settings)

    event_bus = make_event_bus(settings)
    await event_bus.start()
    demand_tracker = DemandTracker(redis, event_bus)
    await demand_tracker.start()

    _surge_loop = SurgeRecomputeLoop(redis)
    _surge_loop.start()

    yield

    if _surge_loop:
        _surge_loop.stop()
    await event_bus.stop()
    await container.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Pricing Service", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "pricing"}

    return app


app = create_app()
