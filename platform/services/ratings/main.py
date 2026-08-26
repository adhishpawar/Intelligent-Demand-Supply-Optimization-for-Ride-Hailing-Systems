from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common.config import get_settings
from libs.common.http_errors import install_error_handlers
from libs.common.logging import configure_logging
from libs.persistence.engine import make_engine, make_sessionmaker
from services.ratings import container
from services.ratings.router import router

configure_logging("ratings")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = make_engine(settings)
    container.configure(make_sessionmaker(engine), settings)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Ratings Service", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "ratings"}

    return app


app = create_app()
