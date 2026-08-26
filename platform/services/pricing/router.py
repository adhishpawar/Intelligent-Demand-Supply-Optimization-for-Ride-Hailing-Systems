from __future__ import annotations

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from libs.common.errors import ConflictError
from libs.common.ids import utcnow
from libs.geo.cities import CITIES, city_for
from libs.geo.city_config_repo import (
    get_dispatch_config_dict,
    get_rate_card,
    get_rate_card_dict,
    update_dispatch_config,
    update_rate_card,
)
from libs.pricing.vehicle_type_repo import (
    get_vehicle_type_multiplier,
    get_vehicle_type_multipliers_dict,
    update_vehicle_type_multiplier,
)
from libs.geo.eta import FastEtaEstimator
from libs.geo.geohash import encode as geohash_encode
from libs.pricing.fare import estimate_fare
from libs.pricing.surge_reader import read_surge_multiplier, surge_key
from libs.security.principal import Principal, Role
from libs.security.rbac import get_principal, require_roles
from services.pricing.container import get_redis, get_session
from services.pricing.schemas import (
    CityConfigResponse,
    CityConfigUpdateRequest,
    DispatchConfigResponse,
    DispatchConfigUpdateRequest,
    EstimateRequest,
    EstimateResponse,
    SurgeResponse,
    VehicleTypeMultiplierUpdateRequest,
    VehicleTypeMultipliersResponse,
)

router = APIRouter()


@router.post("/v1/pricing/estimate", response_model=EstimateResponse)
async def estimate(
    body: EstimateRequest, _principal: Principal = Depends(get_principal), redis: Redis = Depends(get_redis),
    session=Depends(get_session),
):
    city = city_for(body.pickup.lat, body.pickup.lng)
    if city is None:
        raise ConflictError("pickup location is outside any served city")

    eta_estimator = FastEtaEstimator(city.speed_profile)
    distance_m = eta_estimator.distance_m(body.pickup.lat, body.pickup.lng, body.drop.lat, body.drop.lng)
    duration_s = eta_estimator.eta_seconds(body.pickup.lat, body.pickup.lng, body.drop.lat, body.drop.lng, utcnow())
    pickup_geohash = geohash_encode(body.pickup.lat, body.pickup.lng)
    surge = await read_surge_multiplier(redis, city.city_id, pickup_geohash)
    # Round 1 stakeholder council: the rate card is DB-backed and admin-editable
    # (city_configs), not the hardcoded libs.geo.cities default.
    rate_card = await get_rate_card(session, city.city_id)
    vt_multiplier = await get_vehicle_type_multiplier(session, body.vehicle_type)
    fare = estimate_fare(rate_card, distance_m, duration_s, surge, vt_multiplier)

    # eta_pickup: how long until a driver could reach the pickup point -- a rough
    # city-average estimate tonight (a real pickup ETA needs the nearest candidate's
    # position, which is the matching service's job, not pricing's).
    eta_pickup = 180.0

    return EstimateResponse(
        fare_estimate=fare, eta_pickup_seconds=eta_pickup, distance_m=distance_m, duration_s=duration_s,
        surge_multiplier=surge, city_id=city.city_id,
    )


@router.get("/v1/pricing/surge/{city_id}", response_model=SurgeResponse)
async def get_surge(city_id: str, _principal: Principal = Depends(get_principal), redis: Redis = Depends(get_redis)):
    if city_id not in CITIES:
        raise ConflictError("unknown city_id", city_id=city_id)
    keys = await redis.keys(f"surge:{city_id}:*")
    cells = {}
    for key in keys:
        cell = key.split(":")[-1]
        value = await redis.get(key)
        if value is not None:
            cells[cell] = float(value)
    return SurgeResponse(city_id=city_id, cells=cells)


# --- Round 1 stakeholder council: ops + tech-lead both flagged the same gap from
# different angles (business rules hardcoded in Python vs. "this is a support fire
# drill waiting to happen") -- exposed here as a real, DB-backed, audited config the
# admin console can view and edit without a deploy. ---

@router.get("/v1/pricing/config/{city_id}", response_model=CityConfigResponse)
async def get_city_config(
    city_id: str, _principal: Principal = Depends(get_principal), session=Depends(get_session),
):
    if city_id not in CITIES:
        raise ConflictError("unknown city_id", city_id=city_id)
    card = await get_rate_card_dict(session, city_id)
    return CityConfigResponse(city_id=city_id, **card)


@router.patch("/v1/pricing/config/{city_id}", response_model=CityConfigResponse)
async def update_city_config(
    city_id: str, body: CityConfigUpdateRequest,
    principal: Principal = Depends(require_roles(Role.ADMIN)),
    session=Depends(get_session),
):
    if city_id not in CITIES:
        raise ConflictError("unknown city_id", city_id=city_id)
    updated = await update_rate_card(session, city_id, body.model_dump(exclude_none=True), principal.user_id)
    return CityConfigResponse(city_id=city_id, **updated)


# --- Round 12 stakeholder council: closes (most of) PLAN's own AS-05 gap --
# dispatch tuning (offer/claim TTLs, matching deadline, candidate radius/count) as
# real, DB-backed, admin-editable, audited config, same pattern as the rate card
# immediately above. max_dispatch_attempts deliberately stays a global Settings
# value -- see libs/geo/cities.py's DispatchConfig docstring for why. ---

@router.get("/v1/pricing/dispatch-config/{city_id}", response_model=DispatchConfigResponse)
async def get_dispatch_config_endpoint(
    city_id: str, _principal: Principal = Depends(get_principal), session=Depends(get_session),
):
    if city_id not in CITIES:
        raise ConflictError("unknown city_id", city_id=city_id)
    cfg = await get_dispatch_config_dict(session, city_id)
    return DispatchConfigResponse(city_id=city_id, **cfg)


@router.patch("/v1/pricing/dispatch-config/{city_id}", response_model=DispatchConfigResponse)
async def update_dispatch_config_endpoint(
    city_id: str, body: DispatchConfigUpdateRequest,
    principal: Principal = Depends(require_roles(Role.ADMIN)),
    session=Depends(get_session),
):
    if city_id not in CITIES:
        raise ConflictError("unknown city_id", city_id=city_id)
    updated = await update_dispatch_config(session, city_id, body.model_dump(exclude_none=True), principal.user_id)
    return DispatchConfigResponse(city_id=city_id, **updated)


# --- Round 8 stakeholder council: closes Round 7's "REAL-LITE, hardcoded" note on
# vehicle-type fare multipliers -- same DB-backed/admin-editable/audited pattern as
# the city rate card immediately above. ---

@router.get("/v1/pricing/vehicle-multipliers", response_model=VehicleTypeMultipliersResponse)
async def get_vehicle_multipliers(_principal: Principal = Depends(get_principal), session=Depends(get_session)):
    return VehicleTypeMultipliersResponse(multipliers=await get_vehicle_type_multipliers_dict(session))


@router.patch("/v1/pricing/vehicle-multipliers", response_model=VehicleTypeMultipliersResponse)
async def update_vehicle_multiplier(
    body: VehicleTypeMultiplierUpdateRequest,
    principal: Principal = Depends(require_roles(Role.ADMIN)),
    session=Depends(get_session),
):
    await update_vehicle_type_multiplier(session, body.vehicle_type, body.multiplier, principal.user_id)
    return VehicleTypeMultipliersResponse(multipliers=await get_vehicle_type_multipliers_dict(session))
