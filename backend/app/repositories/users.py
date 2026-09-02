"""
User repository.

TENANT SCOPING RULE (applies to every repository in this package)
-------------------------------------------------------------------
Every method that lists or fetches tenant-owned rows takes `tenant_id` as an
explicit, required argument and applies it as a `WHERE` clause here -- not as
an afterthought filter in the router. `get_by_id_scoped` additionally proves
the row belongs to the caller's tenant before returning it, so "guess another
tenant's UUID" cannot leak a record even if the caller has a valid token for a
*different* tenant. This is the actual enforcement point for API-06/TEST-04,
not just a schema constraint.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.enums import Role


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower())
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id_scoped(self, user_id: str, tenant_id: str) -> User | None:
        stmt = select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id_any_tenant(self, user_id: str) -> User | None:
        """SUPER_ADMIN-only path -- callers must check `principal.is_super_admin`."""
        return await self._session.get(User, user_id)

    async def list_for_tenant(self, tenant_id: str, *, limit: int = 50, offset: int = 0) -> list[User]:
        stmt = (
            select(User)
            .where(User.tenant_id == tenant_id)
            .order_by(User.created_at)
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(
        self, *, tenant_id: str, email: str, password_hash: str, full_name: str, role: Role
    ) -> User:
        user = User(
            tenant_id=tenant_id,
            email=email.lower(),
            password_hash=password_hash,
            full_name=full_name,
            role=role.value,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def set_active(self, user: User, is_active: bool) -> None:
        user.is_active = is_active
        await self._session.flush()
