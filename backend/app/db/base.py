"""Declarative base + async engine/session factory.

Async throughout (asyncpg driver) to match the operational platform's stack in
`platform/` and because inference calls under load benefit from not blocking
the event loop on I/O.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings


class Base(DeclarativeBase):
    pass


def make_engine(settings: Settings):
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=False,
    )


def make_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


class Database:
    """Owns the engine/sessionmaker lifecycle for the app.

    A thin wrapper rather than module-level globals so tests can construct an
    independent Database pointed at a different URL without monkeypatching.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = make_engine(settings)
        self.session_factory = make_session_factory(self.engine)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
