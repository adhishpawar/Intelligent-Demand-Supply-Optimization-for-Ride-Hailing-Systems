"""Deterministic seed data (PLAN §6.5 step 4): 2 cities (Pune + Mumbai, wired as
neighbors in `libs.geo.cities` — exercising the AM-09 border-supply fix in the seed
data itself), 12 drivers with vehicles and varied ratings/acceptance rates, 5 riders,
1 admin. Fixed phone numbers -> fixed UUIDs (deterministic, so every run of the demo
is reproducible) and idempotent (safe to re-run: upserts on phone).

Login: phone + OTP. There is no seeded password. Request an OTP for any seeded phone
via `POST /v1/auth/otp/request`; dev mode returns the code directly in the response
(GAP AS-04 — no SMS provider tonight).
"""
from __future__ import annotations

import asyncio
import sys
import uuid

import asyncpg

from libs.common.config import get_settings

ADMIN_PHONE = "+910000000001"

RIDER_NAMES = ["Asha Rider", "Bhavesh Rider", "Chitra Rider", "Devika Rider", "Esha Rider"]

DRIVER_SPECS = [
    # (name, city_id, rating_avg, acceptance_rate, vehicle_type)
    ("Aman Driver", "pune", 4.9, 0.97, "SEDAN"),
    ("Bilal Driver", "pune", 4.7, 0.91, "HATCHBACK"),
    ("Chetan Driver", "pune", 4.95, 0.99, "SUV"),
    ("Deepa Driver", "pune", 4.3, 0.72, "AUTO"),
    ("Eshwar Driver", "pune", 4.6, 0.85, "SEDAN"),
    ("Farah Driver", "pune", 4.8, 0.93, "HATCHBACK"),
    ("Gaurav Driver", "pune", 4.2, 0.68, "AUTO"),
    ("Hina Driver", "pune", 4.85, 0.95, "SEDAN"),
    ("Imran Driver", "mumbai", 4.75, 0.90, "SEDAN"),
    ("Jyoti Driver", "mumbai", 4.5, 0.80, "HATCHBACK"),
    ("Kunal Driver", "mumbai", 4.9, 0.96, "SUV"),
    ("Lata Driver", "mumbai", 4.4, 0.77, "AUTO"),
]


def _deterministic_id(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


async def seed() -> None:
    settings = get_settings()
    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
    )
    try:
        admin_id = _deterministic_id(ADMIN_PHONE)
        await _upsert_user(conn, admin_id, "ADMIN", ADMIN_PHONE, "Platform Admin")

        for i, name in enumerate(RIDER_NAMES, start=1):
            phone = f"+9190000000{i:02d}"
            await _upsert_user(conn, _deterministic_id(phone), "RIDER", phone, name)

        for i, (name, city_id, rating, acceptance, vehicle_type) in enumerate(DRIVER_SPECS, start=1):
            phone = f"+9180000000{i:02d}"
            driver_id = _deterministic_id(phone)
            await _upsert_user(conn, driver_id, "DRIVER", phone, name, rating_avg=rating)
            vehicle_id = _deterministic_id(f"vehicle:{phone}")
            await conn.execute(
                """
                INSERT INTO vehicles (vehicle_id, driver_id, make, model, plate_number, vehicle_type)
                VALUES ($1, $2, 'Generic', 'Model', $3, $4)
                ON CONFLICT (vehicle_id) DO NOTHING
                """,
                vehicle_id,
                driver_id,
                f"MH-{i:02d}-{1000+i}",
                vehicle_type,
            )
            await conn.execute(
                """
                INSERT INTO driver_profiles (driver_id, vehicle_id, status, city_id, kyc_verified, acceptance_rate)
                VALUES ($1, $2, 'OFFLINE', $3, TRUE, $4)
                ON CONFLICT (driver_id) DO UPDATE SET
                    vehicle_id = EXCLUDED.vehicle_id,
                    city_id = EXCLUDED.city_id,
                    acceptance_rate = EXCLUDED.acceptance_rate
                """,
                driver_id,
                vehicle_id,
                city_id,
                acceptance,
            )

        counts = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM driver_profiles) AS drivers"
        )
        print(f"Seed complete: {counts['users']} users, {counts['drivers']} driver profiles.")
        print(f"Admin phone: {ADMIN_PHONE}")
        print(f"Rider phones: {', '.join(f'+9190000000{i:02d}' for i in range(1, 6))}")
        print(f"Driver phones: {', '.join(f'+9180000000{i:02d}' for i in range(1, 13))}")
        print("Login: POST /v1/auth/otp/request {phone}, then /v1/auth/otp/verify — dev mode returns the code.")
    finally:
        await conn.close()


async def _upsert_user(
    conn: asyncpg.Connection, user_id: str, role: str, phone: str, name: str, *, rating_avg: float = 5.0
) -> None:
    await conn.execute(
        """
        INSERT INTO users (user_id, role, phone, name, rating_avg)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (phone) DO UPDATE SET name = EXCLUDED.name, rating_avg = EXCLUDED.rating_avg
        """,
        user_id,
        role,
        phone,
        name,
        rating_avg,
    )


def main() -> int:
    asyncio.run(seed())
    return 0


if __name__ == "__main__":
    sys.exit(main())
