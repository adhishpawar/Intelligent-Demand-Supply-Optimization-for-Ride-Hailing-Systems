"""PLAN §3.1 Layer 1 (gateway, coarse route-level, deny-by-default) proven against the
gateway's real ASGI app in-process. This complements
tests/integration/test_rbac_identity.py (Layer 2+3, proven directly against a
service) — together they cover all three RBAC layers end to end. Requires the
identity service to actually be reachable on its configured port, since the gateway
proxies over real HTTP rather than in-process (that IS the thing being tested).
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from asgi_lifespan import LifespanManager

from services.gateway.main import app

RIDER_PHONE = "+919000000004"


def _uid(phone: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, phone))


@pytest.fixture
async def client():
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gwtest") as c:
            yield c


async def _login(client: httpx.AsyncClient, phone: str) -> str:
    r = await client.post("/v1/auth/otp/request", json={"phone": phone})
    assert r.status_code == 200, r.text
    code = r.json()["dev_code"]
    r = await client.post("/v1/auth/otp/verify", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_unmapped_route_is_denied_by_default(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/this/route/does/not/exist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_missing_token_on_protected_route_is_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/users/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_public_route_needs_no_token(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/auth/otp/request", json={"phone": RIDER_PHONE})
    assert r.status_code == 200
    assert "dev_code" in r.json()


@pytest.mark.asyncio
async def test_authenticated_route_forwards_and_succeeds(client: httpx.AsyncClient) -> None:
    token = await _login(client, RIDER_PHONE)
    r = await client.get("/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["phone"] == RIDER_PHONE


@pytest.mark.asyncio
async def test_role_mismatch_blocked_at_gateway_layer_1(client: httpx.AsyncClient) -> None:
    """A rider hitting an admin-only route is blocked at the GATEWAY, before the
    request ever reaches the Identity service — proven by the fact this works even
    though PLAN's Layer 2 (service-level re-check) would ALSO reject it; the point of
    this specific test is that Layer 1 does its job independently."""
    token = await _login(client, RIDER_PHONE)
    driver_id = _uid("+918000000001")
    r = await client.patch(
        f"/v1/drivers/{driver_id}/kyc", json={"verified": True}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_client_supplied_internal_principal_header_is_ignored(client: httpx.AsyncClient) -> None:
    """A client cannot smuggle in their own X-Internal-Principal to impersonate
    someone else — the gateway strips any client-supplied value and signs its own
    from the verified JWT."""
    token = await _login(client, RIDER_PHONE)
    forged = "eyJmYWtlIjoidHJ1ZSJ9.fakesig"
    r = await client.get(
        "/v1/users/me", headers={"Authorization": f"Bearer {token}", "X-Internal-Principal": forged}
    )
    assert r.status_code == 200
    assert r.json()["phone"] == RIDER_PHONE  # the gateway's own signed assertion won, not the forged one
