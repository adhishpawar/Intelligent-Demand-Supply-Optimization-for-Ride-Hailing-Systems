from __future__ import annotations

from pydantic import BaseModel, Field


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class EstimateRequest(BaseModel):
    pickup: LatLng
    drop: LatLng


class EstimateResponse(BaseModel):
    fare_estimate: float
    eta_pickup_seconds: float
    distance_m: float
    duration_s: float
    surge_multiplier: float
    city_id: str


class SurgeResponse(BaseModel):
    city_id: str
    cells: dict[str, float]
