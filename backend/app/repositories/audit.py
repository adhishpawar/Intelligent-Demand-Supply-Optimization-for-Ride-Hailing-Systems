from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        tenant_id: str | None,
        user_id: str | None,
        action: str,
        resource: str,
        detail: dict | None = None,
    ) -> None:
        self._session.add(
            AuditLog(
                tenant_id=tenant_id,
                user_id=user_id,
                action=action,
                resource=resource,
                detail=detail or {},
            )
        )
        await self._session.flush()

    async def list_for_tenant(self, tenant_id: str, *, limit: int = 100) -> list[AuditLog]:
        stmt = (
            select(AuditLog)
            .where(AuditLog.tenant_id == tenant_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
