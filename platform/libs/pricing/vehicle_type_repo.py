"""Round 8 stakeholder council: the DB-backed home for vehicle-type fare
multipliers, mirroring `libs/geo/city_config_repo.py`'s exact pattern (Round 1) --
Pricing owns the write path (admin-only, audited); Trip just reads. Falls back to
`libs.pricing.fare.VEHICLE_TYPE_MULTIPLIERS` if a type has no DB row yet, so this is
additive, not a breaking change.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.pricing.fare import VEHICLE_TYPE_MULTIPLIERS


async def get_vehicle_type_multiplier(session: AsyncSession, vehicle_type: str) -> float:
    row = (
        await session.execute(
            text("SELECT multiplier FROM vehicle_type_multipliers WHERE vehicle_type = :vt"),
            {"vt": vehicle_type},
        )
    ).first()
    if row is None:
        return VEHICLE_TYPE_MULTIPLIERS.get(vehicle_type, 1.0)  # defensive fallback -- should not normally be hit post-seed
    return float(row[0])


async def get_vehicle_type_multipliers_dict(session: AsyncSession) -> dict[str, float]:
    rows = (await session.execute(text("SELECT vehicle_type, multiplier FROM vehicle_type_multipliers"))).all()
    found = {r[0]: float(r[1]) for r in rows}
    return {**VEHICLE_TYPE_MULTIPLIERS, **found}  # DB values win; any type missing a row still gets its hardcoded default


async def update_vehicle_type_multiplier(
    session: AsyncSession, vehicle_type: str, multiplier: float, changed_by: str | None
) -> float:
    """Upserts the row and writes one audit event (same 'never silently change
    money-affecting config' principle as update_rate_card)."""
    current_row = (
        await session.execute(
            text("SELECT multiplier FROM vehicle_type_multipliers WHERE vehicle_type = :vt"), {"vt": vehicle_type}
        )
    ).first()
    old_value = float(current_row[0]) if current_row else None
    if old_value == multiplier:
        return multiplier

    await session.execute(
        text(
            """
            INSERT INTO vehicle_type_multipliers (vehicle_type, multiplier, updated_at, updated_by)
            VALUES (:vt, :multiplier, now(), :changed_by)
            ON CONFLICT (vehicle_type) DO UPDATE SET
                multiplier = EXCLUDED.multiplier, updated_at = now(), updated_by = EXCLUDED.updated_by
            """
        ),
        {"vt": vehicle_type, "multiplier": multiplier, "changed_by": changed_by},
    )
    await session.execute(
        text(
            "INSERT INTO vehicle_type_multiplier_events (vehicle_type, old_value, new_value, changed_by) "
            "VALUES (:vt, :old_value, :new_value, :changed_by)"
        ),
        {"vt": vehicle_type, "old_value": old_value, "new_value": multiplier, "changed_by": changed_by},
    )
    await session.commit()
    return multiplier
