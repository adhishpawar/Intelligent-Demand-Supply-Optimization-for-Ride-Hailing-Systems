"""Wires `libs.common.errors.DomainError` into FastAPI's exception handling, once,
identically, for every service. A service that forgets to call `install_error_handlers`
will leak 500s for domain errors instead of clean 4xx bodies — caught by
`tests/architecture` smoke-checking every service app.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from libs.common.errors import DomainError


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": type(exc).__name__, "message": exc.message, "details": exc.details},
        )
