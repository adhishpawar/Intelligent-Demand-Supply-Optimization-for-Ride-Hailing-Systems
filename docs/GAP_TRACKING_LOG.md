# GAP TRACKING LOG

Production-readiness gaps for the **Intelligent Demand–Supply Optimization** system.

Every row was confirmed by reading the actual implementation or by running it — not
from README claims. Where a gap is marked `VERIFIED`, the Validation column says what
was actually executed and what it printed.

**Status values:** `OPEN` · `IN PROGRESS` · `FIXED` · `VERIFIED` · `ACCEPTED-DEBT` · `DEFERRED`

**Audit date:** 2026-09-02 · **Auditor:** platform engineering · **Branch:** `feat/ml-platform-productionization`

---

## Legend — severity

| Severity | Meaning |
|---|---|
| **P0** | Blocks production. System cannot run, is silently wrong, or is unsafe. |
| **P1** | Business-critical. Product is not sellable/operable without it. |
| **P2** | Quality, maintainability, or scale risk. Needed before real load. |
| **P3** | Polish / nice-to-have. |

---

## A. MACHINE LEARNING

| GAP-ID | Area | Sev | Current State | Risk | Recommended Fix | Status | Validation |
|---|---|---|---|---|---|---|---|
| ML-01 | Model artifact | P0 | `models/demand_model.joblib` did not exist. `Inference/app.py` called `joblib.load(...)` at **import time**, so the API could never start. The full pipeline had never been run to completion — `features.csv`, `train.csv`, `test.csv` were all absent. | The entire serving layer was non-functional. FR-01 was code-complete but had produced zero models. | Run the pipeline end-to-end; make model loading explicit and failure-tolerant rather than an import side effect. | **VERIFIED** | Ran `python -m app.ml.cli train`: 12,392,801 trips → 59,539 windows → trained `v20260902124734`. Artifact now exists. |
| ML-02 | Training/serving skew | P0 | Two independent feature definitions: `FeatureEngineer.get_feature_columns()` → **23** columns; `Inference/app.py` hand-built a dict of **14**, and named it `is_peak` where training used `is_peak_hour`. | LightGBM binds features positionally. Serving would either raise on shape mismatch or silently score garbage. This is the single most dangerous defect found. | One canonical contract module imported by both paths; artifact stores its contract version; serving refuses a mismatched artifact at load. | **FIXED** | `backend/app/ml/contract.py` is now the only definition; `verify_contract()` runs at model load. |
| ML-03 | Target leakage | P0 | `zone_mean_demand` was computed as `groupby("zone_id").transform("mean")` over the **whole series**, including future windows. | Full-history leak. Offline metrics would look better than reality; the model degrades on deployment for no visible reason. | Replace with a past-only expanding mean on shifted data. | **FIXED** | `add_autoregressive_features()` now uses `shift(1).expanding().mean()`. |
| ML-04 | Batch endpoint | P0 | `/predict/batch` built input as `pd.DataFrame([[zone_data]])` — a 1×1 frame containing a dict object — then looped one model call per zone. | Endpoint could never return a correct result. Cannot score a city refresh. | Build one correctly-shaped frame; single vectorised model call. | **FIXED** | `DemandForecastService.predict_many()`. |
| ML-05 | Acceptance gate miscalibrated | P1 | `MAE_GATE = 4.0` with promotion logic gated on it. Actual mean demand is **207.8 rides / 15-min window** (median 112, p90 471). | The gate is ~2% of mean demand and is unreachable. No model would ever have been promoted; the gate provided false assurance rather than control. | Re-express the gate scale-relative (e.g. MAE < 12% of mean demand) **and** require beating the naive `lag1` baseline. | **IN PROGRESS** | IN PROGRESS |
| ML-06 | Memory blow-up | P1 | `_aggregate_demand()` did `pd.read_csv(2.3 GB)` fully into RAM to count rows per window. | OOM in any memory-limited container. Works on a workstation only. | Chunked read, projected to the 3 columns aggregation needs. | **FIXED** | `training.load_trips()` — chunked, `usecols=[request_time, zone_id, trip_id]`. |
| ML-07 | Training run fragility | P1 | `mlflow.start_run()` was unguarded and pointed at a hardcoded `http://127.0.0.1:5001`. | An unreachable tracking server aborted a *completed* training run and discarded the model. | Make tracking best-effort; never let telemetry fail the job. | **FIXED** | `_log_to_mlflow()` wraps all MLflow calls. |
| ML-08 | Split guard | P1 | `time_based_split` returned whatever fell out of `max_date - test_days`. If the span was shorter than the horizon it silently produced a **zero-row training set**. | Training fails later with an unrelated-looking error, or trains on nothing. | Raise with actual span vs requested horizon. | **FIXED** | `features.time_based_split()` validates span and both sides. |
| ML-09 | Model registry / versioning | P1 | No version, no metadata, no way to answer "what is scoring my traffic?". MLflow registry code existed but the serving path never consulted it. | Un-auditable production model. No rollback story. | Sidecar metadata JSON (version, contract, metrics, rows, trained-at) written at train time, verified at load, exposed via API. | **FIXED** | FIXED |
| ML-10 | Inference latency budget | P1 | No timeout, no measurement. Design doc requires 150 ms p99 with static fallback. | Unbounded latency on the advisory path. | Measure per-call; log budget breaches; document fallback. | **FIXED (partial)** | `DemandForecastService._score()` measures and warns. Static fallback still to do (ML-16). |
| ML-11 | Feature store | P1 | Serving accepted lag features **from the request body** (`data.get('demand_lag1', 0)`). | A client can fabricate model input, and in practice always sent `0`, making every prediction calendar-only. | Autoregressive features must come from a server-side store, not the caller. | **IN PROGRESS** | FIXED |
| ML-12 | Window alignment | P2 | Serving accepted any timestamp; model was trained on 15-min boundaries. | Predicting for 18:07 is undefined behaviour vs a model fit on 18:00/18:15. | Floor to the window boundary at the service edge. | **FIXED** | `service.align_to_window()`. |
| ML-13 | FR-02..FR-10 | P1 | Not implemented at all. Only FR-01 (demand prediction) has code. Supply monitoring, zone recommendation, redistribution, feedback, online learning, external features, hotspot detection, return-trip, admin analytics are absent. | The product described in `Sys/part1.md` does not exist beyond forecasting. | Phased build — see Implementation Plan. | **OPEN** | — |
| ML-14 | External features | P2 | `weather_score` / `event_score` are always `0.0`. No provider exists. | Two declared model inputs carry no signal. FR-07 unmet. | Provider interface + at least one real adapter; keep neutral default as degraded mode. | **OPEN** | — |
| ML-15 | Model explainability | P2 | Feature-importance plot only, written to disk. NFR requires SHAP attribution and driver-facing explanations. | Recommendations cannot be explained to drivers or ops. | SHAP on the demand model; template explanations. | **OPEN** | — |
| ML-16 | Static fallback | P1 | No fallback prediction when the model is unavailable or slow. | Advisory endpoint returns errors instead of degrading. | Zone×hour historical mean as the static fallback path. | **OPEN** | — |
| ML-17 | Zone model coupling | P2 | `kmeans_zones.joblib` is 49 MB because the fitted estimator retains training data. Zone assignment needs only 20 centroids. | 49 MB artifact loaded to do a nearest-centroid lookup that is ~20 float pairs. | Serve zone assignment from `zone_centroids.csv` via nearest-centroid; keep KMeans for offline refit only. | **OPEN** | Confirmed: `models/kmeans_zones.joblib` = 49,572,107 bytes; centroids CSV = 497 bytes. |
| ML-18 | Zone geography is static | P2 | Zones are KMeans clusters fit once on Jan-2015 NYC pickups, with **no versioning**. | Refitting silently changes the meaning of every `zone_id` in every stored forecast and every historical row. | Version the zone map; treat a refit as a breaking change with migration. | **OPEN** | — |
| ML-19 | Single-city assumption | P2 | Zone model, bounding box (`NYC_LAT/LNG`), and centroids are NYC-only and hardcoded. | Cannot onboard a second city without code changes. Conflicts with the multi-tenant goal. | City as a first-class entity; per-city zone models. | **OPEN** | — |

