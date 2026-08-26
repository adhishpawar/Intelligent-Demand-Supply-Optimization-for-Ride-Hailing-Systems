from __future__ import annotations

from pydantic import BaseModel, Field


class RateRequest(BaseModel):
    stars: int = Field(ge=1, le=5)
    comment: str | None = None


class RatingResponse(BaseModel):
    rating_id: str
    trip_id: str
    rater_role: str
    rater_id: str
    ratee_id: str
    stars: int
    comment: str | None
