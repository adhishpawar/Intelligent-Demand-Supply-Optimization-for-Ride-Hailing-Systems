from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


VEHICLE_TYPES_PATTERN = "^(SEDAN|HATCHBACK|SUV|AUTO|BIKE)$"


class RequestRideRequest(BaseModel):
    pickup: LatLng
    drop: LatLng
    # Round 7 stakeholder council: the vehicle_type taxonomy has existed since V001
    # and the seeded drivers were already deliberately spread across all five types
    # -- riders just never had a way to ask for one. Defaults to SEDAN so existing
    # callers (tools/demo.py, older cached frontend builds) keep working unchanged.
    vehicle_type: str = Field(default="SEDAN", pattern=VEHICLE_TYPES_PATTERN)


class TripResponse(BaseModel):
    trip_id: str
    rider_id: str
    driver_id: str | None
    city_id: str
    status: str
    version: int
    pickup_lat: float
    pickup_lng: float
    drop_lat: float
    drop_lng: float
    fare_estimate: float | None
    fare_final: float | None
    surge_multiplier: float
    dispatch_attempts: int
    current_offer_driver_id: str | None
    current_offer_expires_at: datetime | None
    cancellation_fee_applied: float
    requested_at: datetime
    vehicle_type_requested: str


class RespondRequest(BaseModel):
    action: str = Field(pattern="^(ACCEPT|REJECT)$")


class CancelRequest(BaseModel):
    reason: str | None = None


class PaymentResultRequest(BaseModel):
    status: str = Field(pattern="^(SUCCEEDED|FAILED|PAID_PENDING_RETRY_SUCCEEDED)$")


class RateAnnotationRequest(BaseModel):
    rater_role: str = Field(pattern="^(RIDER|DRIVER)$")
    rater_id: str


class AuditEventResponse(BaseModel):
    event_id: str
    from_status: str | None
    to_status: str
    event_type: str
    actor_role: str | None
    actor_id: str | None
    payload: dict
    created_at: datetime
