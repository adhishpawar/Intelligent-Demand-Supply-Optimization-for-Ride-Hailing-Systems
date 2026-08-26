"""Business logic layer — depends only on the repository Protocols and the SmsSender
port, never on SQLAlchemy/redis directly. This is what `container.py` wires with
concrete adapters, and what tests wire with fakes.
"""
from __future__ import annotations

import random

from libs.common.errors import ConflictError, ForbiddenError, NotFoundError, RateLimitedError, UnauthorizedError
from libs.security.jwt_tokens import create_access_token, create_refresh_token, decode_token
from libs.security.principal import Principal, Role
from services.identity.repository import OtpRepository, UserRepository
from services.identity.sms import SmsSender

OTP_TTL_SECONDS = 300


class IdentityService:
    def __init__(
        self,
        users: UserRepository,
        otps: OtpRepository,
        sms: SmsSender,
        *,
        jwt_secret: str,
        jwt_algorithm: str,
        access_ttl: int,
        refresh_ttl: int,
        dev_mode: bool,
        # Round 2 stakeholder council (tech lead): OTP-request volume per phone was
        # completely unbounded -- a real SMS-cost-abuse / inbox-flood vector.
        # Settings-backed (see libs.common.config), same tunable-per-deployment
        # pattern as offer_ttl_seconds, not a hardcoded module constant.
        otp_rate_limit_max_requests: int = 5,
        otp_rate_limit_window_seconds: int = 600,
    ) -> None:
        self._users = users
        self._otps = otps
        self._sms = sms
        self._jwt_secret = jwt_secret
        self._jwt_algorithm = jwt_algorithm
        self._access_ttl = access_ttl
        self._otp_rate_limit_max_requests = otp_rate_limit_max_requests
        self._otp_rate_limit_window_seconds = otp_rate_limit_window_seconds
        self._refresh_ttl = refresh_ttl
        self._dev_mode = dev_mode

    async def register(
        self,
        *,
        role: str,
        phone: str,
        name: str,
        city_id: str | None,
        vehicle_make: str | None,
        vehicle_model: str | None,
        vehicle_plate: str | None,
        vehicle_type: str | None,
    ):
        existing = await self._users.get_by_phone(phone)
        if existing is not None:
            raise ConflictError("a user with this phone number already exists", phone=phone)

        user = await self._users.create_user(role=Role(role), phone=phone, name=name)

        if role == "DRIVER":
            if not city_id:
                raise ConflictError("city_id is required to register a driver")
            await self._users.create_driver_profile(user.user_id, city_id)
            if vehicle_make and vehicle_model and vehicle_plate:
                vehicle_id = await self._users.create_vehicle(
                    user.user_id, vehicle_make, vehicle_model, vehicle_plate, vehicle_type or "SEDAN"
                )
                await self._users.attach_vehicle(user.user_id, vehicle_id)

        return user

    async def request_otp(self, phone: str) -> str:
        user = await self._users.get_by_phone(phone)
        if user is None:
            raise NotFoundError("no user registered with this phone number", phone=phone)

        recent = await self._otps.count_recent(phone, self._otp_rate_limit_window_seconds)
        if recent >= self._otp_rate_limit_max_requests:
            raise RateLimitedError(
                "too many OTP requests for this phone number -- please wait before retrying",
                retry_after_seconds=self._otp_rate_limit_window_seconds,
            )

        code = f"{random.randint(0, 999999):06d}"
        await self._otps.store_code(phone, code, OTP_TTL_SECONDS)
        await self._sms.send_otp(phone, code)
        return code  # caller decides whether to surface this (dev_mode only)

    async def verify_otp(self, phone: str, code: str):
        ok = await self._otps.verify_and_consume(phone, code)
        if not ok:
            raise UnauthorizedError("invalid or expired OTP code")

        user = await self._users.get_by_phone(phone)
        if user is None:
            raise NotFoundError("no user registered with this phone number", phone=phone)
        # Round 6 stakeholder council (tech lead): `is_active` gated at the two
        # token-minting choke points (here and refresh() below) rather than on every
        # authenticated request -- that keeps the RBAC hot path a stateless JWT
        # decode (PLAN's whole reason for JWTs), at the cost of a suspended account's
        # *already-issued* access token staying valid until its own 1-hour expiry.
        # That's an acceptable, explicit bound, not an oversight -- and it's exactly
        # the window the Round 5 token-refresh fix now closes automatically once the
        # access token does expire, rather than the account being silently
        # re-refreshable forever the way it would have been before that fix existed.
        if not user.is_active:
            raise ForbiddenError("this account has been deactivated")

        principal = Principal(user_id=user.user_id, role=Role(user.role), phone=user.phone)
        access = create_access_token(
            principal, secret=self._jwt_secret, algorithm=self._jwt_algorithm, ttl_seconds=self._access_ttl
        )
        refresh = create_refresh_token(
            principal, secret=self._jwt_secret, algorithm=self._jwt_algorithm, ttl_seconds=self._refresh_ttl
        )
        return user, access, refresh

    async def refresh(self, refresh_token: str):
        principal = decode_token(
            refresh_token, secret=self._jwt_secret, algorithm=self._jwt_algorithm, expected_type="refresh"
        )
        user = await self._users.get_by_id(principal.user_id)
        if user is None or not user.is_active:
            raise ForbiddenError("this account has been deactivated")
        access = create_access_token(
            principal, secret=self._jwt_secret, algorithm=self._jwt_algorithm, ttl_seconds=self._access_ttl
        )
        new_refresh = create_refresh_token(
            principal, secret=self._jwt_secret, algorithm=self._jwt_algorithm, ttl_seconds=self._refresh_ttl
        )
        return principal, access, new_refresh

    async def set_active(self, user_id: str, active: bool) -> None:
        await self._users.set_active(user_id, active)

    async def get_profile(self, user_id: str):
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("user not found", user_id=user_id)
        return user

    async def get_driver_public_info(self, driver_id: str):
        info = await self._users.get_driver_public_info(driver_id)
        if info is None:
            raise NotFoundError("driver not found", driver_id=driver_id)
        return info

    async def list_all_drivers(self):
        return await self._users.list_all_drivers()

    async def get_driver_profile(self, driver_id: str):
        profile = await self._users.get_driver_profile(driver_id)
        if profile is None:
            raise NotFoundError("driver profile not found", driver_id=driver_id)
        return profile

    async def set_kyc(self, driver_id: str, verified: bool) -> None:
        await self._users.set_kyc_verified(driver_id, verified)

    async def increment_rides_completed(self, driver_id: str) -> None:
        await self._users.increment_rides_completed(driver_id)
