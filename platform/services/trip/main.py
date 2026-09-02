from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common.config import get_settings
from libs.common.http_errors import install_error_handlers
from libs.common.logging import configure_logging
from libs.eventbus.bus import make_event_bus
from libs.persistence.engine import make_engine, make_sessionmaker
from libs.persistence.outbox import OutboxRelay
from services.trip import container
from services.trip.dispatch_orchestrator import DispatchOrchestrator
from services.trip.router import router

configure_logging("trip")

_orchestrator: DispatchOrchestrator | None = None
_outbox_relay: OutboxRelay | None = None
_relay_task = None
_relay_bus = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator, _outbox_relay, _relay_task, _relay_bus
    settings = get_settings()
    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)
    container.configure(sessionmaker, settings)

    # THE OUTBOX RELAY. Found missing during live testing tonight: every event
    # Trip emits via UnitOfWork.emit() (ride.requested/assigned/completed/cancelled)
    # is written into outbox_events atomically with its state change (PLAN §1.2c),
    # but that table only ever DRAINS if something polls it and publishes — this is
    # that something. Without it, the outbox pattern silently degrades to "write
    # events nobody ever reads," which is worse than not having the pattern at all
    # because it LOOKS like events are flowing (the DB writes succeed) while nothing
    # downstream (Payment, Notification, Pricing's demand tracker) ever sees them.
    _relay_bus = make_event_bus(settings)
    await _relay_bus.start()
    _outbox_relay = OutboxRelay(sessionmaker, _relay_bus, poll_interval_s=0.5)
    _relay_task = asyncio.create_task(_outbox_relay.run_forever())

    _orchestrator = DispatchOrchestrator(container.get_trip_service(), sessionmaker)
    _orchestrator.start()

    yield

    _orchestrator.stop()
    if _outbox_relay:
        _outbox_relay.stop()
    if _relay_task:
        _relay_task.cancel()
    if _relay_bus:
        await _relay_bus.stop()
    await container.shutdown()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Trip Service", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health")
    async def health():
        lag = _outbox_relay.lag_seconds if _outbox_relay else 0.0
        return {"status": "ok", "service": "trip", "outbox_lag_seconds": lag}

    return app


app = create_app()
