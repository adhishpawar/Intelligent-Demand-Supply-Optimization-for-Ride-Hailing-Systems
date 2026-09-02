# RideOps Intelligence — Backend

The ML decision platform for ride-hailing demand forecasting (FR-01), rebuilt
as a production FastAPI service on top of the original research codebase
(`ML_Development/`, `Inference/`). See [`../docs/IMPLEMENTATION_PLAN.md`](../docs/IMPLEMENTATION_PLAN.md)
for the full audit and roadmap, and [`../docs/GAP_TRACKING_LOG.md`](../docs/GAP_TRACKING_LOG.md)
for what's fixed vs. still open.

## What's here

- **Auth**: JWT access + refresh tokens, bcrypt password hashing, tenant
  self-registration.
- **RBAC**: four roles (`SUPER_ADMIN`, `TENANT_ADMIN`, `OPS_ANALYST`,
  `VIEWER`), enforced via `resource:action` permissions on every route.
- **Multi-tenancy**: every owned row carries `tenant_id`; every repository
  query filters by it explicitly. Proven by tests, not just declared by schema.
- **ML serving**: the FR-01 demand model, served through a single feature
  contract shared with training (`app/ml/contract.py`) — this is what the
  original codebase got wrong (training used 23 features, serving used 14).
- **Real feature store**: autoregressive (lag/rolling) features come from
  `demand_observations` in Postgres, not from the caller's request body.
- **Persistence**: PostgreSQL, Alembic migrations, forecasts persisted for
  later accuracy measurement.
- **Rate limiting**: Redis-backed, per-principal, on inference routes.
- **Observability**: request-ID middleware, structured logs.

## Quickstart

```bash
# 1. Python 3.12 venv at the repo root (../venv) already has the deps used
#    during development; to set up fresh:
pip install -r requirements-dev.txt

# 2. Postgres + Redis must be reachable at the URLs in .env (see .env.example)
cp .env.example .env

# 3. Apply migrations
python -m alembic upgrade head

# 4. Train the FR-01 model (writes models/demand_model.joblib + metadata)
python -m app.ml.cli train --test-days 5

# 5. Seed a demo tenant, city, zones, and load real demand history
python -m app.seed

# 6. Run
uvicorn app.main:app --reload --port 8100
```

Then:
- `GET /healthz`, `GET /readyz`
- `GET /docs` — OpenAPI/Swagger UI (auto-generated, always current — this is
  what closes API-08, the original codebase had none)
- Demo credentials printed by `app.seed`:
  `admin@rideops-demo.com` / `ChangeMe123!` (SUPER_ADMIN)
  `analyst@rideops-demo.com` / `ChangeMe123!` (OPS_ANALYST)

## Tests

```bash
python -m pytest tests/unit   # no external dependencies
python -m pytest tests/api    # needs Postgres + Redis reachable (see conftest.py)
```

56 tests as of this writing: contract/leakage/split regressions for the audit
findings, security (password/JWT) unit tests, and full API tests including
explicit tenant-isolation proofs (`tests/api/test_rbac_and_tenancy.py`).

## Directory layout

```
app/
  core/       settings, security (JWT/bcrypt), RBAC permissions, errors, rate limiting
  db/         SQLAlchemy models, session/engine setup
  domain/     enums (Role)
  ml/         contract, feature builder, registry, training, serving service, feature store
  repositories/  tenant-scoped data access
  schemas/    Pydantic request/response models
  services/   application services (auth, forecasting) composing the above
  api/v1/     FastAPI routers
  observability/  request-context middleware
  seed.py     demo data bootstrap
migrations/   Alembic
tests/
  unit/       no DB/network
  api/        full app, real Postgres
```

## What's deliberately not here yet

FR-02 through FR-10 (supply monitoring, zone recommendation, fleet
redistribution, feedback loop, online learning, hotspot detection) — see the
implementation plan's roadmap. This backend currently ships FR-01 (demand
forecasting) end-to-end, production-shaped.
