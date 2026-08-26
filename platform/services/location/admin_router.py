"""Admin-only aggregate view: the live driver heatmap. Real data straight from the
Redis geo index (PLAN §6.4 D5: "the admin console shows raw ingest counts, GEOPOS...
so the demo is inspectable, not merely watchable") — no separate analytics pipeline,
just a direct read of the same structures the matching hot path uses.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from libs.geo.cities import CITIES
from libs.geo.driver_index import DriverIndexRepository
from libs.security.principal import Role
from libs.security.rbac import require_roles
from services.location.container import get_location_service

router = APIRouter()


@router.get("/v1/admin/heatmap/{city_id}")
async def heatmap(
    city_id: str, _principal=Depends(require_roles(Role.ADMIN)), svc=Depends(get_location_service)
):
    if city_id not in CITIES:
        return {"city_id": city_id, "drivers": []}
    index: DriverIndexRepository = svc.index
    online_ids = await index.list_online_driver_ids()
    drivers = []
    for driver_id in online_ids:
        state = await index.get_driver_state(driver_id)
        if state and state.get("city_id") == city_id and "lat" in state:
            drivers.append({
                "driver_id": driver_id, "lat": float(state["lat"]), "lng": float(state["lng"]),
                "status": state.get("status"), "current_trip_id": state.get("current_trip_id"),
            })
    return {"city_id": city_id, "drivers": drivers, "online_count": len(online_ids)}
