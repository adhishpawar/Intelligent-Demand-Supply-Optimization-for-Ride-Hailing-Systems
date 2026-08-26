from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.common.config import get_settings
from libs.common.http_errors import install_error_handlers
from libs.common.logging import configure_logging
from libs.persistence.engine import make_engine, make_sessionmaker
from services.trip import container
from services.trip.dispatch_orchestrator import DispatchOrchestrator
from services.trip.router import router

configure_logging("trip")

_orchestrator: DispatchOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator
    settings = get_settings()
    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)
    container.configure(sessionmaker, settings)

    _orchestrator = DispatchOrchestrator(container.get_trip_service(), sessionmaker)
    _orchestrator.start()

    yield

    _orchestrator.stop()
    await container.shutdown()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Trip Service", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "trip"}

    return app


app = create_app()
