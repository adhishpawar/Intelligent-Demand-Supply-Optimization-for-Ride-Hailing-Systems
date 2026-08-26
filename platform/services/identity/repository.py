"""Repository pattern (PLAN §3.2): Postgres behind a narrow interface. Nothing above
this module writes raw SQL against `users`/`driver_profiles`/`vehicles`/`otp_codes`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.errors import NotFoundError
from libs.security.principal import Role


@dataclass
class UserRecord:
    user_id: str
    role: str
    phone: str
    name: str
    rating_avg: float
    is_active: bool

    def __post_init__(self) -> None:
        # asyncpg returns native uuid.UUID for uuid columns; every repository record
        # normalizes ids to str at the boundary so nothing above this layer has to
        # know or care what the driver's native type is (and so `jose`'s JSON encoder
        # never chokes on a UUID object again).
        self.user_id = str(self.user_id)
        self.rating_avg = float(self.rating_avg)


@dataclass
class DriverProfileRecord:
    driver_id: str
    vehicle_id: str | None
    status: str
    city_id: str | None
    kyc_verified: bool
    acceptance_rate: float
    rides_completed: int
    state_seq: int

    def __post_init__(self) -> None:
        self.driver_id = str(self.driver_id)
        self.vehicle_id = str(self.vehicle_id) if self.vehicle_id is not None else None
        self.acceptance_rate = float(self.acceptance_rate)


@dataclass
class DriverPublicInfo:
    """What a rider is shown once matched — the single most reassuring screen in any
    ride-hailing app ("here is who is coming for you"), identified as the #1 rider-
    perspective gap in the Round 1 stakeholder council review."""

    driver_id: str
    name: str
    rating_avg: float
    vehicle_make: str | None
    vehicle_model: str | None
    vehicle_plate: str | None
    vehicle_type: str | None

    def __post_init__(self) -> None:
        self.driver_id = str(self.driver_id)
        self.rating_avg = float(self.rating_avg)


class UserRepository(Protocol):
    async def create_user(self, *, role: Role, phone: str, name: str) -> UserRecord: ...
    async def get_by_phone(self, phone: str) -> UserRecord | None: ...
    async def get_by_id(self, user_id: str) -> UserRecord | None: ...
    async def create_driver_profile(self, driver_id: str, city_id: str) -> None: ...
    async def create_vehicle(self, driver_id: str, make: str, model: str, plate: str, vehicle_type: str) -> str: ...
    async def attach_vehicle(self, driver_id: str, vehicle_id: str) -> None: ...
    async def get_driver_profile(self, driver_id: str) -> DriverProfileRecord | None: ...
    async def get_driver_public_info(self, driver_id: str) -> "DriverPublicInfo | None": ...
    async def list_all_drivers(self) -> list[dict]: ...
    async def set_kyc_verified(self, driver_id: str, verified: bool) -> None: ...


class PgUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_user(self, *, role: Role, phone: str, name: str) -> UserRecord:
        row = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO users (role, phone, name)
                    VALUES (:role, :phone, :name)
                    RETURNING user_id, role, phone, name, rating_avg, is_active
                    """
                ),
                {"role": role.value, "phone": phone, "name": name},
            )
        ).mappings().first()
        return UserRecord(**dict(row))

    async def get_by_phone(self, phone: str) -> UserRecord | None:
        row = (
            await self._session.execute(
                text("SELECT user_id, role, phone, name, rating_avg, is_active FROM users WHERE phone = :phone"),
                {"phone": phone},
            )
        ).mappings().first()
        return UserRecord(**dict(row)) if row else None

    async def get_by_id(self, user_id: str) -> UserRecord | None:
        row = (
            await self._session.execute(
                text("SELECT user_id, role, phone, name, rating_avg, is_active FROM users WHERE user_id = :id"),
                {"id": user_id},
            )
        ).mappings().first()
        return UserRecord(**dict(row)) if row else None

    async def create_driver_profile(self, driver_id: str, city_id: str) -> None:
        await self._session.execute(
            text(
                """
                INSERT INTO driver_profiles (driver_id, status, city_id)
                VALUES (:driver_id, 'OFFLINE', :city_id)
                ON CONFLICT (driver_id) DO NOTHING
                """
            ),
            {"driver_id": driver_id, "city_id": city_id},
        )

    async def create_vehicle(self, driver_id: str, make: str, model: str, plate: str, vehicle_type: str) -> str:
        row = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO vehicles (driver_id, make, model, plate_number, vehicle_type)
                    VALUES (:driver_id, :make, :model, :plate, :vtype)
                    RETURNING vehicle_id
                    """
                ),
                {"driver_id": driver_id, "make": make, "model": model, "plate": plate, "vtype": vehicle_type},
            )
        ).scalar_one()
        return str(row)

    async def attach_vehicle(self, driver_id: str, vehicle_id: str) -> None:
        await self._session.execute(
            text("UPDATE driver_profiles SET vehicle_id = :vid WHERE driver_id = :did"),
            {"vid": vehicle_id, "did": driver_id},
        )

    async def get_driver_public_info(self, driver_id: str) -> DriverPublicInfo | None:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT u.user_id AS driver_id, u.name, u.rating_avg,
                           v.make AS vehicle_make, v.model AS vehicle_model,
                           v.plate_number AS vehicle_plate, v.vehicle_type
                    FROM users u
                    JOIN driver_profiles dp ON dp.driver_id = u.user_id
                    LEFT JOIN vehicles v ON v.vehicle_id = dp.vehicle_id
                    WHERE u.user_id = :id
                    """
                ),
                {"id": driver_id},
            )
        ).mappings().first()
        return DriverPublicInfo(**dict(row)) if row else None

    async def get_driver_profile(self, driver_id: str) -> DriverProfileRecord | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT driver_id, vehicle_id, status, city_id, kyc_verified, acceptance_rate, "
                    "rides_completed, state_seq FROM driver_profiles WHERE driver_id = :id"
                ),
                {"id": driver_id},
            )
        ).mappings().first()
        return DriverProfileRecord(**dict(row)) if row else None

    async def list_all_drivers(self) -> list[dict]:
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT u.user_id AS driver_id, u.name, u.phone, u.rating_avg,
                           dp.city_id, dp.status, dp.kyc_verified, dp.acceptance_rate, dp.rides_completed
                    FROM users u
                    JOIN driver_profiles dp ON dp.driver_id = u.user_id
                    ORDER BY u.name
                    """
                )
            )
        ).mappings().all()
        return [
            {**dict(r), "driver_id": str(r["driver_id"]), "rating_avg": float(r["rating_avg"]), "acceptance_rate": float(r["acceptance_rate"])}
            for r in rows
        ]

    async def set_kyc_verified(self, driver_id: str, verified: bool) -> None:
        result = await self._session.execute(
            text("UPDATE driver_profiles SET kyc_verified = :v WHERE driver_id = :id"),
            {"v": verified, "id": driver_id},
        )
        if result.rowcount == 0:
            raise NotFoundError("driver profile not found", driver_id=driver_id)


class OtpRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def store_code(self, phone: str, code: str, ttl_seconds: int) -> None:
        await self._session.execute(
            text(
                "INSERT INTO otp_codes (phone, code, expires_at) VALUES (:phone, :code, now() + make_interval(secs => :ttl))"
            ),
            {"phone": phone, "code": code, "ttl": ttl_seconds},
        )

    async def verify_and_consume(self, phone: str, code: str) -> bool:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT id FROM otp_codes
                    WHERE phone = :phone AND code = :code AND consumed_at IS NULL AND expires_at > now()
                    ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {"phone": phone, "code": code},
            )
        ).first()
        if row is None:
            return False
        await self._session.execute(
            text("UPDATE otp_codes SET consumed_at = now() WHERE id = :id"), {"id": row[0]}
        )
        return True
