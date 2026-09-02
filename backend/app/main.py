"""
Application factory.

`create_app()` rather than a module-level `app = FastAPI()` so tests can build
independent instances (different Settings, different Database) without import
side effects -- the exact problem `Inference/app.py` had, generalized and fixed
at the framework level.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.rate_limit import RateLimiter
from app.db.base import Database
from app.ml.registry import ModelRegistry
from app.observability.middleware import RequestContextMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_secrets()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = Database(settings)

        app.state.model_registry = ModelRegistry(settings.demand_model_path, settings.demand_model_metadata_path)
        loaded = app.state.model_registry.load()
        if not loaded:
            logger.warning(
                "starting with NO demand model loaded (%s) -- forecast endpoints will "
                "return 503 until a model is trained and present",
                app.state.model_registry.load_error,
            )

        try:
            # protocol=2: the project's native Redis binary is 5.0.14, which
            # predates RESP3 / the HELLO handshake redis-py defaults to.
            app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True, protocol=2)
            await app.state.redis.ping()
            app.state.rate_limiter = RateLimiter(app.state.redis)
            logger.info("connected to redis at %s", settings.redis_url)
        except Exception as exc:  # noqa: BLE001 - cache/rate-limit are optional
            logger.warning("redis unavailable (%s) -- caching and rate limiting degrade to no-op", exc)
            app.state.redis = None
            app.state.rate_limiter = None

        yield

        await app.state.db.dispose()
        if app.state.redis is not None:
            await app.state.redis.aclose()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "RideOps Intelligence -- ML decision platform for ride-hailing demand "
            "forecasting. Bolt-on to the operational platform; never a blocking "
            "dependency of the booking path."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
