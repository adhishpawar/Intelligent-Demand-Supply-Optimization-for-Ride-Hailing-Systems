"""The gateway's route table (PLAN §3.1 Layer 1): every route explicitly declares its
required roles, and the table is **deny-by-default** — a request whose path matches
nothing here is rejected, rather than the table being an allowlist of things to
*block*. This is what makes "unknown routes are denied by default" true rather than
aspirational: a new backend endpoint is invisible through the gateway until someone
deliberately adds a line here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from libs.common.config import Settings
from libs.security.principal import Role


@dataclass(frozen=True)
class RouteRule:
    method: str  # "*" for any
    pattern: re.Pattern
    target: str  # settings attribute name for the base URL, e.g. "identity"
    roles: frozenset[Role] | None  # None = public, no auth required


def _compile(path_template: str) -> re.Pattern:
    # "/v1/trips/{trip_id}/respond" -> r"^/v1/trips/(?P<trip_id>[^/]+)/respond$"
    pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path_template)
    return re.compile(f"^{pattern}$")


ALL_AUTH = frozenset({Role.RIDER, Role.DRIVER, Role.ADMIN})


def build_routes() -> list[RouteRule]:
    R = RouteRule
    C = _compile
    return [
        # --- Identity: public auth endpoints ---
        R("POST", C("/v1/auth/register"), "identity", None),
        R("POST", C("/v1/auth/otp/request"), "identity", None),
        R("POST", C("/v1/auth/otp/verify"), "identity", None),
        R("POST", C("/v1/auth/refresh"), "identity", None),
        # --- Identity: authenticated ---
        R("GET", C("/v1/users/me"), "identity", ALL_AUTH),
        R("GET", C("/v1/users/{user_id}"), "identity", ALL_AUTH),
        R("GET", C("/v1/drivers/{driver_id}"), "identity", ALL_AUTH),
        R("PATCH", C("/v1/drivers/{driver_id}/kyc"), "identity", frozenset({Role.ADMIN})),
        # --- Location ---
        R("PATCH", C("/v1/drivers/{driver_id}/status"), "location", frozenset({Role.DRIVER, Role.ADMIN})),
        R("PATCH", C("/v1/drivers/{driver_id}/location"), "location", frozenset({Role.DRIVER, Role.ADMIN})),
        R("GET", C("/v1/drivers/{driver_id}/live"), "location", ALL_AUTH),
        # --- Trip ---
        R("POST", C("/v1/trips"), "trip", frozenset({Role.RIDER})),
        R("GET", C("/v1/trips/{trip_id}"), "trip", ALL_AUTH),
        R("GET", C("/v1/trips/{trip_id}/audit"), "trip", ALL_AUTH),
        R("POST", C("/v1/trips/{trip_id}/respond"), "trip", frozenset({Role.DRIVER})),
        R("POST", C("/v1/trips/{trip_id}/start-navigation"), "trip", frozenset({Role.DRIVER, Role.ADMIN})),
        R("POST", C("/v1/trips/{trip_id}/confirm-arrival"), "trip", frozenset({Role.DRIVER, Role.ADMIN})),
        R("POST", C("/v1/trips/{trip_id}/start"), "trip", frozenset({Role.DRIVER, Role.ADMIN})),
        R("POST", C("/v1/trips/{trip_id}/complete"), "trip", frozenset({Role.DRIVER, Role.ADMIN})),
        R("POST", C("/v1/trips/{trip_id}/cancel"), "trip", frozenset({Role.RIDER, Role.ADMIN})),
        R("POST", C("/v1/trips/{trip_id}/driver-cancel"), "trip", frozenset({Role.DRIVER})),
        # --- Pricing ---
        R("POST", C("/v1/pricing/estimate"), "pricing", ALL_AUTH),
        R("GET", C("/v1/pricing/surge/{city_id}"), "pricing", ALL_AUTH),
        # --- Payment ---
        R("POST", C("/v1/payments/{trip_id}/charge"), "payment", frozenset({Role.ADMIN})),
        R("GET", C("/v1/payments/{trip_id}"), "payment", ALL_AUTH),
        R("GET", C("/v1/payments/{trip_id}/ledger"), "payment", ALL_AUTH),
        # --- Ratings ---
        R("POST", C("/v1/trips/{trip_id}/rate"), "ratings", frozenset({Role.RIDER, Role.DRIVER})),
        R("GET", C("/v1/ratings/{user_id}"), "ratings", ALL_AUTH),
        # --- Notifications ---
        R("GET", C("/v1/notifications"), "notification", ALL_AUTH),
        R("POST", C("/v1/notifications/{notification_id}/read"), "notification", ALL_AUTH),
        # --- Admin analytics (read-only aggregate views) ---
        R("GET", C("/v1/admin/heatmap/{city_id}"), "location", frozenset({Role.ADMIN})),
    ]


def target_url(settings: Settings, target: str) -> str:
    port = getattr(settings, f"{target}_port")
    return f"http://localhost:{port}"


def match_route(routes: list[RouteRule], method: str, path: str) -> tuple[RouteRule, dict[str, str]] | None:
    for rule in routes:
        if rule.method != "*" and rule.method != method:
            continue
        m = rule.pattern.match(path)
        if m:
            return rule, m.groupdict()
    return None
