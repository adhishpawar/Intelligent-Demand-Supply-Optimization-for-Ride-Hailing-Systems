from __future__ import annotations

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from libs.common.errors import ConflictError
from libs.common.ids import utcnow
from libs.geo.cities import CITIES, city_for
from libs.geo.eta import FastEtaEstimator
from libs.geo.geohash import encode as geohash_encode
from libs.pricing.fare import estimate_fare
from libs.pricing.surge_reader import read_surge_multiplier, surge_key
from libs.security.principal import Principal
from libs.security.rbac import get_principal
from services.pricing.container import get_redis
from services.pricing.schemas import EstimateRequest, EstimateResponse, SurgeResponse

router = APIRouter()


@router.post("/v1/pricing/estimate", response_model=EstimateResponse)
async def estimate(
    body: EstimateRequest, _principal: Principal = Depends(get_principal), redis: Redis = Depends(get_redis)
):
    city = city_for(body.pickup.lat, body.pickup.lng)
    if city is None:
        raise ConflictError("pickup location is outside any served city")

    eta_estimator = FastEtaEstimator(city.speed_profile)
    distance_m = eta_estimator.distance_m(body.pickup.lat, body.pickup.lng, body.drop.lat, body.drop.lng)
    duration_s = eta_estimator.eta_seconds(body.pickup.lat, body.pickup.lng, body.drop.lat, body.drop.lng, utcnow())
    pickup_geohash = geohash_encode(body.pickup.lat, body.pickup.lng)
    surge = await read_surge_multiplier(redis, city.city_id, pickup_geohash)
    fare = estimate_fare(city.rate_card, distance_m, duration_s, surge)

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
