from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common.config import get_settings
from libs.common.http_errors import install_error_handlers
from libs.common.logging import configure_logging
from libs.eventbus.bus import make_event_bus
from libs.eventbus.dedup import RedisDedup
from libs.persistence.engine import make_engine, make_sessionmaker
from services.notification import container
from services.notification.router import router
from services.notification.service import NotificationFanout

configure_logging("notification")

_bus = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bus
    settings = get_settings()
    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)
    redis = container.configure(sessionmaker, settings)

    fanout = NotificationFanout(sessionmaker, redis)
    dedup = RedisDedup(redis, namespace="notification-consumer")
    _bus = make_event_bus(settings)
    await _bus.start()

    def _make_handler(topic: str):
        async def _handler(payload: dict) -> None:
            if await dedup.seen_before(payload["event_id"]):
                return
            await fanout.handle_event(topic, payload)
        return _handler

    for topic in ("ride.requested", "ride.assigned", "ride.completed", "ride.cancelled", "payment.completed"):
        await _bus.subscribe(topic, "notification-service", _make_handler(topic))

    yield

    await _bus.stop()
    await container.shutdown()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Notification Service", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "notification"}

    return app


app = create_app()
