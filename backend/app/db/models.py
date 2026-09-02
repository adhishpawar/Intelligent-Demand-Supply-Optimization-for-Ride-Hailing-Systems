"""
SQLAlchemy models -- the system of record (DATA-01).

TENANCY RULE, ENFORCED IN CODE NOT JUST IN SCHEMA
--------------------------------------------------
Every tenant-owned table carries an explicit `tenant_id` column, even where it
is reachable transitively (e.g. a zone's tenant is technically derivable via
its city). The repository layer filters by `tenant_id` directly on every query
that touches these tables -- see `app/repositories/*.py` -- rather than joining
through a chain and hoping every future query remembers to. A column that is
always present is a column that can always be filtered on; a column that must
be joined to reach is a column some future query will forget to scope.

ZONE VERSIONING (ML-18)
-----------------------
`Zone.zone_model_version` records which fitted KMeans produced this zone's
`zone_index`. Refitting the clustering is a breaking change to what
`zone_index=7` *means* -- this column is what lets a future migration detect
"these zones are from an old spatial model" rather than silently mixing
incompatible zone definitions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import Role


def _uuid() -> str:
    return str(uuid.uuid4())


class Tenant(Base):
    """A ride-hailing operator account. The unit of billing and isolation."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    cities: Mapped[list["City"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_tenant_id", "tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    role: Mapped[Role] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    """Server-side record of issued refresh tokens, so one can be revoked.

    Storing a hash (never the raw token) means a leaked database dump does not
    itself grant session hijack -- the same principle as password hashing.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")


class City(Base):
    """A city the tenant operates in. Zones live under a city (ML-19)."""

    __tablename__ = "cities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_cities_tenant_slug"),
        Index("ix_cities_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped["Tenant"] = relationship(back_populates="cities")
    zones: Mapped[list["Zone"]] = relationship(back_populates="city", cascade="all, delete-orphan")


class Zone(Base):
    """A demand-forecasting zone within a city.

    `zone_index` is the integer the ML model actually consumes (0..N-1 from the
    fitted KMeans) -- it is NOT the primary key, because the model's zone space
    is versioned (`zone_model_version`) and a future re-fit must be able to
    coexist with or supersede the old one without an id collision.
    """

    __tablename__ = "zones"
    __table_args__ = (
        UniqueConstraint(
            "city_id", "zone_model_version", "zone_index", name="uq_zones_city_version_index"
        ),
        Index("ix_zones_city_id", "city_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    city_id: Mapped[str] = mapped_column(String(36), ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    zone_model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    zone_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    centroid_lat: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lng: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    city: Mapped["City"] = relationship(back_populates="zones")


class ModelRun(Base):
    """A trained model artifact's metadata (mirrors the on-disk sidecar, ML-09).

    Persisting this (rather than only the JSON file next to the artifact) is
    what makes `/api/v1/models` a real history endpoint instead of "whatever is
    on disk right now", and what a future promotion/rollback workflow would
    operate on.
    """

    __tablename__ = "model_runs"
    __table_args__ = (Index("ix_model_runs_is_active", "is_active"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    contract_version: Mapped[str] = mapped_column(String(50), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(50), nullable=False, default="lightgbm")
    feature_columns: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    training_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    test_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DemandObservation(Base):
    """Ground-truth aggregated demand per (city, zone, window).

    This is the training-time feature matrix's `demand` column, persisted, and
    it serves two purposes in production:

    1. It IS the feature store's source for lag/rolling features (ML-11) --
       serving computes `demand_lag1` etc. from real rows here, not from
       whatever the caller claims.
    2. It is what forecast accuracy gets measured against once real windows
       elapse (DATA-02 / FR-05's feedback loop).
    """

    __tablename__ = "demand_observations"
    __table_args__ = (
        UniqueConstraint("city_id", "zone_index", "window_start", name="uq_demand_obs_key"),
        Index("ix_demand_obs_lookup", "city_id", "zone_index", "window_start"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    city_id: Mapped[str] = mapped_column(String(36), ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    zone_index: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    demand: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="historical")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Forecast(Base):
    """A persisted prediction (DATA-02): what was predicted, by which model,
    when, and whether it was served in degraded mode.
    """

    __tablename__ = "forecasts"
    __table_args__ = (
        Index("ix_forecasts_lookup", "city_id", "zone_index", "window_start"),
        Index("ix_forecasts_tenant_id", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    city_id: Mapped[str] = mapped_column(String(36), ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    zone_index: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    predicted_demand: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requested_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Append-only record of security-relevant and mutating actions."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_tenant_id", "tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
