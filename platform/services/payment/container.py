from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.common.config import Settings, get_settings
from libs.eventbus.bus import EventBus, make_event_bus
from services.payment.gateway import FakeGateway
from services.payment.service import PaymentService

_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_gateway = FakeGateway(failure_rate=0.05, timeout_rate=0.02)  # PLAN §2.1: realistic, non-zero failure modes by
# default, but low enough that a live demo mostly shows the happy path — the
# idempotency/retry paths are proven deterministically in tests via force_outcome,
# not left to chance during a presentation.
_event_bus: EventBus | None = None
_service: PaymentService | None = None


async def configure(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    global _sessionmaker, _event_bus, _service
    _sessionmaker = sessionmaker
    _event_bus = make_event_bus(settings)
    await _event_bus.start()
    _service = PaymentService(_gateway, _event_bus, settings)


async def shutdown() -> None:
    if _event_bus:
        await _event_bus.stop()


async def get_session() -> AsyncIterator[AsyncSession]:
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    assert _sessionmaker is not None
    return _sessionmaker


def get_payment_service() -> PaymentService:
    assert _service is not None
    return _service


def get_gateway() -> FakeGateway:
    return _gateway
