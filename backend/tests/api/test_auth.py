from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, slug: str = "acme", email: str = "owner@acme-corp.com") -> dict:
    resp = await client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_name": "Acme Corp",
            "tenant_slug": slug,
            "admin_email": email,
            "admin_password": "SuperSecret123!",
            "admin_full_name": "Ada Min",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_register_tenant_issues_tokens(client: AsyncClient):
    body = await _register(client)
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"


async def test_duplicate_tenant_slug_is_conflict(client: AsyncClient):
    await _register(client, slug="dupe", email="a@dupe-corp.com")
    resp = await client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_name": "Dupe Corp",
            "tenant_slug": "dupe",
            "admin_email": "b@dupe-corp.com",
            "admin_password": "SuperSecret123!",
            "admin_full_name": "B",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_login_success(client: AsyncClient):
    await _register(client, slug="login-co", email="user@login-co.com")
    resp = await client.post(
        "/api/v1/auth/login", json={"email": "user@login-co.com", "password": "SuperSecret123!"}
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_login_wrong_password_is_unauthorized(client: AsyncClient):
    await _register(client, slug="wp-co", email="user@wp-co.com")
    resp = await client.post("/api/v1/auth/login", json={"email": "user@wp-co.com", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


async def test_login_unknown_email_is_unauthorized_not_not_found(client: AsyncClient):
    """Must not leak whether an email exists via a different status code."""
    resp = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@nowhere.com", "password": "whatever"}
    )
    assert resp.status_code == 401


async def test_me_requires_bearer_token(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


async def test_me_returns_authenticated_user(client: AsyncClient):
    tokens = await _register(client, slug="me-co", email="user@me-co.com")
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "user@me-co.com"
    assert resp.json()["role"] == "TENANT_ADMIN"


async def test_refresh_rotates_token_and_old_one_is_dead(client: AsyncClient):
    tokens = await _register(client, slug="refresh-co", email="user@refresh-co.com")
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # The original refresh token was single-use; reusing it must fail.
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401


async def test_logout_revokes_refresh_token(client: AsyncClient):
    tokens = await _register(client, slug="logout-co", email="user@logout-co.com")
    resp = await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 204

    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401


async def test_deactivated_user_cannot_login(client: AsyncClient):
    tokens = await _register(client, slug="deact-co", email="admin@deact-co.com")
    admin_headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    me = (await client.get("/api/v1/auth/me", headers=admin_headers)).json()

    # Admin deactivates themself, then can no longer log in.
    resp = await client.patch(
        f"/api/v1/users/{me['id']}/status", json={"is_active": False}, headers=admin_headers
    )
    assert resp.status_code == 200

    login = await client.post(
        "/api/v1/auth/login", json={"email": "admin@deact-co.com", "password": "SuperSecret123!"}
    )
    assert login.status_code == 401


async def test_malformed_request_returns_single_error_envelope(client: AsyncClient):
    """API-02/API-03: no field, or the wrong type, is a 422 in the standard
    envelope -- never a bare 500 or an HTML page (what the original Flask app
    did for `KeyError` on a missing field).
    """
    resp = await client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "request_id" in body["error"]
