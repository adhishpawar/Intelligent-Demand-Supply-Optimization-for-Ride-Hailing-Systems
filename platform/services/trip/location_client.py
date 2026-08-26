"""Trip -> Location: the one place Trip tells Location a driver's operational status
changed (ON_TRIP on accept, ONLINE again on completion/cancellation). Same signed
system-principal pattern as matching_client.py.
"""
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


async def _set_status(settings: Settings, driver_id: str, status: str, trip_id: str | None = None) -> None:
    url = f"http://localhost:{settings.location_port}/v1/drivers/{driver_id}/status"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.patch(url, json={"status": status, "trip_id": trip_id}, headers=_system_header(settings))
        resp.raise_for_status()


async def set_driver_on_trip(settings: Settings, driver_id: str, trip_id: str) -> None:
    """Sets current_trip_id in the driver's Redis hash so Location can live-stream
    every subsequent GPS ping straight to this trip's WS channel (see
    services/location/service.py's ingest_ping)."""
    await _set_status(settings, driver_id, "ON_TRIP", trip_id)


async def set_driver_online_again(settings: Settings, driver_id: str) -> None:
    await _set_status(settings, driver_id, "ONLINE", trip_id=None)
