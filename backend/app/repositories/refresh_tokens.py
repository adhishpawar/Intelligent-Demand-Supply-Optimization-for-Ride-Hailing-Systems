from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, user_id: str, token_hash: str, expires_at: datetime) -> RefreshToken:
        row = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_valid(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        if row.revoked_at is not None or row.expires_at < now:
            return None
        return row

    async def revoke(self, row: RefreshToken) -> None:
        row.revoked_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def revoke_all_for_user(self, user_id: str) -> None:
        stmt = select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        now = datetime.now(timezone.utc)
        for row in rows:
            row.revoked_at = now
        await self._session.flush()
