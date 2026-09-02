from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class CityResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    slug: str
    timezone: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class CreateCityRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    timezone: str = Field(default="UTC", max_length=64)


class ZoneResponse(BaseModel):
    id: str
    city_id: str
    zone_index: int
    zone_model_version: str
    name: str
    centroid_lat: float
    centroid_lng: float
    is_active: bool

    model_config = {"from_attributes": True}


class CreateZoneRequest(BaseModel):
    """A tenant defines its own zone map -- this is what makes the platform
    onboardable for a city other than the one bundled model was fit on
    (ML-19), by hand, ahead of a proper per-city re-clustering workflow.
    """

    zone_index: int = Field(ge=0, description="must match the served model's zone space, 0..19 today")
    zone_model_version: str = Field(min_length=1, max_length=50)
    name: str = Field(default="", max_length=200)
    centroid_lat: float = Field(ge=-90, le=90)
    centroid_lng: float = Field(ge=-180, le=180)
