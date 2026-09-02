"""The signed internal principal assertion (PLAN §3.1, Layer 2 — this is the piece that
makes "defense in depth" real rather than a diagram). The gateway, after verifying a
user's JWT, forwards `X-Principal: <payload>.<hmac>` to the downstream service. Each
service INDEPENDENTLY re-verifies the HMAC with a secret the gateway alone should hold,
and re-checks the role — it never trusts an unsigned or forged header. A request that
reaches a service directly on the internal network with a forged `X-Principal` and no
valid signature is rejected with 401 (PLAN test D3-3); a request with a raw user JWT
instead of this header is also accepted, because `libs.security.rbac` tries JWT
verification as a fallback path (PLAN test D3-2 concerns going the OTHER way — a
service call that bypasses the gateway must not accidentally succeed via a forged
header, which is exactly what independent HMAC verification prevents).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from libs.common.errors import UnauthorizedError
from libs.common.ids import utcnow
from libs.security.principal import Principal, Role


def _sign(payload_b64: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload_b64, hashlib.sha256).hexdigest()
    return mac


def sign_principal(principal: Principal, *, secret: str, ttl_seconds: int, request_id: str) -> str:
    now = utcnow()
    payload = {
        "user_id": principal.user_id,
        "role": principal.role.value,
        "phone": principal.phone,
        "request_id": request_id,
        "exp": int(now.timestamp()) + ttl_seconds,
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(payload_b64, secret)
    return f"{payload_b64.decode('ascii')}.{signature}"


def verify_principal(header_value: str, *, secret: str) -> Principal:
    try:
        payload_b64_str, signature = header_value.split(".", 1)
    except ValueError as exc:
        raise UnauthorizedError("malformed internal principal assertion") from exc

    payload_b64 = payload_b64_str.encode("ascii")
    expected_signature = _sign(payload_b64, secret)
    # constant-time compare — this header is exactly the kind of secret-adjacent value
    # a timing side-channel could otherwise leak.
    if not hmac.compare_digest(expected_signature, signature):
        raise UnauthorizedError("internal principal assertion signature mismatch")

    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise UnauthorizedError("malformed internal principal assertion payload") from exc

    if payload["exp"] < int(utcnow().timestamp()):
        raise UnauthorizedError("internal principal assertion expired")

    return Principal(user_id=payload["user_id"], role=Role(payload["role"]), phone=payload.get("phone", ""))
