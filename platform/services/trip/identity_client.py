"""Trip -> Identity: the one place Trip tells Identity a driver actually completed a
ride. Same signed system-principal pattern as location_client.py / matching_client.py.

Round 2 stakeholder council finding: `driver_profiles.rides_completed` was defined,
seeded, read by every driver-profile endpoint, and (as of Round 2 item 2) displayed
in the driver console -- but nothing anywhere ever incremented it. This closes that
gap at the one moment that's semantically correct: trip completion, independent of
whether the rider ever rates the ride.
"""
from __future__ import annotations

import httpx

from libs.common.config import Settings
from libs.common.ids import new_id
from libs.security.internal_assertion import sign_principal
from libs.security.principal import Principal, Role

SYSTEM_PRINCIPAL_ID = "00000000-0000-0000-0000-000000000000"


async def mark_ride_completed(settings: Settings, driver_id: str) -> None:
    principal = Principal(user_id=SYSTEM_PRINCIPAL_ID, role=Role.ADMIN, phone="")
    token = sign_principal(principal, secret=settings.internal_assertion_secret, ttl_seconds=30, request_id=new_id())
    url = f"http://localhost:{settings.identity_port}/v1/drivers/{driver_id}/complete-ride"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(url, headers={"X-Internal-Principal": token})
        resp.raise_for_status()
