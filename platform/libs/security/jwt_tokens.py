"""User-facing JWTs (HS256 — PLAN §4.1: "keeps key management to an env var tonight").
Used both by end-user clients (rider/driver/admin apps) calling the gateway, and, per
PLAN §3.1 Layer 2, by any service called *directly* with a raw user token — a service
independently verifies the JWT itself rather than only trusting the gateway's
internal assertion header.
"""
from __future__ import annotations

from datetime import timedelta

from jose import JWTError, jwt

from libs.common.errors import UnauthorizedError
from libs.common.ids import utcnow
from libs.security.principal import Principal, Role


def create_access_token(principal: Principal, *, secret: str, algorithm: str, ttl_seconds: int) -> str:
    now = utcnow()
    claims = {
        "sub": principal.user_id,
        "role": principal.role.value,
        "phone": principal.phone,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(claims, secret, algorithm=algorithm)


def create_refresh_token(principal: Principal, *, secret: str, algorithm: str, ttl_seconds: int) -> str:
    now = utcnow()
    claims = {
        "sub": principal.user_id,
        "role": principal.role.value,
        "phone": principal.phone,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(claims, secret, algorithm=algorithm)


def decode_token(token: str, *, secret: str, algorithm: str, expected_type: str = "access") -> Principal:
    try:
        claims = jwt.decode(token, secret, algorithms=[algorithm])
    except JWTError as exc:
        raise UnauthorizedError("invalid or expired token") from exc

    if claims.get("type") != expected_type:
        raise UnauthorizedError(f"expected a {expected_type} token")

    try:
        role = Role(claims["role"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("token missing a valid role claim") from exc

    return Principal(user_id=claims["sub"], role=role, phone=claims.get("phone", ""))
