"""
Central configuration. Every module reads settings from here; nothing reads
`os.environ` directly and nothing hardcodes a path.

WHY THIS MATTERS HERE SPECIFICALLY
----------------------------------
The pre-production code hardcoded `joblib.load('models/demand_model.joblib')`
at import time, relative to the current working directory. That meant the
service started successfully or not depending on which directory you launched
it from, and there was no way to point staging at a different artifact without
editing source. Paths below are absolute, derived from the repo root, and
overridable by environment variable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> repo root
REPO_ROOT: Path = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="",
    )

    # --- identity ---
    app_name: str = "RideOps Intelligence"
    environment: str = "dev"  # dev | staging | prod
    api_v1_prefix: str = "/api/v1"

    # --- server ---
    host: str = "0.0.0.0"
    port: int = 8100

    # --- security ---
    # Deliberately not given a usable default: a real secret must be injected.
    # `validate_secrets()` refuses to start in staging/prod with the dev value.
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14
    bcrypt_rounds: int = 12

    # --- CORS (frontend is a separate origin by design) ---
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- database ---
    database_url: str = "postgresql+asyncpg://ridehail:ridehail_dev_pw@localhost:5433/rideops_ml"

    # --- cache / rate limiting ---
    redis_url: str = "redis://localhost:6380/1"
    inference_rate_limit_per_minute: int = 60

    # --- paths ---
    data_dir: Path = REPO_ROOT / "Data_Processing"
    model_dir: Path = REPO_ROOT / "models"

    # --- ML serving ---
    # Hard ceiling on a single inference call. The design doc's NFR table
    # specifies a 150ms timeout with a static fallback rather than an error.
    inference_timeout_ms: int = 150
    forecast_cache_ttl_seconds: int = 120
    # A forecast older than this is refused rather than served: the NFR is
    # "staleness must be visible/expire, never silently wrong".
    forecast_max_staleness_seconds: int = 900
    max_forecast_horizon_windows: int = 8  # 8 x 15min = 2h, per FR-01
    max_batch_zones: int = 200

    # --- operational platform integration (bolt-on, must degrade gracefully) ---
    ops_platform_base_url: str = "http://localhost:8000"
    ops_platform_timeout_seconds: float = 2.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        # Allow CORS_ORIGINS="http://a,http://b" from the environment.
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}

    @property
    def demand_model_path(self) -> Path:
        return self.model_dir / "demand_model.joblib"

    @property
    def demand_model_metadata_path(self) -> Path:
        return self.model_dir / "demand_model.metadata.json"

    @property
    def zone_kmeans_path(self) -> Path:
        return self.model_dir / "kmeans_zones.joblib"

    @property
    def zone_centroids_path(self) -> Path:
        return self.model_dir / "zone_centroids.csv"

    def validate_secrets(self) -> None:
        """Refuse to boot a non-dev environment with development secrets.

        Called from the app factory rather than at import, so tests and local
        tooling can construct Settings freely.
        """
        if not self.is_production and self.environment.lower() != "staging":
            return
        if self.jwt_secret == "dev-only-insecure-secret-change-me":
            raise RuntimeError(
                f"JWT_SECRET is still the development default in environment="
                f"{self.environment!r}. Set a real secret before deploying."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
