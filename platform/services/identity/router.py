"""HTTP layer. RBAC Layer 2 (`require_roles`) guards role-restricted endpoints; Layer 3
(row ownership) is applied inline here for `GET /v1/users/{user_id}` and
`GET /v1/drivers/{driver_id}` — a rider/driver may read their own record, an admin may
read any record, anyone else gets 403. This is the ownership-predicate pattern every
service repeats for its own resources (PLAN §3.1 Layer 3); Trip service applies the
same pattern against `trip.rider_id`/`trip.driver_id`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from libs.common.config import Settings, get_settings
from libs.common.errors import ForbiddenError
from libs.security.principal import Principal, Role
from libs.security.rbac import get_principal, require_roles
from services.identity.container import get_identity_service
from services.identity.schemas import (
    DriverProfileResponse,
    DriverPublicInfoResponse,
    KycUpdateRequest,
    OtpRequestRequest,
    OtpRequestResponse,
    OtpVerifyRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserProfileResponse,
)
from services.identity.service import IdentityService

router = APIRouter()


@router.post("/v1/auth/register", response_model=RegisterResponse)
async def register(body: RegisterRequest, svc: IdentityService = Depends(get_identity_service)):
    user = await svc.register(
        role=body.role,
        phone=body.phone,
        name=body.name,
        city_id=body.city_id,
        vehicle_make=body.vehicle_make,
        vehicle_model=body.vehicle_model,
        vehicle_plate=body.vehicle_plate,
        vehicle_type=body.vehicle_type,
    )
    return RegisterResponse(user_id=user.user_id, role=user.role, phone=user.phone)


@router.post("/v1/auth/otp/request", response_model=OtpRequestResponse)
async def request_otp(
    body: OtpRequestRequest,
    svc: IdentityService = Depends(get_identity_service),
    settings: Settings = Depends(get_settings),
):
    code = await svc.request_otp(body.phone)
    return OtpRequestResponse(
        expires_in_seconds=300,
        dev_code=code if settings.environment == "dev" else None,
    )


@router.post("/v1/auth/otp/verify", response_model=TokenResponse)
async def verify_otp(body: OtpVerifyRequest, svc: IdentityService = Depends(get_identity_service)):
    user, access, refresh = await svc.verify_otp(body.phone, body.code)
    return TokenResponse(access_token=access, refresh_token=refresh, user_id=user.user_id, role=user.role)


@router.post("/v1/auth/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, svc: IdentityService = Depends(get_identity_service)):
    principal, access, refresh = await svc.refresh(body.refresh_token)
    return TokenResponse(access_token=access, refresh_token=refresh, user_id=principal.user_id, role=principal.role.value)


@router.get("/v1/users/me", response_model=UserProfileResponse)
async def get_my_profile(
    principal: Principal = Depends(get_principal), svc: IdentityService = Depends(get_identity_service)
):
    user = await svc.get_profile(principal.user_id)
    return UserProfileResponse(**user.__dict__)


@router.get("/v1/users/{user_id}", response_model=UserProfileResponse)
async def get_user(
    user_id: str,
    principal: Principal = Depends(get_principal),
    svc: IdentityService = Depends(get_identity_service),
):
    if principal.user_id != user_id and not principal.has_role(Role.ADMIN):
        raise ForbiddenError("cannot read another user's profile")
    user = await svc.get_profile(user_id)
    return UserProfileResponse(**user.__dict__)


@router.get("/v1/admin/drivers")
async def list_all_drivers(
    _principal: Principal = Depends(require_roles(Role.ADMIN)),
    svc: IdentityService = Depends(get_identity_service),
):
    return await svc.list_all_drivers()


@router.get("/v1/drivers/{driver_id}/public", response_model=DriverPublicInfoResponse)
async def get_driver_public_info(
    driver_id: str,
    _principal: Principal = Depends(get_principal),  # any authenticated user -- name/rating/vehicle is not sensitive
    svc: IdentityService = Depends(get_identity_service),
):
    info = await svc.get_driver_public_info(driver_id)
    return DriverPublicInfoResponse(**info.__dict__)


@router.get("/v1/drivers/{driver_id}", response_model=DriverProfileResponse)
async def get_driver_profile(
    driver_id: str,
    principal: Principal = Depends(get_principal),
    svc: IdentityService = Depends(get_identity_service),
):
    if principal.user_id != driver_id and not principal.has_role(Role.ADMIN):
        raise ForbiddenError("cannot read another driver's profile")
    profile = await svc.get_driver_profile(driver_id)
    return DriverProfileResponse(**profile.__dict__)


@router.patch("/v1/drivers/{driver_id}/kyc", response_model=DriverProfileResponse)
async def update_kyc(
    driver_id: str,
    body: KycUpdateRequest,
    principal: Principal = Depends(require_roles(Role.ADMIN)),  # Layer 2: admin-only, re-checked here
    svc: IdentityService = Depends(get_identity_service),
):
    await svc.set_kyc(driver_id, body.verified)
    profile = await svc.get_driver_profile(driver_id)
    return DriverProfileResponse(**profile.__dict__)
