"""The RBAC matrix test (PLAN §3.1 / §6.4 D3) against the Identity service's real ASGI
app, in-process (no running server needed). Confirms Layer 2 (role re-check) AND
Layer 3 (row ownership) are enforced, not merely assumed — including the two tests
that prove defense-in-depth is real rather than a diagram: a forged internal
assertion is rejected, and a valid internal assertion is honoured independently of
any JWT.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from asgi_lifespan import LifespanManager

from libs.common.config import get_settings
from libs.security.internal_assertion import sign_principal
from libs.security.principal import Principal, Role
from services.identity.main import create_app


def _uid(phone: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, phone))


RIDER_A_PHONE = "+919000000001"  # seeded rider (tools/seed.py)
RIDER_B_PHONE = "+919000000002"
DRIVER_PHONE = "+918000000001"
ADMIN_PHONE = "+910000000001"


@pytest.fixture
async def client():
    app = create_app()
    async with LspanCompat(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


class LspanCompat(LifespanManager):
    """Thin rename so the fixture reads clearly; LifespanManager already does exactly
    what's needed (drives FastAPI's startup/shutdown lifespan for the in-process
    ASGI transport)."""


async def _login(client: httpx.AsyncClient, phone: str) -> str:
    r = await client.post("/v1/auth/otp/request", json={"phone": phone})
    assert r.status_code == 200, r.text
    code = r.json()["dev_code"]
    r = await client.post("/v1/auth/otp/verify", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_anonymous_is_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/users/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_rider_can_read_own_profile(client: httpx.AsyncClient) -> None:
    token = await _login(client, RIDER_A_PHONE)
    r = await client.get(f"/v1/users/{_uid(RIDER_A_PHONE)}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["phone"] == RIDER_A_PHONE


@pytest.mark.asyncio
async def test_rider_cannot_read_another_riders_profile(client: httpx.AsyncClient) -> None:
    token = await _login(client, RIDER_A_PHONE)
    r = await client.get(f"/v1/users/{_uid(RIDER_B_PHONE)}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_driver_cannot_read_another_drivers_profile(client: httpx.AsyncClient) -> None:
    token = await _login(client, DRIVER_PHONE)
    other_driver_phone = "+918000000002"
    r = await client.get(f"/v1/drivers/{_uid(other_driver_phone)}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_read_any_profile(client: httpx.AsyncClient) -> None:
    token = await _login(client, ADMIN_PHONE)
    r = await client.get(f"/v1/users/{_uid(RIDER_A_PHONE)}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_non_admin_forbidden_from_kyc_endpoint(client: httpx.AsyncClient) -> None:
    rider_token = await _login(client, RIDER_A_PHONE)
    r = await client.patch(
        f"/v1/drivers/{_uid(DRIVER_PHONE)}/kyc",
        json={"verified": True},
        headers={"Authorization": f"Bearer {rider_token}"},
    )
    assert r.status_code == 403

    driver_token = await _login(client, DRIVER_PHONE)
    r = await client.patch(
        f"/v1/drivers/{_uid(DRIVER_PHONE)}/kyc",
        json={"verified": True},
        headers={"Authorization": f"Bearer {driver_token}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_use_kyc_endpoint(client: httpx.AsyncClient) -> None:
    admin_token = await _login(client, ADMIN_PHONE)
    r = await client.patch(
        f"/v1/drivers/{_uid(DRIVER_PHONE)}/kyc",
        json={"verified": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["kyc_verified"] is True


@pytest.mark.asyncio
async def test_forged_internal_principal_header_is_401(client: httpx.AsyncClient) -> None:
    """PLAN §6.4 D3, test 3: a forged/unsigned X-Internal-Principal reaching a service
    directly must fail closed. This is what proves Layer 2 is real independent
    verification and not merely reading a header the gateway is trusted to have set
    correctly."""
    r = await client.get(
        f"/v1/users/{_uid(RIDER_A_PHONE)}", headers={"X-Internal-Principal": "forged.notavalidsignature"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_valid_internal_principal_assertion_is_honoured(client: httpx.AsyncClient) -> None:
    """A properly HMAC-signed internal assertion — what the gateway actually
    produces after verifying a user's JWT — must be accepted on its own, with no
    Authorization header at all, proving the internal-call path genuinely works end
    to end and independently of the JWT path."""
    settings = get_settings()
    admin_principal = Principal(user_id=_uid(ADMIN_PHONE), role=Role.ADMIN, phone=ADMIN_PHONE)
    signed = sign_principal(
        admin_principal, secret=settings.internal_assertion_secret, ttl_seconds=30, request_id="test-req"
    )
    r = await client.get(f"/v1/users/{_uid(RIDER_A_PHONE)}", headers={"X-Internal-Principal": signed})
    assert r.status_code == 200
