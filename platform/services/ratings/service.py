"""Ratings — REAL-LITE per PLAN §2.1, but the feedback loop into matching is real, not
stubbed: this module writes directly to `users.rating_avg`, which is exactly the
column `services/matching/repository.py`'s validate_and_enrich query reads for the
scoring formula. A rating posted here measurably changes a driver's future rank.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import Settings
from libs.common.errors import ConflictError, ForbiddenError
from libs.security.principal import Principal, Role
from services.ratings.trip_client import annotate_rating, get_trip


class RatingsService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def rate(self, session: AsyncSession, trip_id: str, principal: Principal, stars: int, comment: str | None) -> dict:
        trip = await get_trip(self._settings, trip_id)

        if principal.role == Role.RIDER:
            if trip["rider_id"] != principal.user_id:
                raise ForbiddenError("not the rider on this trip")
            ratee_id = trip["driver_id"]
        elif principal.role == Role.DRIVER:
            if trip["driver_id"] != principal.user_id:
                raise ForbiddenError("not the driver on this trip")
            ratee_id = trip["rider_id"]
        else:
            raise ForbiddenError("only riders and drivers may submit ratings")

        if trip["status"] not in ("PAID", "RATED"):
            raise ConflictError("trip must be paid before it can be rated", status=trip["status"])
        if not ratee_id:
            raise ConflictError("trip has no counterpart to rate")

        try:
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO ratings (trip_id, rater_role, rater_id, ratee_id, stars, comment)
                        VALUES (:trip_id, :rater_role, :rater_id, :ratee_id, :stars, :comment)
                        RETURNING rating_id
                        """
                    ),
                    {
                        "trip_id": trip_id, "rater_role": principal.role.value, "rater_id": principal.user_id,
                        "ratee_id": ratee_id, "stars": stars, "comment": comment,
                    },
                )
            ).first()
        except Exception as exc:
            await session.rollback()
            raise ConflictError("you have already rated this trip", trip_id=trip_id) from exc

        # Rolling average recompute -- THE feedback loop into matching (PLAN §2.1
        # row 9: "the resulting rating feeds back into the matching scorer").
        await session.execute(
            text(
                """
                UPDATE users SET
                    rating_avg = ROUND(((rating_avg * rating_count) + :stars) / (rating_count + 1), 2),
                    rating_count = rating_count + 1
                WHERE user_id = :ratee_id
                """
            ),
            {"stars": stars, "ratee_id": ratee_id},
        )
        await session.commit()

        await annotate_rating(self._settings, trip_id, principal.role.value, principal.user_id)
        return {"rating_id": str(row[0]), "trip_id": trip_id, "rater_role": principal.role.value, "rater_id": principal.user_id, "ratee_id": ratee_id, "stars": stars, "comment": comment}

    async def list_for_user(self, session: AsyncSession, user_id: str) -> list[dict]:
        rows = (
            await session.execute(
                text("SELECT rating_id, trip_id, rater_role, rater_id, ratee_id, stars, comment FROM ratings WHERE ratee_id = :id ORDER BY created_at DESC"),
                {"id": user_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]
