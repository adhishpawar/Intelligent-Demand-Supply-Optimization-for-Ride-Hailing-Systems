"""Inbound-HTTP idempotency (PLAN §3.3, mechanism 1). A `FastAPI` dependency, not
middleware, because it needs the parsed principal and the raw body hash — used on
every POST that creates or moves state/money (ride request, driver accept, payment
trigger, cancel). A replayed `(key, endpoint)` returns the *stored* response; the same
key with a *different* body is a 409 — this is the case most naive implementations get
wrong, and it is unit-tested.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.errors import ConflictError


def hash_body(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_reserve(
        self, *, key: str, endpoint: str, principal_id: str | None, body_hash: str
    ) -> dict | None:
        """Returns the stored response dict if this (key, endpoint) was already
        completed. Raises ConflictError (409) if the key was reused with a different
        body. Returns None if this is a fresh key (caller should proceed and then call
        `store_result`)."""
        row = (
            await self._session.execute(
                text(
                    "SELECT request_hash, status_code, response_body FROM idempotency_keys "
                    "WHERE idempotency_key = :key AND endpoint = :endpoint"
                ),
                {"key": key, "endpoint": endpoint},
            )
        ).mappings().first()

        if row is None:
            return None

        if row["request_hash"] != body_hash:
            raise ConflictError(
                "idempotency key reused with a different request body",
                idempotency_key=key,
            )

        return {"status_code": row["status_code"], "body": dict(row["response_body"])}

    async def store_result(
        self, *, key: str, endpoint: str, principal_id: str | None, body_hash: str, status_code: int, response_body: dict
    ) -> None:
        await self._session.execute(
            text(
                """
                INSERT INTO idempotency_keys (idempotency_key, endpoint, principal_id, request_hash, status_code, response_body)
                VALUES (:key, :endpoint, :principal_id, :hash, :status, CAST(:body AS JSONB))
                ON CONFLICT (idempotency_key, endpoint) DO NOTHING
                """
            ),
            {
                "key": key,
                "endpoint": endpoint,
                "principal_id": principal_id,
                "hash": body_hash,
                "status": status_code,
                "body": json.dumps(response_body, default=str),
            },
        )
