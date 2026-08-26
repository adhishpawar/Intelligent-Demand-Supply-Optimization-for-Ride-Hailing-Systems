from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    role: str = Field(pattern="^(RIDER|DRIVER)$")
    phone: str
    name: str
    # driver-only, ignored for riders:
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    vehicle_plate: str | None = None
    vehicle_type: str | None = "SEDAN"
    city_id: str | None = None


class RegisterResponse(BaseModel):
    user_id: str
    role: str
    phone: str


class OtpRequestRequest(BaseModel):
    phone: str


class OtpRequestResponse(BaseModel):
    expires_in_seconds: int
    dev_code: str | None = None  # GAP AS-04: present only in dev mode, never in a real deployment


class OtpVerifyRequest(BaseModel):
    phone: str
    code: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str
    role: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserProfileResponse(BaseModel):
    user_id: str
    role: str
    phone: str
    name: str
    rating_avg: float


class DriverProfileResponse(BaseModel):
    driver_id: str
    vehicle_id: str | None
    status: str
    city_id: str | None
    kyc_verified: bool
    acceptance_rate: float
    rides_completed: int


class KycUpdateRequest(BaseModel):
    verified: bool
