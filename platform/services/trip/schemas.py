from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class RequestRideRequest(BaseModel):
    pickup: LatLng
    drop: LatLng


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


class RespondRequest(BaseModel):
    action: str = Field(pattern="^(ACCEPT|REJECT)$")


class CancelRequest(BaseModel):
    reason: str | None = None


class AuditEventResponse(BaseModel):
    event_id: str
    from_status: str | None
    to_status: str
    event_type: str
    actor_role: str | None
    actor_id: str | None
    payload: dict
    created_at: datetime