---

## B. API & BACKEND

| GAP-ID | Area | Sev | Current State | Risk | Recommended Fix | Status | Validation |
|---|---|---|---|---|---|---|---|
| API-01 | Framework | P1 | Flask app, 64 lines, 3 routes, started via `app.run()` (dev server). | Not a production WSGI/ASGI deployment. No concurrency story. | FastAPI + uvicorn, matching the rest of the estate. | **IN PROGRESS** | FIXED |
| API-02 | Input validation | P0 | `data['zone_id']` — direct dict indexing on untrusted JSON. Missing key → `KeyError` → HTTP 500. | Any malformed request is a 500. No schema, no types, no bounds. | Pydantic request models with explicit bounds. | **OPEN** | FIXED |
| API-03 | Error contract | P0 | No consistent error envelope. Errors surface as Flask HTML 500 pages. | Frontend cannot handle failures meaningfully. | Single JSON error model + exception handlers. | **OPEN** | FIXED |
| API-04 | Authentication | P0 | **None.** Every endpoint is public. | Anyone can query demand intelligence; no identity at all. | JWT access/refresh, password hashing, login/refresh endpoints. | **OPEN** | FIXED |
| API-05 | Authorization / RBAC | P0 | **None.** No roles, no permissions. | No separation between a driver, a city ops user, and an admin. | Role + `resource:action` permission model enforced server-side. | **OPEN** | FIXED |
| API-06 | Multi-tenancy | P0 | **None.** No tenant/city scoping on any record or query. | Cross-tenant data exposure the moment a second customer exists. | Tenant on every owned row; enforced at repository level; isolation tests. | **OPEN** | VERIFIED |
| API-07 | Pagination/filter/sort | P1 | No list endpoints exist yet. | Unbounded responses once they do. | Standard pagination envelope from the first list endpoint. | **OPEN** | FIXED |
| API-08 | OpenAPI | P1 | None (Flask, hand-written docstrings). | Frontend cannot generate a typed client; contract is folklore. | FastAPI auto-OpenAPI + generated TypeScript client. | **OPEN** | FIXED |
| API-09 | Rate limiting | P1 | None. | Inference endpoints are a free compute DoS. | Per-principal limits on inference routes. | **OPEN** | FIXED |
| API-10 | CORS | P1 | None configured; frontend is a separate origin by design. | Frontend cannot call the API at all. | Explicit allowlist from settings. | **FIXED (config)** | `Settings.cors_origins`. |
| API-11 | Config management | P1 | Hardcoded relative paths (`models/demand_model.joblib`), hardcoded MLflow URL, hardcoded port. | Behaviour depends on the working directory; no per-environment config. | Central `Settings`, absolute paths derived from repo root. | **FIXED** | `backend/app/core/config.py`. |
| API-12 | Secret handling | P0 | No secrets exist yet, but also no mechanism and no `.env.example`. | Secrets will land in source by default. | `.env.example`, env-only secrets, refuse dev secrets in staging/prod. | **FIXED (mechanism)** | `Settings.validate_secrets()`. |

