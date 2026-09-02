"""The API Gateway — PLAN §3.1 Layer 1: coarse, route-level, deny-by-default RBAC.

For every request: match the path against the deny-by-default route table (no match
= 404, never silently proxied); if the route requires auth, verify the caller's JWT
(rejecting missing/expired/wrong-signature tokens with 401) and check the role against
the route's allowed set (403 on mismatch); then forward to the target service with a
freshly signed internal principal assertion (`X-Internal-Principal`) instead of the
raw client JWT — this is what Layer 2 at each service independently re-verifies,
closing the loop PLAN §3.1 describes as "defense in depth."
"""
from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from libs.common.config import get_settings
from libs.common.ids import new_id
from libs.common.logging import configure_logging
from libs.security.internal_assertion import sign_principal
from libs.security.jwt_tokens import decode_token
from services.gateway.routes import build_routes, match_route, target_url

configure_logging("gateway")
logger = logging.getLogger("gateway")

app = FastAPI(title="API Gateway")
# The frontend (web/rider, web/driver, web/admin) is served as static files from a
# different origin than the gateway (PLAN §4.1: buildless static frontend, no build
# step) -- CORS is required for the browser to call across ports. Wide open (dev
# only): every origin/method/header allowed, since this is a local demo with no
# real users or credentials at stake.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
)
_routes = build_routes()

_HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade", "host", "content-length"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "gateway", "routes": len(_routes)}


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
async def proxy(full_path: str, request: Request):
    settings = get_settings()
    path = f"/{full_path}"
    request_id = new_id()

    matched = match_route(_routes, request.method, path)
    if matched is None:
        # Deny-by-default: an unmapped path is a 404, not a silent proxy-through.
        return JSONResponse(status_code=404, content={"error": "NotFound", "message": "no route for this path"})
    rule, _path_params = matched

    forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    forward_headers.pop("authorization", None)
    forward_headers.pop("x-internal-principal", None)  # never trust a client-supplied one
    forward_headers["x-request-id"] = request_id

    if rule.roles is not None:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse(status_code=401, content={"error": "Unauthorized", "message": "missing bearer token"})
        try:
            principal = decode_token(
                auth.split(" ", 1)[1], secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
            )
        except Exception as exc:
            return JSONResponse(status_code=401, content={"error": "Unauthorized", "message": str(exc)})

        if principal.role not in rule.roles:
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "message": f"role {principal.role.value} not permitted on this route"},
            )

        forward_headers["x-internal-principal"] = sign_principal(
            principal, secret=settings.internal_assertion_secret,
            ttl_seconds=settings.internal_assertion_ttl_seconds, request_id=request_id,
        )

    base_url = target_url(settings, rule.target)
    body = await request.body()

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            upstream = await client.request(
                request.method, f"{base_url}{path}", headers=forward_headers, content=body, params=request.query_params,
            )
        except httpx.RequestError as exc:
            logger.error("upstream unreachable", extra={"extra_fields": {"target": rule.target, "error": str(exc)}})
            return JSONResponse(status_code=502, content={"error": "BadGateway", "message": f"{rule.target} service unreachable"})

    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)
