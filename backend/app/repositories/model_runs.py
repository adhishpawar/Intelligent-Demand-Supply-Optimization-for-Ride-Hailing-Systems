from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelRun
from app.ml.registry import ModelMetadata


class ModelRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self) -> ModelRun | None:
        stmt = select(ModelRun).where(ModelRun.is_active.is_(True))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_version(self, model_version: str) -> ModelRun | None:
        stmt = select(ModelRun).where(ModelRun.model_version == model_version)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all(self, *, limit: int = 50) -> list[ModelRun]:
        stmt = select(ModelRun).order_by(ModelRun.trained_at.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def upsert_from_metadata(self, meta: ModelMetadata, *, activate: bool) -> ModelRun:
        existing = await self.get_by_version(meta.model_version)
        if existing is None:
            existing = ModelRun(
                model_name=meta.model_name,
                model_version=meta.model_version,
                contract_version=meta.contract_version,
                algorithm=meta.algorithm,
                feature_columns=meta.feature_columns,
                metrics=meta.metrics,
                training_rows=meta.training_rows,
                test_rows=meta.test_rows,
                notes=meta.notes,
                trained_at=meta.trained_at,
            )
            self._session.add(existing)
            await self._session.flush()

        if activate:
            await self._session.execute(update(ModelRun).values(is_active=False))
            existing.is_active = True
            await self._session.flush()

        return existing
