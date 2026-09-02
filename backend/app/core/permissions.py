"""
RBAC permission model: `resource:action` strings, per the mandate's spec.

WHY A STATIC TABLE RATHER THAN A DB-BACKED PERMISSION SYSTEM
--------------------------------------------------------------
Four roles, ~14 permissions. A configurable permission-assignment system would
be solving a problem this product does not have yet. If a fifth role or a
finer-grained permission is needed later, it is a one-line change here -- and
because every route depends on `require_permission(...)` rather than checking
`role == "..."` inline, that one-line change is the only change needed.

Nothing here does authorization by itself: `app.core.security.require_permission`
is the FastAPI dependency that actually enforces this at the route level. This
module only says what each role is allowed to do.
"""

from __future__ import annotations

from app.domain.enums import Role

# resource:action strings. Kept flat and explicit rather than wildcarded, so
# `grep "forecast:write"` finds every place that matters.
PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.SUPER_ADMIN: frozenset(
        {
            "tenant:read", "tenant:manage",
            "user:read", "user:manage",
            "city:read", "city:manage",
            "zone:read", "zone:manage",
            "forecast:read", "forecast:write",
            "model:read", "model:manage",
            "audit:read",
        }
    ),
    Role.TENANT_ADMIN: frozenset(
        {
            "tenant:read",
            "user:read", "user:manage",
            "city:read", "city:manage",
            "zone:read", "zone:manage",
            "forecast:read", "forecast:write",
            "model:read",
            "audit:read",
        }
    ),
    Role.OPS_ANALYST: frozenset(
        {
            "tenant:read",
            "city:read",
            "zone:read",
            "forecast:read", "forecast:write",
            "model:read",
        }
    ),
    Role.VIEWER: frozenset(
        {
            "tenant:read",
            "city:read",
            "zone:read",
            "forecast:read",
            "model:read",
        }
    ),
}


def role_has_permission(role: Role, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, frozenset())


def permissions_for(role: Role) -> frozenset[str]:
    return PERMISSIONS.get(role, frozenset())
