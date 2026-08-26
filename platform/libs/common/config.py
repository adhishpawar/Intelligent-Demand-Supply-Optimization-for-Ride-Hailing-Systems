"""Central settings. Every service imports `Settings` and gets the same env-var names,
the same defaults, and the same non-default infra ports (PLAN.md's Infrastructure
Executor step deliberately chose 5433/6380 instead of the Postgres/Redis defaults, so a
pre-existing local install can never be silently used by mistake).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- infra ---
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "ridehail"
    postgres_user: str = "ridehail"
    postgres_password: str = "ridehail_dev_pw"

    redis_host: str = "localhost"
    redis_port: int = 6380

    kafka_bootstrap: str = "localhost:9092"

    # EVENT_BUS = "kafka" | "inprocess" — PLAN §5.1 amendment AM-... fallback switch.
    event_bus: str = "inprocess"

    # LOCATION_HISTORY_BACKEND = "postgres" | "cassandra" — PLAN §4.4.
    location_history_backend: str = "postgres"

    # --- security ---
    jwt_secret: str = "dev-only-jwt-secret-change-in-prod-CHANGE-ME"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 3600
    jwt_refresh_ttl_seconds: int = 60 * 60 * 24 * 14
    internal_assertion_secret: str = "dev-only-internal-secret-change-in-prod-CHANGE-ME"
    internal_assertion_ttl_seconds: int = 30

    # --- service ports (native/single-mode run) ---
    gateway_port: int = 8000
    identity_port: int = 8001
    location_port: int = 8002
    matching_port: int = 8003
    trip_port: int = 8004
    pricing_port: int = 8005
    routing_port: int = 8006
    payment_port: int = 8007
    notification_port: int = 8008
    ratings_port: int = 8009

    # --- dispatch tuning (PLAN §5 / GAP AS-05, all per-city-overridable in city_config) ---
    offer_ttl_seconds: int = 15
    claim_ttl_seconds: int = 25  # > offer_ttl_seconds always (PLAN A3)
    max_dispatch_attempts: int = 3
    matching_deadline_seconds: int = 90
    candidate_radius_km: float = 3.0
    candidate_count: int = 20

    # --- run mode ---
    run_mode: str = "single"  # "single" | "compose"

    environment: str = "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
