from __future__ import annotations

import httpx

from libs.common.config import Settings
from libs.common.ids import new_id
from libs.security.internal_assertion import sign_principal
from libs.security.principal import Principal, Role

SYSTEM_PRINCIPAL_ID = "00000000-0000-0000-0000-000000000000"


def _system_header(settings: Settings) -> dict[str, str]:
    principal = Principal(user_id=SYSTEM_PRINCIPAL_ID, role=Role.ADMIN, phone="")
    token = sign_principal(principal, secret=settings.internal_assertion_secret, ttl_seconds=30, request_id=new_id())
    return {"X-Internal-Principal": token}


async def get_trip(settings: Settings, trip_id: str) -> dict:
    url = f"http://localhost:{settings.trip_port}/v1/trips/{trip_id}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url, headers=_system_header(settings))
        resp.raise_for_status()
        return resp.json()


async def annotate_rating(settings: Settings, trip_id: str, rater_role: str, rater_id: str) -> None:
    url = f"http://localhost:{settings.trip_port}/v1/trips/{trip_id}/rate-annotation"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(url, json={"rater_role": rater_role, "rater_id": rater_id}, headers=_system_header(settings))
        resp.raise_for_status()
