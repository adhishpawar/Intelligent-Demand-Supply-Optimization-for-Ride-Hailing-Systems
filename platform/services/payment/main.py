"""Payment is triggered by consuming `ride.completed` (HLD 3.6: "Trip COMPLETED ->
Pricing computes fare_final -> Payment Service creates a PaymentIntent"), decoupling
Trip from ever waiting on Payment (PLAN's non-negotiable constraint: booking survives
the rest of the platform being slow/down). Consumer dedupes on `event_id` (PLAN §3.3
mechanism 2) before charging — a redelivered `ride.completed` must never trigger a
second charge attempt beyond what `charge_trip`'s own idempotency already prevents;
this is belt AND braces at the event layer too.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from libs.common.config import get_settings
from libs.common.http_errors import install_error_handlers
from libs.common.logging import configure_logging
from libs.eventbus.bus import make_event_bus
from libs.eventbus.dedup import RedisDedup
from libs.persistence.engine import make_engine, make_sessionmaker
from services.payment import container
from services.payment.retry_loop import RetryLoop
from services.payment.router import router

configure_logging("payment")

_retry_loop: RetryLoop | None = None
_consumer_bus = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _retry_loop, _consumer_bus
    settings = get_settings()
    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)
    await container.configure(sessionmaker, settings)

    redis = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
    dedup = RedisDedup(redis, namespace="payment-consumer")
    _consumer_bus = make_event_bus(settings)
    await _consumer_bus.start()

    async def on_ride_completed(payload: dict) -> None:
        if await dedup.seen_before(payload["event_id"]):
            return
        async with sessionmaker() as session:
            await container.get_payment_service().charge_trip(session, payload["trip_id"])

    async def on_ride_cancelled(payload: dict) -> None:
        # Round 9 stakeholder council: cancellation fees were calculated and shown
        # to the rider but never actually posted to the ledger -- see
        # PaymentService.charge_cancellation_fee's docstring.
        if await dedup.seen_before(payload["event_id"]):
            return
        async with sessionmaker() as session:
            await container.get_payment_service().charge_cancellation_fee(session, payload["trip_id"])

    await _consumer_bus.subscribe("ride.completed", "payment-service", on_ride_completed)
    await _consumer_bus.subscribe("ride.cancelled", "payment-service", on_ride_cancelled)

    _retry_loop = RetryLoop(sessionmaker, container.get_payment_service())
    _retry_loop.start()

    yield

    if _retry_loop:
        _retry_loop.stop()
    await _consumer_bus.stop()
    await redis.aclose()
    await container.shutdown()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Payment Service", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health")
    async def health():
        gw = container.get_gateway()
        return {"status": "ok", "service": "payment", "gateway_charge_calls": gw.charge_call_count}

    return app


app = create_app()
