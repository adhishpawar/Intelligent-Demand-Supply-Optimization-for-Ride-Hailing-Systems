"""Note on service boundaries: `go_online` needs the driver's registered city_id,
which lives in `driver_profiles` (owned, schema-wise, by the Identity service). Rather
than an HTTP hop to Identity for one column on a call that must stay fast, this reads
that column directly via SQL. All services share one physical Postgres instance
tonight, so this is a pragmatic, narrow, and explicitly acknowledged boundary
compromise, not a hidden one — logged as GAP-LOC-01 in PROGRESS.md. A stricter
deployment would put `city_id` in the JWT claims at login (it barely changes) or add
an internal `GET /internal/drivers/{id}/city` endpoint on Identity.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from libs.common.errors import ForbiddenError
from libs.security.principal import Principal, Role
from libs.security.rbac import require_roles
from services.location.container import get_history_repo, get_location_service, get_session
from services.location.repository import PgLocationHistoryRepository, get_driver_city
from services.location.schemas import (
    DriverLiveStateResponse,
    LocationPingRequest,
    LocationPingResponse,
    StatusUpdateRequest,
    StatusUpdateResponse,
)
from services.location.service import LocationService

router = APIRouter()


async def _record_history(repo: PgLocationHistoryRepository, driver_id: str, lat: float, lng: float) -> None:
    await repo.record(driver_id, lat, lng, trip_id=None)


@router.patch("/v1/drivers/{driver_id}/status", response_model=StatusUpdateResponse)
async def update_status(
    driver_id: str,
    body: StatusUpdateRequest,
    principal: Principal = Depends(require_roles(Role.DRIVER, Role.ADMIN)),
    svc: LocationService = Depends(get_location_service),
    session=Depends(get_session),
):
    if principal.role == Role.DRIVER and principal.user_id != driver_id:
        raise ForbiddenError("a driver can only change their own status")

    city_id = await get_driver_city(session, driver_id) or ""
    seq = await svc.go_online_or_offline(driver_id, body.status, city_id)
    return StatusUpdateResponse(driver_id=driver_id, status=body.status, city_id=city_id, state_seq=seq)


@router.patch("/v1/drivers/{driver_id}/location", response_model=LocationPingResponse)
async def ping_location(
    driver_id: str,
    body: LocationPingRequest,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(require_roles(Role.DRIVER, Role.ADMIN)),
    svc: LocationService = Depends(get_location_service),
    history_repo: PgLocationHistoryRepository = Depends(get_history_repo),
):
    if principal.role == Role.DRIVER and principal.user_id != driver_id:
        raise ForbiddenError("a driver can only report their own location")

    accepted, reason = await svc.ingest_ping(driver_id, body.lat, body.lng, body.heading, body.speed_kmh)
    if accepted:
        background_tasks.add_task(_record_history, history_repo, driver_id, body.lat, body.lng)
    return LocationPingResponse(accepted=accepted, reason=reason)


@router.get("/v1/drivers/{driver_id}/live", response_model=DriverLiveStateResponse)
async def get_live_state(
    driver_id: str,
    principal: Principal = Depends(require_roles(Role.DRIVER, Role.ADMIN, Role.RIDER)),
    svc: LocationService = Depends(get_location_service),
):
    state = await svc.get_live_state(driver_id)
    if not state:
        return DriverLiveStateResponse(driver_id=driver_id, status=None)
    return DriverLiveStateResponse(
        driver_id=driver_id,
        status=state.get("status"),
        lat=float(state["lat"]) if "lat" in state else None,
        lng=float(state["lng"]) if "lng" in state else None,
        heading=float(state["heading"]) if "heading" in state else None,
        speed_kmh=float(state["speed_kmh"]) if "speed_kmh" in state else None,
        last_ping_ts=float(state["last_ping_ts"]) if "last_ping_ts" in state else None,
    )
