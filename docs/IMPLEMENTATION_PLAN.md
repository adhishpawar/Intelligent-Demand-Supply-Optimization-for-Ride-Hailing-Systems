# IMPLEMENTATION PLAN
## Converting the Python/ML application into a production full-stack product

**Date:** 2026-09-02 · **Branch:** `feat/ml-platform-productionization`
**Companion docs:** [`GAP_TRACKING_LOG.md`](./GAP_TRACKING_LOG.md) · [`PRODUCTION_READINESS.md`](./PRODUCTION_READINESS.md)

---

## 1. Current system status

The repository contains **three distinct things** that are easy to confuse:

| # | What | Where | State |
|---|---|---|---|
| 1 | **Operational ride-hailing platform** | `platform/` | Production-shaped. 9 FastAPI services, React UI, Postgres/Redis/Kafka. Merged to `main` in PR #1. **Not the subject of this plan.** |
| 2 | **ML application (FR-01 demand forecasting)** | `Data_Processing/`, `ML_Development/`, `Inference/`, `models/`, `tests/` | Research-grade. Real algorithms, never ran end-to-end, serving layer non-functional. **This is what gets productionised.** |
| 3 | **Design documents** | `Sys/part1.md`, `Sys/part2.md` | Excellent, detailed target architecture for an ML Decision Platform (FR-01…FR-10). Describes the destination, not the present. |

### 1.1 Existing Python/ML architecture

```
Data_Processing/          raw NYC TLC CSVs (4 files, 1.6-1.9 GB each)
  trips_cleaned.csv       2.3 GB, 12,392,801 rows  <- preprocessing HAS been run
ML_Development/
  DataPreprocessor.py     365 ln  clean -> transform -> KMeans zones (k=20)
  FeatureEngineer.py      416 ln  trips -> (zone x 15min) matrix, 23 features
  DemandForecaster.py     408 ln  LightGBM + MLflow + promotion gate
  main.py                 191 ln  CLI orchestrator (preprocess/features/train/all)
Inference/app.py           64 ln  Flask, 3 routes  <- CANNOT START (see below)
models/
  kmeans_zones.joblib     49 MB   fitted KMeans (retains training data)
  zone_centroids.csv      497 B   20 zone centres
tests/test_fr01_full.py   504 ln  42 tests: 33 pass, 2 fail, 7 skip
```

**Python 3.12.2**, venv present, LightGBM 4.6 / scikit-learn 1.8 / pandas 2.3 / MLflow 3.11.

### 1.2 Existing API status

One Flask module, three routes, **no** validation, auth, error contract, or pagination.
It could not start: it called `joblib.load('models/demand_model.joblib')` at import time
and that file did not exist. Its two prediction routes were both defective independently
of that (see §3, P0-2 and P0-3).

### 1.3 Existing ML capabilities

| Capability | Reality |
|---|---|
| Data cleaning | **Works.** Domain-justified rules, verified by 33 passing tests. |
| Zone creation | **Works.** k=20 KMeans; centroids land on real NYC geography. |
| Feature engineering | **Works**, with one leakage defect (now fixed). 23 features. |
| Model training | **Now works.** Was never successfully run before this audit. |
| Model serving | **Did not work.** Wrong feature set, broken batch route, no artifact. |
| FR-02 … FR-10 | **Do not exist.** Only FR-01 has any code. |

### 1.4 Existing data architecture

There is none in the production sense: no database, no system of record, no persistence
of anything the system computes. All state is CSV files on a local disk, and the dataset
ends **2015-01-31**, so the system has no path to live data.

### 1.5 Existing frontend status

The ML platform has **no** frontend. (`platform/web-react/` is the operational
ride-hailing UI — rider/driver/admin — and serves a different audience.)

---

## 2. What I did before writing this plan

Rather than reason about the code from the outside, I ran it, because several claims
could only be settled by execution:

1. **Ran the test suite** → `2 failed, 33 passed, 7 skipped`. All 7 skips are the
   serving tests, skipped because no model existed.
2. **Ran the full pipeline end-to-end** for the first time in the project's history:
   12,392,801 trips → 59,539 (zone, window) rows → trained model `v20260902124734`.
3. **Measured the result** against the naive baselines the code itself defines.

**Outcome:**

