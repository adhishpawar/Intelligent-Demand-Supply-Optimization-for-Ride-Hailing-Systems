"""One SQLAlchemy async engine/sessionmaker construction, shared by every service so
pool sizing, echo, and the DSN format are not reinvented nine times.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from libs.common.config import Settings


def make_engine(settings: Settings, *, echo: bool = False) -> AsyncEngine:
    dsn = (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )
    return create_async_engine(dsn, echo=echo, pool_size=10, max_overflow=10, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
