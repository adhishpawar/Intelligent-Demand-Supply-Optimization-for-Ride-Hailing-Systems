from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.errors import NotFoundError
from app.core.security import Principal, require_permission, resolve_tenant_scope
from app.db.deps import SessionDep
from app.repositories.cities import CityRepository, ZoneRepository
from app.repositories.tenants import TenantRepository
from app.core.errors import ConflictError
from app.db.models import Zone
from app.schemas.org import CityResponse, CreateCityRequest, CreateZoneRequest, TenantResponse, ZoneResponse

router = APIRouter(tags=["organization"])


@router.get("/tenants/me", response_model=TenantResponse)
async def get_my_tenant(
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("tenant:read"))],
) -> TenantResponse:
    tenant = await TenantRepository(session).get(principal.tenant_id)
    if tenant is None:
        raise NotFoundError("tenant not found")
    return TenantResponse.model_validate(tenant)


@router.get("/cities", response_model=list[CityResponse])
async def list_cities(
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("city:read"))],
    tenant_id: Annotated[str | None, Query(description="SUPER_ADMIN only: cross-tenant lookup")] = None,
) -> list[CityResponse]:
    scope = resolve_tenant_scope(principal, tenant_id)
    cities = await CityRepository(session).list_for_tenant(scope)
    return [CityResponse.model_validate(c) for c in cities]


@router.post("/cities", response_model=CityResponse, status_code=201)
async def create_city(
    body: CreateCityRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("city:manage"))],
) -> CityResponse:
    city = await CityRepository(session).create(
        tenant_id=principal.tenant_id, name=body.name, slug=body.slug, timezone=body.timezone
    )
    await session.commit()
    return CityResponse.model_validate(city)


@router.get("/cities/{city_id}/zones", response_model=list[ZoneResponse])
async def list_zones(
    city_id: str,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("zone:read"))],
) -> list[ZoneResponse]:
    city = await CityRepository(session).get_scoped(city_id, principal.tenant_id)
    if city is None:
        raise NotFoundError("city not found")
    zones = await ZoneRepository(session).list_for_city(city_id, principal.tenant_id)
    return [ZoneResponse.model_validate(z) for z in zones]


@router.post("/cities/{city_id}/zones", response_model=ZoneResponse, status_code=201)
async def create_zone(
    city_id: str,
    body: CreateZoneRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("zone:manage"))],
) -> ZoneResponse:
    city = await CityRepository(session).get_scoped(city_id, principal.tenant_id)
    if city is None:
        raise NotFoundError("city not found")

    zone_repo = ZoneRepository(session)
    existing = await zone_repo.get_by_index(city_id, principal.tenant_id, body.zone_index)
    if existing is not None and existing.zone_model_version == body.zone_model_version:
        raise ConflictError(
            f"zone_index {body.zone_index} already exists for zone_model_version "
            f"{body.zone_model_version!r} in this city"
        )

    zone = Zone(
        tenant_id=principal.tenant_id,
        city_id=city_id,
        zone_model_version=body.zone_model_version,
        zone_index=body.zone_index,
        name=body.name or f"Zone {body.zone_index}",
        centroid_lat=body.centroid_lat,
        centroid_lng=body.centroid_lng,
    )
    await zone_repo.bulk_create([zone])
    await session.commit()
    return ZoneResponse.model_validate(zone)
