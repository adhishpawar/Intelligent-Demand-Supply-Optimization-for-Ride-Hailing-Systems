"""
Seed script: bootstrap a demo tenant, city, zones, model run, and demand
history so the API and frontend have something real to show immediately.

Run from `backend/`:
    python -m app.seed

Idempotent: re-running upserts rather than duplicating (tenant/city/zone
creation checks first; demand_observations uses ON CONFLICT DO NOTHING).

WHY features.csv IS THE SOURCE OF demand_observations
-------------------------------------------------------
`Data_Processing/features.csv` already holds the exact (zone_id, window_start,
demand) triples the model was trained on. Loading these into
`demand_observations` means the feature store's live lag/rolling lookups
(`app.ml.feature_store.PostgresFeatureStore`) return the SAME numbers the
training pipeline saw for the same window -- serving and training agree not
just on which columns exist (that's `contract.py`), but on the values a real
lookback produces.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.base import Database
from app.db.models import City, ModelRun, Tenant, User, Zone
from app.domain.enums import Role
from app.ml.contract import CONTRACT_VERSION
from app.ml.registry import ModelMetadata
from app.repositories.observations import DemandObservationRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("seed")

DEMO_TENANT_SLUG = "rideops-demo"
DEMO_CITY_SLUG = "nyc"
ZONE_MODEL_VERSION = "kmeans20-nyc-2015q1"  # see ML-18: refit -> bump this
SUPER_ADMIN_EMAIL = "admin@rideops-demo.com"
SUPER_ADMIN_PASSWORD = "ChangeMe123!"
ANALYST_EMAIL = "analyst@rideops-demo.com"
ANALYST_PASSWORD = "ChangeMe123!"


async def seed() -> None:
    settings = get_settings()
    db = Database(settings)

    async with db.session_factory() as session:
        # --- tenant -----------------------------------------------------
        from sqlalchemy import select

        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == DEMO_TENANT_SLUG))
        ).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name="RideOps Demo Operator", slug=DEMO_TENANT_SLUG)
            session.add(tenant)
            await session.flush()
            logger.info("created tenant %s", tenant.id)
        else:
            logger.info("tenant already exists: %s", tenant.id)

        # --- super admin + analyst users ---------------------------------
        admin = (await session.execute(select(User).where(User.email == SUPER_ADMIN_EMAIL))).scalar_one_or_none()
        if admin is None:
            admin = User(
                tenant_id=tenant.id,
                email=SUPER_ADMIN_EMAIL,
                password_hash=hash_password(SUPER_ADMIN_PASSWORD),
                full_name="Platform Admin",
                role=Role.SUPER_ADMIN.value,
            )
            session.add(admin)
            logger.info("created SUPER_ADMIN user %s / %s", SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
        else:
            logger.info("SUPER_ADMIN already exists: %s", SUPER_ADMIN_EMAIL)

        analyst = (await session.execute(select(User).where(User.email == ANALYST_EMAIL))).scalar_one_or_none()
        if analyst is None:
            analyst = User(
                tenant_id=tenant.id,
                email=ANALYST_EMAIL,
                password_hash=hash_password(ANALYST_PASSWORD),
                full_name="Ops Analyst",
                role=Role.OPS_ANALYST.value,
            )
            session.add(analyst)
            logger.info("created OPS_ANALYST user %s / %s", ANALYST_EMAIL, ANALYST_PASSWORD)

        await session.flush()

        # --- city ---------------------------------------------------------
        city = (
            await session.execute(
                select(City).where(City.tenant_id == tenant.id, City.slug == DEMO_CITY_SLUG)
            )
        ).scalar_one_or_none()
        if city is None:
            city = City(tenant_id=tenant.id, name="New York City", slug=DEMO_CITY_SLUG, timezone="America/New_York")
            session.add(city)
            await session.flush()
            logger.info("created city %s", city.id)
        else:
            logger.info("city already exists: %s", city.id)

        # --- zones (from zone_centroids.csv) -------------------------------
        centroids_path = settings.model_dir / "zone_centroids.csv"
        existing_zone_count = (
            await session.execute(select(Zone).where(Zone.city_id == city.id))
        ).scalars().all()
        if not existing_zone_count and centroids_path.exists():
            centroids = pd.read_csv(centroids_path)
            zones = [
                Zone(
                    tenant_id=tenant.id,
                    city_id=city.id,
                    zone_model_version=ZONE_MODEL_VERSION,
                    zone_index=int(row.zone_id),
                    name=f"Zone {int(row.zone_id)}",
                    centroid_lat=float(row.centroid_lat),
                    centroid_lng=float(row.centroid_lng),
                )
                for row in centroids.itertuples()
            ]
            session.add_all(zones)
            await session.flush()
            logger.info("created %d zones for city %s", len(zones), city.id)
        else:
            logger.info("zones already present (%d) or centroids file missing", len(existing_zone_count))

        await session.commit()

        # --- model run ------------------------------------------------------
        meta_path = settings.demand_model_metadata_path
        if meta_path.exists():
            meta = ModelMetadata.from_file(meta_path)
            if meta.contract_version != CONTRACT_VERSION:
                logger.warning(
                    "metadata contract %s != serving contract %s -- skipping model_runs upsert",
                    meta.contract_version,
                    CONTRACT_VERSION,
                )
            else:
                existing_run = (
                    await session.execute(select(ModelRun).where(ModelRun.model_version == meta.model_version))
                ).scalar_one_or_none()
                if existing_run is None:
                    from sqlalchemy import update as sa_update

                    await session.execute(sa_update(ModelRun).values(is_active=False))
                    session.add(
                        ModelRun(
                            model_name=meta.model_name,
                            model_version=meta.model_version,
                            contract_version=meta.contract_version,
                            algorithm=meta.algorithm,
                            feature_columns=meta.feature_columns,
                            metrics=meta.metrics,
                            training_rows=meta.training_rows,
                            test_rows=meta.test_rows,
                            notes=meta.notes,
                            trained_at=pd.Timestamp(meta.trained_at).to_pydatetime(),
                            is_active=True,
                        )
                    )
                    await session.commit()
                    logger.info("recorded model run %s (activated)", meta.model_version)
                else:
                    logger.info("model run %s already recorded", meta.model_version)
        else:
            logger.warning("no model metadata at %s -- run `python -m app.ml.cli train` first", meta_path)

        # --- demand observations (from features.csv) -------------------------
        features_path = settings.data_dir / "features.csv"
        obs_repo = DemandObservationRepository(session)
        existing_count = await obs_repo.count_for_city(city.id)
        if existing_count > 0:
            logger.info("demand_observations already loaded for city (%d rows)", existing_count)
        elif features_path.exists():
            df = pd.read_csv(features_path, usecols=["zone_id", "window_start", "demand"], parse_dates=["window_start"])
            rows = [
                {
                    "city_id": city.id,
                    "zone_index": int(r.zone_id),
                    "window_start": r.window_start.to_pydatetime(),
                    "demand": int(r.demand),
                    "source": "historical",
                }
                for r in df.itertuples()
            ]
            batch_size = 5000
            total = 0
            for i in range(0, len(rows), batch_size):
                total += await obs_repo.bulk_upsert(rows[i : i + batch_size])
            await session.commit()
            logger.info("loaded %d demand_observations rows from features.csv", total)
        else:
            logger.warning("no features.csv at %s -- feature store will start empty", features_path)

    await db.dispose()
    logger.info("seed complete")
    logger.info("")
    logger.info("Login credentials:")
    logger.info("  SUPER_ADMIN : %s / %s", SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD)
    logger.info("  OPS_ANALYST : %s / %s", ANALYST_EMAIL, ANALYST_PASSWORD)


if __name__ == "__main__":
    asyncio.run(seed())