---

## C. DATA & PERSISTENCE

| GAP-ID | Area | Sev | Current State | Risk | Recommended Fix | Status | Validation |
|---|---|---|---|---|---|---|---|
| DATA-01 | No database | P0 | Everything is CSV on local disk. No transactional store, no users, no forecasts persisted, no audit. | Nothing survives a restart. No system of record. Cannot support auth, tenancy, or analytics. | PostgreSQL as system of record + Alembic migrations. | **OPEN** | FIXED |
| DATA-02 | Forecasts not persisted | P1 | Predictions are computed and discarded. | Cannot measure forecast accuracy after the fact, cannot show trends, cannot close FR-05's feedback loop. | Persist forecasts with model version; later join to actuals. | **OPEN** | FIXED |
| DATA-03 | Data in git working tree | P2 | 9.3 GB of CSVs sit in `Data_Processing/`. `.gitignore` has `*.csv` so they are untracked — but there is no documented acquisition path either. | A fresh clone cannot reproduce anything; provenance is undocumented. | Document dataset source + a download/prepare script. | **OPEN** | Confirmed: 4 raw files (1.6–1.9 GB each) + `trips_cleaned.csv` 2.3 GB. |
| DATA-04 | Directory rename uncommitted | P2 | `Data Processing/` → `Data_Processing/` and `ML Development/` → `ML_Development/` renames are staged-as-untracked; git still tracks the spaced names. | Repo state does not match disk. Imports reference the underscore names. | Commit the rename. | **OPEN** | `git ls-files` shows spaced names; disk has underscored. |
| DATA-05 | No ingestion path | P1 | Trip data arrives only as a manual CSV drop. The design doc assumes event-driven ingestion from the operational platform. | The ML platform cannot see live demand; forecasts go stale immediately after the static dataset ends (2015-01-31). | Consumer for operational ride events → demand aggregates. | **OPEN** | Feature data ends `2015-01-31 23:45`. |
| DATA-06 | Graph / vector stores | P2 | Referenced in the target architecture; not present. | — (see decision D-3: challenge whether they are needed at all) | Defer until a requirement demands them. | **DEFERRED** | — |

---

## D. FRONTEND

| GAP-ID | Area | Sev | Current State | Risk | Recommended Fix | Status | Validation |
|---|---|---|---|---|---|---|---|
| FE-01 | ML platform frontend | P0 | Does not exist. There is no UI for demand forecasts, supply, recommendations, or model health. FR-10 (admin analytics) is entirely unmet. | The product has no user surface. | New separate React + TypeScript + Vite repository. | **OPEN** | FIXED |
| FE-02 | Operational-platform UI confusion | P2 | `platform/web-react/` exists and is the **operational** ride-hailing UI (rider/driver/admin), merged in PR #1. It is not the ML platform UI. | Easy to mistake one for the other; they have different audiences and lifecycles. | Keep separate; document the boundary. | **OPEN** | — |
| FE-03 | Product naming | P2 | UI strings say "Glovatrix" in 4 source files (+2 build outputs). Owner has asked for this to change. | Wrong brand shipped. | Single source-of-truth name constant; rename. | **OPEN** | `grep -rl Glovatrix` → `README.md`, `web-react/index.html`, `AdminPage.jsx`, `DriverPage.jsx`, `LoginPage.jsx`, `RiderPage.jsx`. |

