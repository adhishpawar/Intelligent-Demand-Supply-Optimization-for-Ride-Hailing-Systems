from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.core.security import Principal, require_permission
from app.db.deps import SessionDep
from app.repositories.audit import AuditRepository
from app.repositories.users import UserRepository
from app.schemas.auth import CreateUserRequest, UpdateUserStatusRequest, UserResponse
from app.schemas.common import Page, PaginationParams
from app.services.auth_service import AuthService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserResponse])
async def list_users(
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("user:read"))],
    pagination: Annotated[PaginationParams, Depends()],
) -> Page[UserResponse]:
    repo = UserRepository(session)
    users = await repo.list_for_tenant(principal.tenant_id, limit=pagination.limit, offset=pagination.offset)
    return Page(
        items=[UserResponse.model_validate(u) for u in users],
        total=len(users),
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    body: CreateUserRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("user:manage"))],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserResponse:
    service = AuthService(session, settings)
    user = await service.create_user(
        tenant_id=principal.tenant_id,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=body.role,
    )
    await AuditRepository(session).record(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="user.create",
        resource=f"user:{user.id}",
        detail={"email": user.email, "role": user.role},
    )
    return UserResponse.model_validate(user)


@router.patch("/{user_id}/status", response_model=UserResponse)
async def update_user_status(
    user_id: str,
    body: UpdateUserStatusRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_permission("user:manage"))],
) -> UserResponse:
    repo = UserRepository(session)
    user = await repo.get_by_id_scoped(user_id, principal.tenant_id)
    if user is None:
        raise NotFoundError("user not found")
    await repo.set_active(user, body.is_active)
    await session.commit()
    await AuditRepository(session).record(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="user.set_active",
        resource=f"user:{user_id}",
        detail={"is_active": body.is_active},
    )
    return UserResponse.model_validate(user)
