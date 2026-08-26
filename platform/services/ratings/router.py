from __future__ import annotations

from fastapi import APIRouter, Depends

from libs.security.principal import Principal, Role
from libs.security.rbac import get_principal, require_roles
from services.ratings.container import get_ratings_service, get_session
from services.ratings.schemas import RateRequest, RatingResponse
from services.ratings.service import RatingsService

router = APIRouter()


@router.post("/v1/trips/{trip_id}/rate", response_model=RatingResponse)
async def rate_trip(
    trip_id: str, body: RateRequest,
    principal: Principal = Depends(require_roles(Role.RIDER, Role.DRIVER)),
    svc: RatingsService = Depends(get_ratings_service),
    session=Depends(get_session),
):
    result = await svc.rate(session, trip_id, principal, body.stars, body.comment)
    return RatingResponse(**result)


@router.get("/v1/ratings/{user_id}")
async def get_ratings(
    user_id: str, _principal: Principal = Depends(get_principal),
    svc: RatingsService = Depends(get_ratings_service), session=Depends(get_session),
):
    return await svc.list_for_user(session, user_id)