| Metric | Value |
|---|---|
| MAE | **16.47** |
| RMSE | 26.32 |
| MAPE | 51.9% |
| Naive `lag1` baseline MAE | 18.91 |
| Zone×hour historical mean MAE | 69.28 |
| Mean demand per window | **207.8** (median 112, p90 471, max 2247) |
| **MAE as % of mean demand** | **7.9%** |

This settles a question the original code got wrong: `MAE_GATE = 4.0` is **~2% of mean
demand** and was never achievable. The model is genuinely useful (7.9% error, beats both
baselines) but would have been rejected forever by a gate set without reference to the
data. The gate must become scale-relative.

MAPE of 51.9% looks alarming but is an artefact of low-demand zones — zone 6 averages
13.3 rides/window against zone 19's 1011.4. Percentage error on small denominators
dominates the average. This argues for **per-zone evaluation**, not a single headline MAPE.

---

## 3. P0 production blockers

| # | Blocker | Why it is P0 | Status |
|---|---|---|---|
| P0-1 | **No model artifact existed**; serving imported it at module load | Service could not start at all | **FIXED — artifact now exists** |
| P0-2 | **Training/serving feature skew**: 23 columns trained vs 14 served, plus a name mismatch (`is_peak_hour` vs `is_peak`) | LightGBM binds positionally → shape error, or silently wrong scores | **FIXED — single contract module** |
| P0-3 | **`/predict/batch` structurally broken** (`pd.DataFrame([[dict]])`) | Endpoint could never work | **FIXED** |
| P0-4 | **Target leakage** in `zone_mean_demand` (whole-series mean) | Offline metrics overstate real performance | **FIXED — past-only expanding mean** |
| P0-5 | **No authentication** | Every endpoint public | OPEN |
| P0-6 | **No authorization / RBAC** | No separation of driver / ops / admin | OPEN |
| P0-7 | **No multi-tenancy** | Cross-tenant exposure on customer #2 | OPEN |
| P0-8 | **No database** | Nothing survives a restart; no system of record | OPEN |
| P0-9 | **No input validation / error contract** | Malformed request → HTTP 500 | OPEN |
| P0-10 | **Serving layer entirely untested** (7/7 skipped) | This is *why* P0-2 and P0-3 survived | IN PROGRESS |
| P0-11 | **No frontend** | Product has no user surface | OPEN |

---

## 4. P1 business-critical tasks

- Feature store for autoregressive features (today they came from the request body, and
  in practice were always `0` — every prediction was effectively calendar-only).
- Static fallback + measured 150 ms inference budget (NFR requirement).
- Persist forecasts so accuracy can be measured after the fact and FR-05's feedback loop
  can exist at all.
- Live data ingestion from the operational platform (dataset currently ends 2015-01-31).
- Scale-relative acceptance gate + per-zone evaluation.
- FR-02 (supply monitoring) and FR-03 (zone recommendation) — without these, the product
  is a forecasting API, not a demand–supply *optimisation* system.
- Rate limiting on inference routes.
- OpenAPI + generated TypeScript client.
- Structured logging, request IDs, metrics.
- Docker + CI.

---

## 5. Architecture council findings

### 5.1 Contrarian Architect — "what fails first?"

| Scale | First failure |
|---|---|
| **10×** | Per-request feature-store lookups. `predict_many` does one store call per zone; at 200 zones that is 200 round-trips per city refresh. Must become a single batched lookup. |
| **100×** | The 15-min batch forecast becomes the bottleneck, not inference. Precompute per city per window and serve from cache; the current design recomputes on demand. |
| **1000×** | The single-process model registry. Every replica loads a 49 MB (zone) + LightGBM artifact into its own heap and reloads independently — no coordinated rollout, so during a deploy different replicas serve different model versions with no way to tell which. |

**Also flagged:** the `zone_id` universe is a fitted KMeans with no version. Refitting
silently redefines every historical forecast and every stored row. This is a data-model
time bomb, not a modelling detail.

### 5.2 Graph & Taxonomy Purist

**Challenge sustained: this system does not currently need Neo4j or a vector database.**

The target architecture in the master prompt lists both. For *this* domain the entities
are zones, time windows, drivers, and demand counts. That is a **relational + time-series**
problem. A graph store earns its place when you need multi-hop traversal over
relationships; "which zone is adjacent to which" is a 20×20 adjacency matrix, not a graph
workload. A vector store earns its place with semantic similarity over unstructured
content; there is none here.

