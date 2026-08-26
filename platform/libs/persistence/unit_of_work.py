"""Unit of Work (PLAN §3.2): `async with uow: ... uow.emit(topic, key, payload)` commits
the state change and the outbox row together, in the same transaction. This is what
makes the correct thing (atomic write+event) the *default* thing — a handler has to
work to get this wrong, not merely forget to be careful.
"""
from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.topics import Topic
from libs.persistence.outbox import write_outbox_event


class UnitOfWork:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        self.session: AsyncSession | None = None
        self._pending_events: list[tuple[Topic, str, dict]] = []

    async def __aenter__(self) -> Self:
        self.session = self._sessionmaker()
        await self.session.begin()
        self._pending_events = []
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        assert self.session is not None
        try:
            if exc_type is None:
                for topic, key, payload in self._pending_events:
                    await write_outbox_event(self.session, topic=topic, partition_key=key, payload=payload)
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()
            self.session = None

    def emit(self, topic: Topic, partition_key: str, payload: dict) -> None:
        """Queue an event to be written to the outbox at commit time (same transaction
        as every write this UoW's session performed)."""
        self._pending_events.append((topic, partition_key, payload))
