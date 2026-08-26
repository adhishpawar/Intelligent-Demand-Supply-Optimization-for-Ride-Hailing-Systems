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


class CityConfigResponse(BaseModel):
    """Round 1 stakeholder council: every business rule GAP AS-06 flagged as
    ambiguous, now visible and admin-editable rather than a hardcoded constant."""

    city_id: str
    base_fare: float
    per_km_rate: float
    per_min_rate: float
    booking_fee: float
    surge_cap: float
    commission_pct: float
    cancellation_fee: float


class CityConfigUpdateRequest(BaseModel):
    base_fare: float | None = Field(default=None, ge=0)
    per_km_rate: float | None = Field(default=None, ge=0)
    per_min_rate: float | None = Field(default=None, ge=0)
    booking_fee: float | None = Field(default=None, ge=0)
    surge_cap: float | None = Field(default=None, ge=1.0, le=10.0)
    commission_pct: float | None = Field(default=None, ge=0, le=1.0)
    cancellation_fee: float | None = Field(default=None, ge=0)
