"""The one HTTP hop from Trip to Matching per dispatch round — a single request, not
a per-candidate call, so this respects PLAN A1 (zero network I/O *inside* the scoring
loop) while still being a real inter-service call over the wire (PLAN §4.1: REST over
async httpx, chosen for demo inspectability).

Authenticates as a system principal via the same signed internal assertion the
gateway would produce (PLAN §3.1 Layer 2) — using role=ADMIN as tonight's stand-in for
a dedicated SYSTEM role (GAP note in PROGRESS.md).
"""
from __future__ import annotations

import httpx

from libs.common.ids import new_id
from libs.security.internal_assertion import sign_principal
from libs.security.principal import Principal, Role

SYSTEM_PRINCIPAL_ID = "00000000-0000-0000-0000-000000000000"


class MatchingClient:
    def __init__(self, base_url: str, internal_assertion_secret: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = internal_assertion_secret

    def _system_header(self) -> dict[str, str]:
        principal = Principal(user_id=SYSTEM_PRINCIPAL_ID, role=Role.ADMIN, phone="")
        token = sign_principal(principal, secret=self._secret, ttl_seconds=30, request_id=new_id())
        return {"X-Internal-Principal": token}

    async def dispatch(
        self, *, trip_id: str, city_id: str, pickup_lat: float, pickup_lng: float, excluded_driver_ids: list[str],
        vehicle_type: str = "SEDAN",
    ) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{self._base_url}/v1/matching/dispatch",
                json={
                    "trip_id": trip_id, "city_id": city_id, "pickup_lat": pickup_lat, "pickup_lng": pickup_lng,
                    "excluded_driver_ids": excluded_driver_ids, "vehicle_type": vehicle_type,
                },
                headers=self._system_header(),
            )
            resp.raise_for_status()
            return resp.json()

    async def release_claim(self, *, driver_id: str, trip_id: str) -> None:
        """Called on ACCEPT, REJECT, and every redispatch round that clears a prior
        offer — see the docstring on the matching-side endpoint for why this must be
        explicit rather than left to the claim's TTL."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{self._base_url}/v1/matching/release-claim",
                json={"driver_id": driver_id, "trip_id": trip_id},
                headers=self._system_header(),
            )
            resp.raise_for_status()
