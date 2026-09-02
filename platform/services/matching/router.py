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
from libs.geo.city_config_repo import get_dispatch_config
from libs.security.principal import Role
from libs.security.rbac import require_roles
from services.matching.claim import DriverClaimService
from services.matching.container import get_dispatcher_for_city, get_redis, get_session
from services.matching.schemas import DispatchRequest, DispatchResponse, ReleaseClaimRequest

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
    # Round 12 stakeholder council: radius/count/claim-TTL are now DB-backed and
    # per-city (AS-05), fetched fresh on every dispatch call rather than baked into
    # the cached Dispatcher -- see DriverClaimService.try_claim's docstring for why
    # a cached-forever value would have silently defeated live admin edits.
    dispatch_cfg = await get_dispatch_config(session, body.city_id)
    result = await dispatcher.dispatch_once(
        session,
        trip_id=body.trip_id,
        city=city,
        pickup_lat=body.pickup_lat,
        pickup_lng=body.pickup_lng,
        excluded_driver_ids=set(body.excluded_driver_ids),
        radius_km=dispatch_cfg.candidate_radius_km,
        count=dispatch_cfg.candidate_count,
        at=datetime.now(timezone.utc),
        vehicle_type=body.vehicle_type,
        claim_ttl_seconds=dispatch_cfg.claim_ttl_seconds,
    )
    return DispatchResponse(**result.__dict__)


@router.post("/v1/matching/release-claim")
async def release_claim(
    body: ReleaseClaimRequest,
    _principal=Depends(require_roles(Role.ADMIN)),
):
    """Explicit claim release (fixes a real bug found in testing tonight): a
    driver's Layer-1 claim (claim.py) previously only expired via TTL, meaning a
    driver who accepted-then-completed a ride stayed effectively unmatchable for up
    to `claim_ttl_seconds` afterward even though driver_profiles.status had already
    correctly returned to ONLINE. Trip service calls this immediately after ACCEPT,
    REJECT, and every redispatch round that clears an old offer, so a driver's claim
    lifetime tracks their ACTUAL offer lifecycle rather than a fixed timer once the
    outcome is already known.

    No TTL needed here (Round 12) -- release is a compare-and-delete (see
    claim.py), never a SET, so there's nothing for a TTL to apply to.
    """
    claims = DriverClaimService(get_redis())
    await claims.release(body.driver_id, body.trip_id)
    return {"released": True}
