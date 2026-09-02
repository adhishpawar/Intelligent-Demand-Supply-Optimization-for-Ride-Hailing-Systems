"""FastAPI dependency for a request-scoped DB session.

The Database instance lives on `app.state.db`, set up once in the app factory
(`app.main.create_app`) rather than as a module-level singleton, so tests can
build a completely separate app+Database pointed at a test database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db = request.app.state.db
    async with db.session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
