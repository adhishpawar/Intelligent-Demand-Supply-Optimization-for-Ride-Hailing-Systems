from __future__ import annotations

from pydantic import BaseModel


class DispatchRequest(BaseModel):
    """PLAN §6.3 finding C3: takes a structured request object rather than a bare
    trip_id specifically so a future batch/pooled dispatch request is a new field on
    this object, not a new signature at every call site."""

    trip_id: str
    city_id: str
    pickup_lat: float
    pickup_lng: float
    excluded_driver_ids: list[str] = []


class DispatchResponse(BaseModel):
    driver_id: str | None
    eta_seconds: float | None
    distance_m: float | None
    candidates_considered: int


class ReleaseClaimRequest(BaseModel):
    driver_id: str
    trip_id: str
