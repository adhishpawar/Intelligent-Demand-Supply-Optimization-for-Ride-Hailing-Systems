from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.security import Principal, get_principal
from app.db.deps import SessionDep
from app.repositories.users import UserRepository
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterTenantRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register-tenant", response_model=TokenResponse, status_code=201)
async def register_tenant(
    body: RegisterTenantRequest, session: SessionDep, settings: Annotated[Settings, Depends(get_settings)]
) -> TokenResponse:
    """Self-service signup. Creates a tenant and its first TENANT_ADMIN user."""
    service = AuthService(session, settings)
    _, user = await service.register_tenant(
        tenant_name=body.tenant_name,
        tenant_slug=body.tenant_slug,
        admin_email=body.admin_email,
        admin_password=body.admin_password,
        admin_full_name=body.admin_full_name,
    )
    access, refresh, ttl = await service._issue_tokens(user)  # noqa: SLF001 - same module's service
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=ttl)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest, session: SessionDep, settings: Annotated[Settings, Depends(get_settings)]
) -> TokenResponse:
    service = AuthService(session, settings)
    access, refresh, ttl = await service.login(email=body.email, password=body.password)
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=ttl)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest, session: SessionDep, settings: Annotated[Settings, Depends(get_settings)]
) -> TokenResponse:
    service = AuthService(session, settings)
    access, new_refresh, ttl = await service.refresh(raw_refresh_token=body.refresh_token)
    return TokenResponse(access_token=access, refresh_token=new_refresh, expires_in=ttl)


@router.post("/logout", status_code=204)
async def logout(
    body: RefreshRequest, session: SessionDep, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    service = AuthService(session, settings)
    await service.logout(raw_refresh_token=body.refresh_token)


@router.get("/me", response_model=UserResponse)
async def me(principal: Annotated[Principal, Depends(get_principal)], session: SessionDep) -> UserResponse:
    users = UserRepository(session)
    user = await users.get_by_id_scoped(principal.user_id, principal.tenant_id)
    if user is None:
        # SUPER_ADMIN principals are not scoped to a tenant row of their own in
        # every deployment shape; fall back to the unscoped lookup for them.
        user = await users.get_by_id_any_tenant(principal.user_id)
    return UserResponse.model_validate(user)
