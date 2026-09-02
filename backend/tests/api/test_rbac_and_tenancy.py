"""
RBAC and multi-tenancy enforcement tests (closes TEST-04: 'tenant A cannot
read tenant B').

These are the tests the mandate explicitly asks for under "Multi-Tenancy":
proof, not a schema constraint taken on faith.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register_and_login(client: AsyncClient, slug: str, email: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/register-tenant",
        json={
            "tenant_name": slug,
            "tenant_slug": slug,
            "admin_email": email,
            "admin_password": "SuperSecret123!",
            "admin_full_name": "Admin",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_tenant_admin_can_create_city_and_analyst_can_read_it(client: AsyncClient):
    tenant_a = await _register_and_login(client, "rbac-a", "admin@rbac-a.com")

    resp = await client.post(
        "/api/v1/cities",
        json={"name": "Metropolis", "slug": "metro", "timezone": "UTC"},
        headers=_auth(tenant_a),
    )
    assert resp.status_code == 201, resp.text

    analyst_create = await client.post(
        "/api/v1/users",
        json={
            "email": "analyst@rbac-a.com",
            "password": "AnalystPass123!",
            "full_name": "Ann Alyst",
            "role": "OPS_ANALYST",
        },
        headers=_auth(tenant_a),
    )
    assert analyst_create.status_code == 201

    analyst_login = await client.post(
        "/api/v1/auth/login", json={"email": "analyst@rbac-a.com", "password": "AnalystPass123!"}
    )
    analyst_tokens = analyst_login.json()

    read_resp = await client.get("/api/v1/cities", headers=_auth(analyst_tokens))
    assert read_resp.status_code == 200
    assert len(read_resp.json()) == 1
    assert read_resp.json()[0]["slug"] == "metro"


async def test_ops_analyst_cannot_manage_users(client: AsyncClient):
    tenant = await _register_and_login(client, "rbac-b", "admin@rbac-b.com")
    await client.post(
        "/api/v1/users",
        json={
            "email": "analyst@rbac-b.com",
            "password": "AnalystPass123!",
            "full_name": "Ann",
            "role": "OPS_ANALYST",
        },
        headers=_auth(tenant),
    )
    analyst_tokens = (
        await client.post(
            "/api/v1/auth/login", json={"email": "analyst@rbac-b.com", "password": "AnalystPass123!"}
        )
    ).json()

    resp = await client.post(
        "/api/v1/users",
        json={"email": "x@rbac-b.com", "password": "Pass1234!", "full_name": "X", "role": "VIEWER"},
        headers=_auth(analyst_tokens),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_viewer_cannot_create_city(client: AsyncClient):
    tenant = await _register_and_login(client, "rbac-c", "admin@rbac-c.com")
    await client.post(
        "/api/v1/users",
        json={
            "email": "viewer@rbac-c.com",
            "password": "ViewerPass123!",
            "full_name": "V",
            "role": "VIEWER",
        },
        headers=_auth(tenant),
    )
    viewer_tokens = (
        await client.post(
            "/api/v1/auth/login", json={"email": "viewer@rbac-c.com", "password": "ViewerPass123!"}
        )
    ).json()

    resp = await client.post(
        "/api/v1/cities", json={"name": "X", "slug": "x", "timezone": "UTC"}, headers=_auth(viewer_tokens)
    )
    assert resp.status_code == 403


async def test_tenant_a_cannot_list_tenant_b_users(client: AsyncClient):
    """The core tenant-isolation claim: even a valid, active token for tenant A
    must never see tenant B's rows -- not via a 403, but via them simply not
    appearing, proving the repository layer filters by tenant_id rather than
    trusting a request parameter.
    """
    tenant_a = await _register_and_login(client, "iso-a", "admin@iso-a.com")
    tenant_b = await _register_and_login(client, "iso-b", "admin@iso-b.com")

    await client.post(
        "/api/v1/users",
        json={"email": "extra@iso-b.com", "password": "Pass1234!", "full_name": "Extra", "role": "VIEWER"},
        headers=_auth(tenant_b),
    )

    resp_a = await client.get("/api/v1/users", headers=_auth(tenant_a))
    assert resp_a.status_code == 200
    emails_a = {u["email"] for u in resp_a.json()["items"]}
    assert emails_a == {"admin@iso-a.com"}
    assert "extra@iso-b.com" not in emails_a
    assert "admin@iso-b.com" not in emails_a


async def test_tenant_a_cannot_list_tenant_b_cities(client: AsyncClient):
    tenant_a = await _register_and_login(client, "iso-city-a", "admin@iso-city-a.com")
    tenant_b = await _register_and_login(client, "iso-city-b", "admin@iso-city-b.com")

    await client.post(
        "/api/v1/cities",
        json={"name": "Tenant B City", "slug": "b-city", "timezone": "UTC"},
        headers=_auth(tenant_b),
    )

    resp = await client.get("/api/v1/cities", headers=_auth(tenant_a))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_tenant_a_cannot_fetch_tenant_b_city_by_guessed_id(client: AsyncClient):
    """Guessing another tenant's UUID must not work even for a direct GET,
    proving isolation is enforced by tenant_id filtering, not merely by never
    listing the other tenant's rows in an index endpoint.
    """
    tenant_a = await _register_and_login(client, "iso-guess-a", "admin@iso-guess-a.com")
    tenant_b = await _register_and_login(client, "iso-guess-b", "admin@iso-guess-b.com")

    create_resp = await client.post(
        "/api/v1/cities",
        json={"name": "Secret City", "slug": "secret", "timezone": "UTC"},
        headers=_auth(tenant_b),
    )
    city_id = create_resp.json()["id"]

    zones_resp = await client.get(f"/api/v1/cities/{city_id}/zones", headers=_auth(tenant_a))
    assert zones_resp.status_code == 404
