from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, forecasts, models_router, orgs, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(orgs.router)
api_router.include_router(forecasts.router)
api_router.include_router(models_router.router)