**Recommendation:** PostgreSQL only. Revisit if and when a requirement appears that
genuinely needs traversal or embeddings. Adopting them now would add two stateful systems
to operate, sync, and back up, for zero current capability. *(Recorded as Decision D-3.)*

### 5.3 Enterprise Expansionist — where the business value is

| Tier | Capability | Value |
|---|---|---|
| **MVP** | FR-01 demand forecast + FR-10 ops dashboard | City ops can *see* predicted demand. Sellable as intelligence. |
| **Production** | + FR-02 supply monitoring, FR-03 zone recommendation | The actual product: tells drivers where to go. Drives the utilisation/wait-time metrics. |
| **Enterprise** | + FR-04 fleet redistribution, FR-05/06 feedback + online learning, FR-08 hotspots | Self-improving optimisation; the defensible moat. |

FR-04 matters more than its position suggests: without fleet-level coordination, FR-03
sends *every* idle driver to the same hot zone and manufactures the oversupply it was
meant to fix. FR-03 without FR-04 is actively harmful at scale.

### 5.4 Naïve Outsider — the uncomfortable questions

- **Who pays?** The ride-hailing operator, for utilisation and wait-time improvement.
  Not riders, not drivers.
- **Why ML rather than a rules engine?** Justified — but only once there is live data.
  Today the model is trained on a fixed 2015 dataset and cannot see current demand, so
  it would ship as an expensive lookup table. **Live ingestion (DATA-05) is therefore not
  a "later" item; it is what makes the ML claim honest.**
- **Can a driver trust it?** Not yet — no explanation is produced (ML-15). A driver told
  to relocate with no reason will ignore it, and FR-05 will faithfully record that the
  recommendation failed.
- **What about driver privacy?** Precise location traces are the most sensitive data
  here, and there is currently no retention or anonymisation policy (SEC-02).
- **What happens when it is wrong?** Nothing today — forecasts are not persisted, so
  accuracy after the fact cannot even be measured (DATA-02).

### 5.5 Infrastructure Executor — what can actually be deployed

| Layer | Choice | Why |
|---|---|---|
| Backend | **FastAPI + uvicorn**, Python 3.12 | Matches `platform/`; async; OpenAPI for free. Keeps Python/ML core per the mandate. |
| Data | **PostgreSQL 16** + Alembic | System of record. Already operated for `platform/`. |
| Cache | **Redis** | Forecast cache with TTL — the NFR requires expiry, not silent staleness. |
| Queue | **Redis + RQ/APScheduler** initially | Celery is more machinery than the current job volume warrants. |
| ML | LightGBM, joblib artifact + sidecar metadata; MLflow **offline only** | Serving must not depend on a tracking server's uptime. |
| Frontend | **React + TypeScript + Vite**, separate repo | Per mandate. Typed client generated from OpenAPI. |
| Container | Docker, multi-stage | — |
| CI | GitHub Actions | Repo is already on GitHub. |

---

## 6. Production target architecture

```
        ML Platform Frontend  (SEPARATE REPO: React + TypeScript + Vite)
                    |
                  HTTPS / JSON
                    |
        ┌───────────▼─────────────────────────────────────┐
        │  FastAPI  /api/v1                               │
        │  validation → authn → RBAC → tenant scope       │
        └───────────┬─────────────────────────────────────┘
                    │
        ┌───────────▼──────────┐
        │ Application services │   forecasting · supply · recommendation · analytics
        └───────┬──────────┬───┘
                │          │
        ┌───────▼───┐  ┌───▼──────────┐
        │ ML layer  │  │ Repositories │
        │ contract  │  └───┬──────────┘
        │ features  │      │
        │ registry  │  ┌───▼────────────┐      ┌──────────┐
        │ service   │  │  PostgreSQL    │      │  Redis   │
        └───────────┘  │ system of      │      │ forecast │
                       │ record         │      │ cache/TTL│
                       └───┬────────────┘      └──────────┘
                           │
                    ┌──────▼──────────┐
                    │ Background jobs │  forecast refresh · retrain · aggregation
                    └──────┬──────────┘
                           │  (events / polling, non-blocking)
                    ┌──────▼────────────────────────┐
                    │ OPERATIONAL PLATFORM (platform/)│
                    │ must run with ML fully down     │
                    └─────────────────────────────────┘
```

