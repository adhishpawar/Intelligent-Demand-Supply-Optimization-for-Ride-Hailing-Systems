# Intelligent Demand–Supply Optimization for Ride-Hailing Systems

**RideOps** — a working ride-hailing platform (bookings, matching, payments) plus a
separate ML intelligence layer that forecasts demand on top of it. This document is the
single entry point: what exists, how to run all of it, and a walkthrough of every user
role's workflow ("different cases").

For narrower/deeper detail than this file covers, see:
[`platform/README.md`](platform/README.md) ·
[`backend/README.md`](backend/README.md) ·
[frontend README](../23%20Ride%20hailing%20FrontEnd/README.md) ·
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) ·
[`docs/GAP_TRACKING_LOG.md`](docs/GAP_TRACKING_LOG.md) ·
[`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md)

---

## 1. What's actually here

Two products, deliberately decoupled — the ML layer is a bolt-on that must never be a
dependency the operational platform needs to stay alive.

| | **Operational Platform** | **ML Intelligence Platform** |
|---|---|---|
| **What it does** | Books, matches, tracks, and pays for real rides | Forecasts ride demand per zone/time-window |
| **Where** | [`platform/`](platform/) (this repo) | [`backend/`](backend/) (this repo) + a **separate** repo for its web UI |
| **Backend** | 9 FastAPI microservices + gateway | 1 FastAPI service |
| **Frontend** | `platform/web-react/` — rider/driver/admin app | [`23 Ride hailing FrontEnd/`](../23%20Ride%20hailing%20FrontEnd/) — ops-analyst dashboard |
| **Data** | Postgres `ridehail` DB, Redis, Kafka | Postgres `rideops_ml` DB, Redis |
| **Login** | Phone + OTP (dev mode: OTP returned in the response) | Email + password (JWT) |
| **Default port(s)** | Gateway `:8000`, web app `:8080` | API `:8100`, web app `:5173` |
| **Design doc** | [`Sys/part1.md`](Sys/part1.md) §2.1 (recap) | [`Sys/part1.md`](Sys/part1.md) §2.2 (FR-01..FR-10) |

They share the same native Postgres/Redis instances (different databases) purely to
avoid running two copies of the same infrastructure locally — not a dependency between
the two products.

---

## 2. Prerequisites

- **Windows**, PowerShell available (native infra binaries are Windows builds)
- **Python 3.12** — a venv already exists at the repo root (`venv/`)
- **Node.js 22+** and **npm** — for both React frontends
- Nothing else to install manually: `platform/run.ps1` downloads and initializes
  native Postgres/Redis/Kafka on first run

---

## 3. How to run everything

### 3.1 Operational Platform

```powershell
cd platform
.\run.ps1
```

One command, idempotent. From a clean clone with nothing installed, this downloads and
starts native Postgres (`:5433`)/Redis (`:6380`)/Kafka (`:9092`), runs migrations and
seed data, starts all 9 backend services + gateway (`:8000`), and starts the React
frontend (`:8080`) — then prints the seeded demo credentials. Re-running it when
everything is already up just confirms and exits.

```powershell
.\run.ps1 -Fresh      # wipe and reinitialize all data from scratch
.\run.ps1 -SkipSeed   # bring infra/services up without reseeding
.\stop.ps1            # stop everything run.ps1 started
```

Open **http://localhost:8080/login** — one login screen for all three roles; which
interface you land on is decided by the account's role, not a picker.

### 3.2 ML Intelligence Backend

```powershell
cd backend
pip install -r requirements-dev.txt
copy .env.example .env

# Postgres (:5433) + Redis (:6380) must be reachable -- if you already ran
# platform/run.ps1 above, they are. If not, run.ps1 alone (no other args)
# will bring native Postgres/Redis up even before its own services start.

python -m alembic upgrade head              # apply the schema
python -m app.ml.cli train --test-days 5    # train the FR-01 demand model (~2 min)
python -m app.seed                          # seed a demo tenant, city, zones, users, history
uvicorn app.main:app --reload --port 8100
```

Open **http://localhost:8100/docs** for the interactive API (Swagger UI).
`python -m app.seed` prints the login credentials it created (also listed in §5 below).

### 3.3 ML Intelligence Frontend

```powershell
cd "..\23 Ride hailing FrontEnd"
npm install
copy .env.example .env.local
npm run dev
```

Open **http://localhost:5173/login**. The dev server proxies `/api/*` to `:8100`, so
the backend above must be running first.

### 3.4 Run order, start to finish

```powershell
# Terminal 1
cd platform; .\run.ps1

# Terminal 2 (after terminal 1 finishes)
cd backend; python -m alembic upgrade head; python -m app.ml.cli train; python -m app.seed
uvicorn app.main:app --port 8100

# Terminal 3
cd "23 Ride hailing FrontEnd"; npm run dev
```

Alembic migration, training, and seeding are one-time setup — after the first run, just
start the `uvicorn` server in Terminal 2.

---

## 4. How to use it — walkthroughs by role

### 4.1 Operational Platform — Rider

1. Log in at `:8080/login` with a seeded rider phone (`+919000000001`) — dev mode
   returns the OTP directly in the response, type it straight into the OTP field.
2. Pick a vehicle type, enter pickup/drop, see the fare estimate, request the ride.
3. Watch matching happen live — a driver is assigned within seconds (a background
   process simulates driver movement); track them on the map.
4. Ride runs through its lifecycle (arriving → arrived → in progress → completed);
   payment fires automatically on completion.
5. Rate the driver. Ride history is queryable afterward.

**Cancellation case**: cancel after a driver is assigned — the UI shows the exact
cancellation fee before you confirm (not "a fee may apply").

### 4.2 Operational Platform — Driver

1. Log in with a seeded driver phone (`+918000000001`..`+918000000012`).
2. Toggle **Go online** (blocked with a clear message if KYC isn't verified — verify it
   as Admin first, see below).
3. An incoming ride offer appears with a countdown, fare estimate, and distance —
   Accept or Reject.
4. Advance the trip through its states (start navigation → confirm arrival → start →
   complete). Session earnings update from the real ledger, not an estimate.
5. Rate the rider once the trip settles.

### 4.3 Operational Platform — Admin

1. Log in with the seeded admin phone (`+910000000001`).
2. **Drivers panel**: verify KYC, suspend/reactivate accounts.
3. **Pricing panel**: edit a city's rate card (per-km/per-min rate, commission %,
   cancellation fee) or vehicle-type multipliers — changes apply to the *next* fare
   calculated, live, no redeploy.
4. **Dispatch tuning panel**: edit offer/claim TTLs, candidate radius/count per city.
5. **Trip detail**: inspect any trip's full audit trail; force-cancel if needed.
6. **Revenue tile**: real numbers from the ledger, not a mock.

### 4.4 Operational Platform — prove it without the UI

```powershell
cd platform
.\venv\Scripts\python.exe -m tools.demo
```

Drives a complete trip through the real running services headlessly — login, online,
request, real dispatch match, accept, full lifecycle, automatic payment via the live
Kafka consumer, rating — and prints the audit trail plus a ledger-balance check.

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -v
```

### 4.5 ML Platform — first time (become a tenant admin)

Either use the seeded demo account (§5), or self-register a new operator:

1. Go to `:5173/register`.
2. Fill in your operator name, your name, email, password — this creates a **new
   tenant** and makes you its **TENANT_ADMIN** in one step.
3. You land on the Dashboard. It's empty — no cities yet.

### 4.6 ML Platform — TENANT_ADMIN: onboard a city

1. **Cities & Zones** → **+ Add city** → name it, set a timezone.
2. **+ Add zone**, once per zone: a zone index (0, 1, 2, ...), the zone-model version
   string it belongs to (a label), and a centroid lat/lng.
   - The seeded demo city already has 20 real zones (NYC, from the trained model's
     actual zone map) — use it to see forecasts immediately without defining your own.
   - A zone must exist in this table before it can be forecast — a `zone_id` with no
     matching row returns `404`, verified live (`/forecasts/.../zones/50` → `NOT_FOUND`
     against a city with only zones 0–19).
   - Registering a `zone_index` the model was never trained on (verified: created
     index `25` against the NYC model) **does not get rejected or fall back** — the
     model happily scores it anyway, extrapolating on a `zone_id` value it has never
     seen. Treat any zone outside `0..19` on today's model as unvalidated output, not
     a supported case; per-tenant model retraining (so a new city gets its own
     zone-aware model) is on the roadmap, not yet built.
3. **Users** → **+ Invite user** → assign `OPS_ANALYST` or `VIEWER` for your team.

### 4.7 ML Platform — OPS_ANALYST / VIEWER: read a forecast

1. **Dashboard**: every zone in the city, ranked by predicted demand right now.
   A `degraded` tag means no recent observation history exists for that exact
   window — a calendar-only estimate, clearly flagged rather than hidden.
2. **Forecasts**: pick a city + zone, see the next 2 hours as a chart, plus recent
   forecast history. OPS_ANALYST can hit **Re-score now**; VIEWER is read-only.
3. **Model Health**: what model version is live, its accuracy vs. a naive baseline,
   and every past training run.

### 4.8 ML Platform — get a forecast for a specific window (API, not UI)

Useful for the historical/high-confidence case — a window inside the training data's
actual date range (Jan 2015) returns a real, non-degraded prediction:

```powershell
# after logging in via /api/v1/auth/login and capturing access_token
curl http://localhost:8100/api/v1/forecasts/cities/<city_id>/zones/19?window_start=2015-01-30T18:00:00 `
  -H "Authorization: Bearer <access_token>"
```

### 4.9 ML Platform — train a new model version

```powershell
cd backend
python -m app.ml.cli train --test-days 5
python -m app.seed   # re-run: activates the new model_run row, loads any new history
```

`/api/v1/models/history` (or the Model Health page) shows every version trained, which
one is active, and its metrics vs. the baseline it had to beat.

### 4.10 ML Platform — SUPER_ADMIN: cross-tenant oversight

Log in as the seeded SUPER_ADMIN (§5). Unlike `TENANT_ADMIN`, this role can pass
`?tenant_id=` on list endpoints to inspect a *different* tenant's cities — every other
role is hard-pinned to its own token's tenant regardless of what it requests, which is
the actual multi-tenancy enforcement (proven by `backend/tests/api/test_rbac_and_tenancy.py`,
not just declared).

### 4.11 Run the ML platform's tests

```powershell
cd backend
python -m pytest tests/unit    # no external dependencies
python -m pytest tests/api     # needs Postgres + Redis reachable
```

---

## 5. Seeded credentials

| Platform | Role | Identifier | Password / OTP |
|---|---|---|---|
| Operational | Admin | `+910000000001` | dev-mode OTP in response |
| Operational | Rider | `+919000000001`..`+919000000005` | dev-mode OTP in response |
| Operational | Driver | `+918000000001`..`+918000000012` | dev-mode OTP in response |
| ML Intelligence | SUPER_ADMIN | `admin@rideops-demo.com` | `ChangeMe123!` |
| ML Intelligence | OPS_ANALYST | `analyst@rideops-demo.com` | `ChangeMe123!` |

Change the ML platform password after first login in a real deployment — this is a
seed-script default, not something `.env` protects.

---

## 6. Troubleshooting

- **A Windows process won't die with `pkill`** — this environment's `pkill` doesn't
  reliably signal native Windows processes from Git Bash. Find the real PID and use
  `taskkill //F //PID <pid>` instead (confirmed necessary during this build — a stale
  server kept answering requests with pre-fix code after a plain `pkill`).
- **Redis: `unknown command 'HELLO'`** — the native Redis binary here is 5.0.14,
  which predates RESP3. The ML backend's client is already pinned to
  `protocol=2`; if you write a new Redis client against this instance, do the same.
- **Docker: model not found inside the container** — `backend/Dockerfile` bakes
  `MODEL_DIR=/app/models`; mount or copy the trained artifact there. (This was a real
  bug found by actually running the built image — `Settings.model_dir`'s default is
  computed relative to the repo layout, which isn't meaningful once code is flattened
  into `/app`.)
- **Path-like environment variable values getting mangled on `docker run`** — Git
  Bash's MSYS layer rewrites strings that look like absolute Unix paths, including env
  var *values* that aren't paths on the host. Prefix the command with
  `MSYS_NO_PATHCONV=1` when this bites.
- **ML forecast always shows `degraded: true`** — expected for "now": the training
  dataset ends 2015-01-31, so there's no recent observation history for the current
  date. Query a window inside Jan 2015 (§4.8) to see a fully-informed prediction.

---

## 7. Project layout

```
platform/                      Operational Platform (9 services + gateway + web UI)
backend/                       ML Intelligence Platform backend (FastAPI)
../23 Ride hailing FrontEnd/   ML Intelligence Platform web UI (separate repo)
ML_Development/, Inference/    Original research-stage ML code (superseded by backend/app/ml/, kept as reference)
Data_Processing/                Raw + cleaned trip data, feature matrices
models/                        Trained model artifacts + metadata sidecar
Sys/                           Engineering design docs (HLD/LLD for both platforms)
docs/                          Audit findings, gap tracking, production-readiness assessment
tests/                         Original ML unit test suite (see backend/tests/ for the current one)
```
