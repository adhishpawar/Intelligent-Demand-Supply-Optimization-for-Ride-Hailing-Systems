from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import City, Zone


class CityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_scoped(self, city_id: str, tenant_id: str) -> City | None:
        stmt = select(City).where(City.id == city_id, City.tenant_id == tenant_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_tenant(self, tenant_id: str) -> list[City]:
        stmt = select(City).where(City.tenant_id == tenant_id).order_by(City.created_at)
        return list((await self._session.execute(stmt)).scalars().all())

    async def create(self, *, tenant_id: str, name: str, slug: str, timezone: str) -> City:
        city = City(tenant_id=tenant_id, name=name, slug=slug, timezone=timezone)
        self._session.add(city)
        await self._session.flush()
        return city


class ZoneRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_city(self, city_id: str, tenant_id: str) -> list[Zone]:
        stmt = (
            select(Zone)
            .where(Zone.city_id == city_id, Zone.tenant_id == tenant_id, Zone.is_active.is_(True))
            .order_by(Zone.zone_index)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_by_index(self, city_id: str, tenant_id: str, zone_index: int) -> Zone | None:
        stmt = select(Zone).where(
            Zone.city_id == city_id, Zone.tenant_id == tenant_id, Zone.zone_index == zone_index
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def bulk_create(self, zones: list[Zone]) -> None:
        self._session.add_all(zones)
        await self._session.flush()
