"""FastAPI RBAC dependencies — Layer 2 of PLAN §3.1's three-layer defense in depth.

`get_principal` accepts EITHER:
  - `X-Internal-Principal: <payload>.<hmac>` — the gateway's signed assertion. If this
    header is present it MUST verify; a forged/garbage value fails closed with 401 and
    is never silently ignored in favour of a JWT also present on the same request
    (that would reopen exactly the bypass this header exists to close).
  - `Authorization: Bearer <jwt>` — a raw user JWT, verified independently by this
    service. This is what makes "call the service directly, no gateway" still work
    correctly for legitimate calls, and still enforce the caller's real role for
    illegitimate ones (PLAN test D3-2).

`require_roles(*roles)` is Layer 1's re-check, done again at the service: even a
correctly-authenticated principal is rejected here if their role isn't allowed.
Ownership checks (Layer 3 — "authorized for the role but not the row") are enforced
in each service's repository layer, not here — see e.g. services/trip/repository.py.
"""
from __future__ import annotations

from fastapi import Depends, Header

from libs.common.config import Settings, get_settings
from libs.common.errors import UnauthorizedError, ForbiddenError
from libs.security.internal_assertion import verify_principal
from libs.security.jwt_tokens import decode_token
from libs.security.principal import Principal, Role


async def get_principal(
    x_internal_principal: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if x_internal_principal is not None:
        # Presence of this header commits us to this path. A bad signature is fatal —
        # it does NOT fall back to checking a JWT that might also be present.
        return verify_principal(x_internal_principal, secret=settings.internal_assertion_secret)

    if authorization is not None and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        return decode_token(
            token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm, expected_type="access"
        )

    raise UnauthorizedError("missing credentials: no X-Internal-Principal or Authorization header")


def require_roles(*roles: Role):
    async def _dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_role(*roles):
            raise ForbiddenError(
                f"role {principal.role.value} is not permitted on this endpoint",
                required_roles=[r.value for r in roles],
            )
        return principal

    return _dependency
