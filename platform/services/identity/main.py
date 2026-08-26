from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common.config import get_settings
from libs.common.http_errors import install_error_handlers
from libs.common.logging import configure_logging
from libs.persistence.engine import make_engine, make_sessionmaker
from services.identity import container
from services.identity.router import router

configure_logging("identity")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = make_engine(settings)
    container.configure(make_sessionmaker(engine))
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Identity Service", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "identity"}

    return app


app = create_app()
