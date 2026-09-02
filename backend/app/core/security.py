"""
Authentication and authorization primitives.

Password hashing via passlib/bcrypt, tokens via PyJWT. Authorization is
enforced through FastAPI dependencies (`get_principal`, `require_permission`,
`require_role`) so a route's access control is visible in its signature, not
buried in the handler body -- the same reason the operational platform's
`libs/security/rbac.py` does it this way.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.permissions import role_has_permission
from app.domain.enums import Role

_bearer = HTTPBearer(auto_error=False)

# bcrypt's algorithm ignores anything past 72 bytes; passlib (which used to
# wrap this) is unmaintained and its bcrypt backend has been broken by
# bcrypt>=5's API changes for some time now, so this calls the `bcrypt`
# package directly rather than carrying that dependency.
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    truncated = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(truncated, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        truncated = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(truncated, password_hash.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed hash (e.g. from a corrupted row) -- fail closed, not 500.
        return False


def hash_token(raw_token: str) -> str:
    """One-way hash for storing refresh tokens -- see RefreshToken model docstring."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


class TokenError(Exception):
    pass


def create_access_token(
    *, user_id: str, tenant_id: str, role: Role, settings: Settings
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role.value,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("access token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid access token") from exc
    if payload.get("type") != "access":
        raise TokenError("not an access token")
    return payload


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, resolved once per request from the JWT."""

    user_id: str
    tenant_id: str
    role: Role

    def has_permission(self, permission: str) -> bool:
        return role_has_permission(self.role, permission)

    @property
    def is_super_admin(self) -> bool:
        return self.role == Role.SUPER_ADMIN


async def get_principal(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(creds.credentials, settings)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        role = Role(payload["role"])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid role in token") from exc

    principal = Principal(user_id=payload["sub"], tenant_id=payload["tenant_id"], role=role)
    request.state.principal = principal
    return principal


def require_permission(permission: str):
    """FastAPI dependency factory: 403 unless the caller's role grants `permission`.

    Usage: `principal: Principal = Depends(require_permission("forecast:write"))`
    """

    async def _checker(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not principal.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {principal.role.value} lacks permission {permission!r}",
            )
        return principal

    return _checker


def require_role(*allowed: Role):
    async def _checker(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires one of {[r.value for r in allowed]}",
            )
        return principal

    return _checker


def resolve_tenant_scope(principal: Principal, requested_tenant_id: str | None) -> str:
    """Resolve which tenant a request should be scoped to.

    A SUPER_ADMIN may cross tenants by passing `tenant_id`; every other role is
    hard-pinned to their own token's tenant_id regardless of what they pass --
    this is the actual tenant-isolation enforcement point (API-06 / TEST-04),
    not merely a schema convention.
    """
    if principal.is_super_admin and requested_tenant_id:
        return requested_tenant_id
    return principal.tenant_id
