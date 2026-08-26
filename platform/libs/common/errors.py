"""Shared exception hierarchy. Every service maps these to HTTP status codes the same way
(see `libs.common.http_errors`), so a 409 means the same thing everywhere in this system.
"""
from __future__ import annotations


class DomainError(Exception):
    """Base class for all expected, handled domain failures."""

    http_status: int = 400

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(DomainError):
    http_status = 404


class ConflictError(DomainError):
    """A precondition/version/uniqueness conflict — e.g. optimistic lock lost a race,
    or an idempotency key was reused with a different body."""

    http_status = 409


class ForbiddenError(DomainError):
    """Principal is authenticated but not authorized for this role or this resource."""

    http_status = 403


class UnauthorizedError(DomainError):
    """Missing, malformed, expired, or badly-signed credentials."""

    http_status = 401


class IllegalTransitionError(ConflictError):
    """Raised by the trip state machine when `(state, event)` has no transition.

    Deliberately a `ConflictError` (409), not a generic 400 — an illegal transition
    almost always means the caller's view of the world is stale (they think the trip
    is still MATCHING; it already completed), which is a conflict, not a bad request.
    """


class ValidationError(DomainError):
    http_status = 422


class RateLimitedError(DomainError):
    """Caller exceeded a request-rate ceiling — e.g. too many OTP requests for the
    same phone number in a short window. Distinct from ForbiddenError: the caller is
    not doing anything unauthorized, just doing it too fast; the fix is "wait", not
    "you are not allowed to do this."
    """

    http_status = 429
