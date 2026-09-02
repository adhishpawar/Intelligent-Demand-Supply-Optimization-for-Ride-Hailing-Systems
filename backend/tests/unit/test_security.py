from __future__ import annotations

import time

import jwt as pyjwt
import pytest

from app.core.config import Settings
from app.core.permissions import PERMISSIONS, role_has_permission
from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.domain.enums import Role


def _settings() -> Settings:
    return Settings(jwt_secret="unit-test-secret")


def test_password_hash_is_not_the_plaintext():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert verify_password("correct horse battery staple", h)


def test_wrong_password_does_not_verify():
    h = hash_password("real-password")
    assert not verify_password("wrong-password", h)


def test_verify_password_fails_closed_on_malformed_hash():
    assert not verify_password("anything", "not-a-real-bcrypt-hash")


def test_password_longer_than_72_bytes_still_hashes_and_verifies_consistently():
    """bcrypt truncates at 72 bytes; hash and verify must agree on that
    truncation rather than one side silently behaving differently.
    """
    long_password = "x" * 200
    h = hash_password(long_password)
    assert verify_password(long_password, h)
    assert verify_password("x" * 72, h)  # same after truncation


def test_access_token_round_trips():
    settings = _settings()
    token = create_access_token(user_id="u1", tenant_id="t1", role=Role.OPS_ANALYST, settings=settings)
    payload = decode_access_token(token, settings)
    assert payload["sub"] == "u1"
    assert payload["tenant_id"] == "t1"
    assert payload["role"] == "OPS_ANALYST"


def test_expired_access_token_is_rejected():
    settings = _settings()
    now = int(time.time())
    expired_payload = {
        "sub": "u1", "tenant_id": "t1", "role": "VIEWER", "type": "access",
        "iat": now - 7200, "exp": now - 3600,
    }
    token = pyjwt.encode(expired_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(TokenError):
        decode_access_token(token, settings)


def test_token_signed_with_wrong_secret_is_rejected():
    settings = _settings()
    other = Settings(jwt_secret="a-different-secret")
    token = create_access_token(user_id="u1", tenant_id="t1", role=Role.VIEWER, settings=other)
    with pytest.raises(TokenError):
        decode_access_token(token, settings)


def test_refresh_token_type_is_rejected_at_access_boundary():
    """A refresh token must never work where an access token is expected."""
    settings = _settings()
    now = int(time.time())
    refresh_shaped = {
        "sub": "u1", "tenant_id": "t1", "role": "VIEWER", "type": "refresh",
        "iat": now, "exp": now + 3600,
    }
    token = pyjwt.encode(refresh_shaped, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(TokenError, match="not an access token"):
        decode_access_token(token, settings)


@pytest.mark.parametrize("role", list(Role))
def test_every_role_can_read_forecasts(role: Role):
    """Baseline: whatever else changes, every role can at least read a
    forecast -- this is the platform's core deliverable.
    """
    assert role_has_permission(role, "forecast:read")


def test_only_admin_roles_can_manage_users():
    assert role_has_permission(Role.SUPER_ADMIN, "user:manage")
    assert role_has_permission(Role.TENANT_ADMIN, "user:manage")
    assert not role_has_permission(Role.OPS_ANALYST, "user:manage")
    assert not role_has_permission(Role.VIEWER, "user:manage")


def test_viewer_has_no_write_permissions():
    viewer_perms = PERMISSIONS[Role.VIEWER]
    assert not any(p.endswith(":manage") or p.endswith(":write") for p in viewer_perms)
