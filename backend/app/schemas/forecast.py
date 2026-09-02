from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ForecastPointResponse(BaseModel):
    zone_id: int = Field(description="zone_index within the city's zone model")
    window_start: datetime
    predicted_demand: float
    unit: str
    degraded: bool = Field(description="true if autoregressive history was unavailable")
    model_version: str


class HorizonRequest(BaseModel):
    zone_id: int = Field(ge=0)
    windows: int = Field(default=4, ge=1, le=8, description="number of 15-min windows ahead, max 2h")


class BatchForecastRequest(BaseModel):
    zone_ids: list[int] = Field(min_length=1, max_length=200)


class ForecastHistoryPoint(BaseModel):
    window_start: datetime
    predicted_demand: float
    model_version: str
    degraded: bool

    model_config = {"from_attributes": True}


class ModelInfoResponse(BaseModel):
    loaded: bool
    load_error: str | None
    artifact_path: str
    serving_contract_version: str
    metadata: dict | None