**Non-negotiable constraint, carried from `Sys/part1.md` §1.5:** the operational platform
must book, match, and complete rides with **zero** dependency on this system being alive.
The ML platform is a consumer of its events and a producer of advisory signals — never a
blocking participant.

---

## 7. Implementation roadmap

Each phase ends with something demonstrable and a green test suite. No phase is marked
done on the basis of code that looks right.

| Phase | Scope | Exit criterion |
|---|---|---|
| **0. Audit** ✅ | Inspect, run, measure, document | These three documents exist; pipeline proven end-to-end |
| **1. ML core hardening** 🔄 | Contract, shared feature builder, registry + metadata, training path, leakage fix | Model trains; artifact verifiable; contract mismatch refused at load |
| **2. Persistence** | PostgreSQL, Alembic, tenant/user/zone/forecast tables | Migrations run clean from empty DB |
| **3. Auth + RBAC + tenancy** | JWT, roles, `resource:action` permissions, tenant scoping | Tests prove denial + cross-tenant isolation |
| **4. REST API v1** | Forecast, zone, supply, model, analytics, admin endpoints; validation; error contract; pagination; OpenAPI | OpenAPI published; serving tests execute (0 skipped) |
| **5. Feature store + serving hardening** | Real autoregressive lookups, batched; static fallback; latency budget; forecast persistence | Predictions no longer degraded; p99 measured |
| **6. Async jobs** | Scheduled forecast refresh, retraining, aggregation | Jobs idempotent + observable |
| **7. Frontend repo** | New React/TS/Vite repo, typed client, auth flow, API layer | Logs in against real API |
| **8. Frontend workflows** | Ops dashboard, zone explorer, forecast charts, supply heatmap, model health, admin | Every screen handles loading/empty/error/denied |
| **9. FR-02 + FR-03** | Supply monitoring; zone recommendation | Recommendation returned end-to-end with explanation |
| **10. Testing + security** | Integration, E2E, isolation, rate limiting, PII policy | Security review passes |
| **11. Observability** | Structured logs, request IDs, metrics, drift | Dashboards exist |
| **12. CI/CD + Docker** | Pipelines both repos, multi-stage images | Green on push |
| **13. Staging** | Deploy + smoke tests | Validated in a real environment |
| **14. Readiness gate** | Re-assess `PRODUCTION_READINESS.md` | Evidence-backed status raise |

**FR-04…FR-10** are explicitly *post-roadmap*. Scoping them in now would be planning
fiction; they need the feedback data that phases 5–9 produce.

---

## 8. Decisions required from the owner

These change the shape of the work, so I want them settled rather than assumed.

| ID | Decision | Options | My recommendation |
|---|---|---|---|
| **D-1** | **Frontend repo location** | ~~(a) separate repo; (b) folder in this repo~~ | ✅ **DECIDED (owner, 2026-09-02): a new separate folder on the local filesystem, with its own git repository** — sibling to this one, not nested inside it. Satisfies the mandate's "do NOT put React inside the Python repository". A GitHub remote can be added later. |
| **D-2** | **Product name** — "Glovatrix" is to be removed | Owner picks | Placeholder in use: **"RideOps"** / "RideOps Intelligence". Held in one constant so a rename is one line. **Tell me the real name and I will apply it everywhere.** |
| **D-3** | **Neo4j + vector DB** | (a) Build as the prompt lists; (b) PostgreSQL only, revisit on demand | **(b)** — see §5.2. No current requirement justifies two extra stateful systems. |
| **D-4** | **Live data** | (a) Integrate with `platform/` events now; (b) stay on the static 2015 dataset | **(a) eventually, and it is on the critical path** — see §5.4. Static data makes the ML claim hollow. Suggest phase 5–6. |
| **D-5** | **Scope of "production"** | (a) Real deployment target; (b) production-*shaped*, running locally | Assuming **(b)** unless told otherwise — it changes how much of phases 12–13 is real vs documented. |

---

## 9. Immediate next step

Phase 1 is partially complete (contract, feature builder, registry, training path,
leakage fix, and a trained artifact). Remaining Phase 1 work:

1. Recalibrate the acceptance gate to be scale-relative; add per-zone evaluation.
2. Port the 504-line test suite onto the new modules so the 7 skipped serving tests
   execute, plus new tests for the contract, leakage, and split guards.
3. Commit, then proceed to Phase 2 on approval.
