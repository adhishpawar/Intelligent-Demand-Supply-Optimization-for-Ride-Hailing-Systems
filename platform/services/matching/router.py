"""The matching service's only endpoint. Called by the Trip service's dispatch
orchestrator — internal, service-to-service, hence `require_roles(ADMIN)` (tonight's
stand-in for a dedicated SYSTEM role; the Trip service authenticates via a signed
internal principal assertion carrying role=ADMIN for its own system identity, since
`libs.security.principal.Role` doesn't have a distinct SYSTEM value tonight — see
PROGRESS.md GAP note).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from libs.common.config import Settings, get_settings
from libs.geo.cities import get_city
from libs.security.principal import Role
from libs.security.rbac import require_roles
from services.matching.container import get_dispatcher_for_city, get_session
from services.matching.schemas import DispatchRequest, DispatchResponse

router = APIRouter()


@router.post("/v1/matching/dispatch", response_model=DispatchResponse)
async def dispatch(
    body: DispatchRequest,
    _principal=Depends(require_roles(Role.ADMIN)),
    settings: Settings = Depends(get_settings),
    session=Depends(get_session),
):
    city = get_city(body.city_id)
    dispatcher = get_dispatcher_for_city(body.city_id, settings)
    result = await dispatcher.dispatch_once(
        session,
        trip_id=body.trip_id,
        city=city,
        pickup_lat=body.pickup_lat,
        pickup_lng=body.pickup_lng,
        excluded_driver_ids=set(body.excluded_driver_ids),
        radius_km=settings.candidate_radius_km,
        count=settings.candidate_count,
        at=datetime.now(timezone.utc),
    )
    return DispatchResponse(**result.__dict__)