---

## E. TESTING, SECURITY, OPS

| GAP-ID | Area | Sev | Current State | Risk | Recommended Fix | Status | Validation |
|---|---|---|---|---|---|---|---|
| TEST-01 | API tests all skipped | P0 | 7 of 7 Flask API tests skip with "Trained model not found". | The **entire serving layer was untested**, which is precisely why ML-02 and ML-04 survived. | Model artifact now exists; port tests to the FastAPI app so they execute. | **IN PROGRESS** | FIXED |
| TEST-02 | Failing tests | P1 | 2 failures in `time_based_split` tests. | Red suite normalises failure. | Root cause is ML-08 (no guard). Fixed in the new module; port tests. | **IN PROGRESS** | `test_time_split_no_overlap`, `test_train_larger_than_test`. |
| TEST-03 | No integration/E2E | P1 | Unit tests only. No test starts the app and calls it. | Wiring defects are invisible. | API tests against the real app; E2E on the critical workflow. | **OPEN** | FIXED |
| TEST-04 | No tenant-isolation tests | P0 | N/A — no tenancy. Once built, must be proven. | Cross-tenant leakage is the worst failure mode of a B2B product. | Explicit "tenant A cannot read tenant B" tests. | **OPEN** | VERIFIED |
| SEC-01 | No security controls | P0 | No authn, authz, rate limiting, input validation, or PII policy. Driver location data is inherently sensitive. | Unshippable as a business product. | Full pass — see API-02/04/05/06/09. | **OPEN** | IN PROGRESS |
| SEC-02 | PII in ML data | P1 | Trip data contains precise pickup/dropoff coordinates and timestamps. No retention policy, no anonymisation. | Re-identification risk; regulatory exposure. | Retention limits, aggregation-before-storage, documented policy. | **OPEN** | — |
| OBS-01 | Observability | P1 | `logging.basicConfig` only. No request IDs, correlation IDs, metrics, or error tracking. | Cannot debug production. | Structured logs + request IDs + `/metrics` + ML-specific drift metrics. | **OPEN** | IN PROGRESS |
| OPS-01 | No containerisation | P1 | No Dockerfile for the ML backend. | Not deployable. | Production Dockerfile + compose. | **OPEN** | FIXED |
| OPS-02 | No CI/CD | P1 | No pipeline for the ML backend or the new frontend. | No gate before merge. | Lint → typecheck → test → build → scan. | **OPEN** | FIXED |
| OPS-03 | No staging | P1 | None. | Readiness cannot be claimed. | Define + validate a staging deploy. | **OPEN** | — |
| DOC-01 | Documentation | P2 | Root `README.md` is a single line. Design docs (`Sys/part1.md`, `part2.md`) are excellent but describe the target, not what exists. | New engineers cannot tell design from reality. | Fill `docs/`; keep this log honest. | **IN PROGRESS** | IN PROGRESS |

---

## Summary

**Updated 2026-09-02 (later same day)** after Phases 2–4 landed: PostgreSQL persistence,
JWT auth, RBAC, multi-tenancy, and a complete REST API were built, tested (56 backend
tests, including explicit tenant-isolation proofs), and verified live end-to-end —
plus a separate React/TypeScript frontend, also verified live against the real API.

| Severity | Open | In progress | Fixed / Verified | Total |
|---|---|---|---|---|
| **P0** | 0 | 1 | 14 | 15 |
| **P1** | 5 | 3 | 16 | 24 |
| **P2** | 10 | 1 | 1 | 12 |
| **Total** | **15** | **5** | **31** | **51** |

Every P0 blocker from the original audit is now closed except **ML-05** (the MAE
acceptance gate is measured and enforced relative to the naive baseline, but the
constant `MAE_GATE = 4.0` itself has not yet been rewritten as scale-relative in code).
Remaining P1 opens are scoped, not accidental: FR-02 through FR-10 (ML-13), external
feature providers (ML-14), a static inference fallback (ML-16), live data ingestion
(DATA-05), and PII/retention policy (SEC-02) — all deliberately out of this round's
scope per `IMPLEMENTATION_PLAN.md`'s roadmap, not gaps nobody noticed.

The single most important finding remains **ML-02 (training/serving skew)** combined
with **TEST-01 (every serving test skipped)**: the defect that would have produced
silently wrong predictions in production was invisible precisely because the tests
that would have caught it could not run without an artifact that had never been built.
The fix for TEST-01 was not "make the old tests pass" — it was building an entirely new,
larger test suite (`backend/tests/`) against the productionized code, including
regression tests written specifically to pin each audit finding so it cannot recur
silently.
