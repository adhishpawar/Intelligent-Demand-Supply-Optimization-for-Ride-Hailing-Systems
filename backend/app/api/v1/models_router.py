from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.security import Principal, require_permission
from app.db.deps import SessionDep
from app.repositories.model_runs import ModelRunRepository
from app.schemas.forecast import ModelInfoResponse

router = APIRouter(prefix="/models", tags=["models"])


@router.get("/current", response_model=ModelInfoResponse)
async def current_model(
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("model:read"))],
) -> ModelInfoResponse:
    """What is actually scoring traffic right now (ML-09)."""
    registry = request.app.state.model_registry
    return ModelInfoResponse(**registry.describe())


@router.get("/history")
async def model_history(
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("model:read"))],
) -> list[dict]:
    runs = await ModelRunRepository(session).list_all()
    return [
        {
            "model_version": r.model_version,
            "contract_version": r.contract_version,
            "algorithm": r.algorithm,
            "metrics": r.metrics,
            "training_rows": r.training_rows,
            "test_rows": r.test_rows,
            "is_active": r.is_active,
            "trained_at": r.trained_at.isoformat(),
            "notes": r.notes,
        }
        for r in runs
    ]
