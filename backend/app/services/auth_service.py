"""
Auth application service: registration, login, refresh, logout.

Sits between the router and the repositories so the router stays thin and this
logic is reusable (e.g. by the seed script, which registers the first tenant
the same way an HTTP caller would).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, UnauthorizedError
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    verify_password,
)
from app.db.models import Tenant, User
from app.domain.enums import Role
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.tenants import TenantRepository
from app.repositories.users import UserRepository


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._tenants = TenantRepository(session)
        self._users = UserRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)

    async def register_tenant(
        self,
        *,
        tenant_name: str,
        tenant_slug: str,
        admin_email: str,
        admin_password: str,
        admin_full_name: str,
    ) -> tuple[Tenant, User]:
        if await self._tenants.get_by_slug(tenant_slug) is not None:
            raise ConflictError(f"tenant slug {tenant_slug!r} is already in use")
        if await self._users.get_by_email(admin_email) is not None:
            raise ConflictError(f"email {admin_email!r} is already registered")

        tenant = await self._tenants.create(name=tenant_name, slug=tenant_slug)
        user = await self._users.create(
            tenant_id=tenant.id,
            email=admin_email,
            password_hash=hash_password(admin_password),
            full_name=admin_full_name,
            role=Role.TENANT_ADMIN,
        )
        await self._session.commit()
        return tenant, user

    async def create_user(
        self, *, tenant_id: str, email: str, password: str, full_name: str, role: Role
    ) -> User:
        if await self._users.get_by_email(email) is not None:
            raise ConflictError(f"email {email!r} is already registered")
        user = await self._users.create(
            tenant_id=tenant_id,
            email=email,
            password_hash=hash_password(password),
            full_name=full_name,
            role=role,
        )
        await self._session.commit()
        return user

    async def _issue_tokens(self, user: User) -> tuple[str, str, int]:
        access = create_access_token(
            user_id=user.id, tenant_id=user.tenant_id, role=Role(user.role), settings=self._settings
        )
        raw_refresh = new_opaque_token()
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._settings.refresh_token_ttl_seconds
        )
        await self._refresh_tokens.create(
            user_id=user.id, token_hash=hash_token(raw_refresh), expires_at=expires_at
        )
        await self._session.commit()
        return access, raw_refresh, self._settings.access_token_ttl_seconds

    async def login(self, *, email: str, password: str) -> tuple[str, str, int]:
        user = await self._users.get_by_email(email)
        # Constant-shape error whether the email exists or the password is
        # wrong -- do not let login responses enumerate valid accounts.
        if user is None or not verify_password(password, user.password_hash):
            raise UnauthorizedError("invalid email or password")
        if not user.is_active:
            raise UnauthorizedError("account is deactivated")
        return await self._issue_tokens(user)

    async def refresh(self, *, raw_refresh_token: str) -> tuple[str, str, int]:
        record = await self._refresh_tokens.get_valid(hash_token(raw_refresh_token))
        if record is None:
            raise UnauthorizedError("invalid or expired refresh token")
        user = await self._users.get_by_id_any_tenant(record.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("account is no longer active")
        # Rotate: the presented refresh token is single-use.
        await self._refresh_tokens.revoke(record)
        return await self._issue_tokens(user)

    async def logout(self, *, raw_refresh_token: str) -> None:
        record = await self._refresh_tokens.get_valid(hash_token(raw_refresh_token))
        if record is not None:
            await self._refresh_tokens.revoke(record)
            await self._session.commit()
