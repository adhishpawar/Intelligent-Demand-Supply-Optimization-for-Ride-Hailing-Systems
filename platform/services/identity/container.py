"""Composition root (PLAN §3.2: DI / composition root pattern — handlers depend on
Protocols via `Depends`; tests inject fakes without `mock.patch` spaghetti).
"""
from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.common.config import Settings, get_settings
from services.identity.repository import OtpRepository, PgUserRepository
from services.identity.service import IdentityService
from services.identity.sms import LoggingSmsSender

_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def configure(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    global _sessionmaker
    _sessionmaker = sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    assert _sessionmaker is not None, "container.configure() not called"
    async with _sessionmaker() as session:
        async with session.begin():
            yield session


async def get_identity_service(
    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)
) -> IdentityService:
    return IdentityService(
        users=PgUserRepository(session),
        otps=OtpRepository(session),
        sms=LoggingSmsSender(),
        jwt_secret=settings.jwt_secret,
        jwt_algorithm=settings.jwt_algorithm,
        access_ttl=settings.jwt_access_ttl_seconds,
        refresh_ttl=settings.jwt_refresh_ttl_seconds,
        dev_mode=(settings.environment == "dev"),
        otp_rate_limit_max_requests=settings.otp_rate_limit_max_requests,
        otp_rate_limit_window_seconds=settings.otp_rate_limit_window_seconds,
        otp_verify_max_attempts=settings.otp_verify_max_attempts,
    )
