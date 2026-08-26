"""Round 1 stakeholder council: the DB-backed home for every business rule GAP AS-06
flagged as ambiguous (commission %, cancellation fee, surge cap). Every service that
needs a city's live rate card reads through here — Pricing owns the write path
(admin-only, audited); everyone else (Trip, at ride-request and completion time) just
reads. Falls back to the hardcoded `libs.geo.cities.CITIES` default if a city has no
DB row yet, so this is additive, not a breaking change to the existing registry.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.geo.cities import RateCard, get_city

RATE_CARD_FIELDS = (
    "base_fare", "per_km_rate", "per_min_rate", "booking_fee", "surge_cap", "commission_pct", "cancellation_fee",
)


async def get_rate_card(session: AsyncSession, city_id: str) -> RateCard:
    row = (
        await session.execute(
            text(
                "SELECT base_fare, per_km_rate, per_min_rate, booking_fee, surge_cap, commission_pct, cancellation_fee "
                "FROM city_configs WHERE city_id = :id"
            ),
            {"id": city_id},
        )
    ).mappings().first()
    if row is None:
        return get_city(city_id).rate_card  # defensive fallback -- should not normally be hit post-seed
    return RateCard(**{k: float(v) for k, v in dict(row).items()})


async def get_rate_card_dict(session: AsyncSession, city_id: str) -> dict:
    card = await get_rate_card(session, city_id)
    return {f: getattr(card, f) for f in RATE_CARD_FIELDS}


async def update_rate_card(session: AsyncSession, city_id: str, updates: dict, changed_by: str | None) -> dict:
    """Upserts the row and writes one audit event per changed field (PLAN's own
    'never silently change money-affecting config' principle, same spirit as the
    ledger and trip_events)."""
    current = await get_rate_card_dict(session, city_id)
    changed = {k: v for k, v in updates.items() if k in RATE_CARD_FIELDS and v is not None and v != current.get(k)}
    if not changed:
        return current

    exists = (await session.execute(text("SELECT 1 FROM city_configs WHERE city_id = :id"), {"id": city_id})).first()
    if exists:
        set_clause = ", ".join(f"{k} = :{k}" for k in changed)
        await session.execute(
            text(f"UPDATE city_configs SET {set_clause}, updated_at = now(), updated_by = :changed_by WHERE city_id = :city_id"),
            {**changed, "changed_by": changed_by, "city_id": city_id},
        )
    else:
        merged = {**current, **changed}
        cols = ", ".join(["city_id", *merged.keys()])
        placeholders = ", ".join([":city_id", *[f":{k}" for k in merged]])
        await session.execute(
            text(f"INSERT INTO city_configs ({cols}, updated_by) VALUES ({placeholders}, :changed_by)"),
            {**merged, "city_id": city_id, "changed_by": changed_by},
        )

    for field, new_value in changed.items():
        await session.execute(
            text(
                "INSERT INTO city_config_events (city_id, field, old_value, new_value, changed_by) "
                "VALUES (:city_id, :field, :old_value, :new_value, :changed_by)"
            ),
            {"city_id": city_id, "field": field, "old_value": current.get(field), "new_value": new_value, "changed_by": changed_by},
        )
    await session.commit()
    return {**current, **changed}
