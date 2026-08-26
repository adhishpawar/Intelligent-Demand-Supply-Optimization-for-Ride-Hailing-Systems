"""PLAN finding A5: "the geo index proposes, the database disposes." Redis's geo set
can lag reality (a driver who went ON_TRIP a moment ago via a path that hasn't
finished updating the index yet). Before scoring, the candidate shortlist from Redis
is validated against Postgres in ONE batched query — never per-candidate, which would
reintroduce the network-I/O-in-the-hot-path problem finding A1 forbids.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def validate_and_enrich(
    session: AsyncSession, candidate_ids: list[str]
) -> dict[str, dict]:
    """Returns {driver_id: {rating_avg, acceptance_rate}} for exactly the candidates
    that are (a) still ONLINE in driver_profiles, (b) not already assigned to an
    active trip, and (c) KYC-verified -- all three re-checked here even though Redis
    is supposed to already reflect (a)/(b), because Redis is a cache of the truth,
    not the truth.

    (c) is a Round 5 stakeholder council (tech lead/compliance) fix: `kyc_verified`
    was written by the admin KYC toggle and read by every driver-profile display, but
    nothing anywhere ever gated matching on it -- an unverified (or KYC-revoked)
    driver could go online and be dispatched real rides identically to a verified
    one. This is the one place that decides who a rider is actually matched with, so
    it's the correct enforcement point, not the go-online endpoint (which is
    Location's, a different service, and "can this driver toggle their own status"
    is a different question from "can this driver actually carry a passenger")."""
    if not candidate_ids:
        return {}
    rows = (
        await session.execute(
            text(
                """
                SELECT dp.driver_id, u.rating_avg, dp.acceptance_rate
                FROM driver_profiles dp
                JOIN users u ON u.user_id = dp.driver_id
                WHERE dp.driver_id = ANY(:ids)
                  AND dp.status = 'ONLINE'
                  AND dp.kyc_verified = TRUE
                  AND dp.driver_id NOT IN (
                      SELECT driver_id FROM trips
                      WHERE driver_id IS NOT NULL
                        AND status IN ('DRIVER_ASSIGNED', 'DRIVER_ARRIVING', 'DRIVER_ARRIVED', 'IN_PROGRESS')
                  )
                """
            ),
            {"ids": candidate_ids},
        )
    ).mappings().all()
    return {
        str(r["driver_id"]): {"rating_avg": float(r["rating_avg"]), "acceptance_rate": float(r["acceptance_rate"])}
        for r in rows
    }
