from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.common.config import Settings
from services.ratings.service import RatingsService

_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_service: RatingsService | None = None


def configure(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    global _sessionmaker, _service
    _sessionmaker = sessionmaker
    _service = RatingsService(settings)


async def get_session() -> AsyncIterator[AsyncSession]:
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session


def get_ratings_service() -> RatingsService:
    assert _service is not None
    return _service
