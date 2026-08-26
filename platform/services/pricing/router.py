from __future__ import annotations

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from libs.common.errors import ConflictError
from libs.common.ids import utcnow
from libs.geo.cities import CITIES, city_for
from libs.geo.city_config_repo import get_rate_card, get_rate_card_dict, update_rate_card
from libs.geo.eta import FastEtaEstimator
from libs.geo.geohash import encode as geohash_encode
from libs.pricing.fare import estimate_fare
from libs.pricing.surge_reader import read_surge_multiplier, surge_key
from libs.security.principal import Principal, Role
from libs.security.rbac import get_principal, require_roles
from services.pricing.container import get_redis, get_session
from services.pricing.schemas import CityConfigResponse, CityConfigUpdateRequest, EstimateRequest, EstimateResponse, SurgeResponse

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
    fare = estimate_fare(rate_card, distance_m, duration_s, surge, body.vehicle_type)

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
