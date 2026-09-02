"""Forecast endpoints -- the FR-01 surface.

Every route pulls the shared `ModelRegistry` and (optional) Redis client off
`request.app.state`, set up once in the app factory, and constructs a
request-scoped `ForecastingService` around them plus the request's own DB
session. Nothing here builds a DataFrame or touches LightGBM directly -- that
separation is what the "separate ML from HTTP concerns" section of the
mandate asks for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.config import Settings, get_settings
from app.core.rate_limit import rate_limit
from app.core.security import Principal, require_permission
from app.db.deps import SessionDep
from app.schemas.forecast import (
    BatchForecastRequest,
    ForecastHistoryPoint,
    ForecastPointResponse,
)
from app.services.forecasting_service import ForecastingService

router = APIRouter(prefix="/forecasts", tags=["forecasts"])


def _build_service(request: Request, session: SessionDep, settings: Settings) -> ForecastingService:
    registry = request.app.state.model_registry
    redis_client = getattr(request.app.state, "redis", None)
    return ForecastingService(session, registry, settings, redis_client=redis_client)


@router.get("/cities/{city_id}/zones/{zone_id}", response_model=ForecastPointResponse)
async def predict_single(
    city_id: str,
    zone_id: int,
    request: Request,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("forecast:read"))],
    _throttled: Annotated[None, Depends(rate_limit(limit=60, window_seconds=60, scope="forecast:predict"))],
    settings: Annotated[Settings, Depends(get_settings)],
    window_start: Annotated[datetime | None, Query(description="defaults to the next 15-min window")] = None,
) -> ForecastPointResponse:
    service = _build_service(request, session, settings)
    result = await service.predict(
        tenant_id=principal.tenant_id,
        city_id=city_id,
        zone_index=zone_id,
        window_start=window_start,
        requested_by=principal.user_id,
    )
    return ForecastPointResponse(**result)


@router.get("/cities/{city_id}/zones/{zone_id}/horizon", response_model=list[ForecastPointResponse])
async def predict_horizon(
    city_id: str,
    zone_id: int,
    request: Request,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("forecast:read"))],
    _throttled: Annotated[None, Depends(rate_limit(limit=60, window_seconds=60, scope="forecast:predict"))],
    settings: Annotated[Settings, Depends(get_settings)],
    windows: Annotated[int, Query(ge=1, le=8)] = 4,
) -> list[ForecastPointResponse]:
    service = _build_service(request, session, settings)
    results = await service.predict_horizon(
        tenant_id=principal.tenant_id,
        city_id=city_id,
        zone_index=zone_id,
        windows=windows,
        requested_by=principal.user_id,
    )
    return [ForecastPointResponse(**r) for r in results]


@router.post("/cities/{city_id}/batch", response_model=list[ForecastPointResponse])
async def predict_batch(
    city_id: str,
    body: BatchForecastRequest,
    request: Request,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("forecast:write"))],
    _throttled: Annotated[None, Depends(rate_limit(limit=20, window_seconds=60, scope="forecast:batch"))],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ForecastPointResponse]:
    service = _build_service(request, session, settings)
    results = await service.predict_city(
        tenant_id=principal.tenant_id, city_id=city_id, zone_ids=body.zone_ids, requested_by=principal.user_id
    )
    return [ForecastPointResponse(**r) for r in results]


@router.get("/cities/{city_id}/zones/{zone_id}/history", response_model=list[ForecastHistoryPoint])
async def forecast_history(
    city_id: str,
    zone_id: int,
    request: Request,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("forecast:read"))],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ForecastHistoryPoint]:
    service = _build_service(request, session, settings)
    rows = await service.history(
        tenant_id=principal.tenant_id, city_id=city_id, zone_index=zone_id, limit=limit
    )
    return [ForecastHistoryPoint.model_validate(r) for r in rows]
