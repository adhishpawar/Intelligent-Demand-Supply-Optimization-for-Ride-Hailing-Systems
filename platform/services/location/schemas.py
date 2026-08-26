from __future__ import annotations

from pydantic import BaseModel, Field


class LocationPingRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    heading: float = 0.0
    speed_kmh: float = 0.0


class LocationPingResponse(BaseModel):
    accepted: bool
    reason: str | None = None


class StatusUpdateRequest(BaseModel):
    status: str = Field(pattern="^(ONLINE|OFFLINE)$")


class StatusUpdateResponse(BaseModel):
    driver_id: str
    status: str
    city_id: str
    state_seq: int


class DriverLiveStateResponse(BaseModel):
    driver_id: str
    status: str | None
    lat: float | None = None
    lng: float | None = None
    heading: float | None = None
    speed_kmh: float | None = None
    last_ping_ts: float | None = None
