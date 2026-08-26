from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common.config import get_settings
from libs.common.http_errors import install_error_handlers
from libs.common.logging import configure_logging
from libs.persistence.engine import make_engine, make_sessionmaker
from services.location import container
from services.location.router import router
from services.location.sweeper import StalenessSweeper

configure_logging("location")

_sweeper: StalenessSweeper | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _sweeper
    settings = get_settings()
    engine = make_engine(settings)
    await container.configure(make_sessionmaker(engine), settings)

    _sweeper = StalenessSweeper(container.get_location_service()._index)  # noqa: SLF001 - composition root wiring
    _sweeper.start()

    yield

    _sweeper.stop()
    await container.shutdown()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Location Service", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health")
    async def health():
        lag = _sweeper.evicted_count if _sweeper else 0
        return {"status": "ok", "service": "location", "sweeper_evictions_total": lag}

    return app


app = create_app()
