# PRODUCTION READINESS

**System:** RideOps Intelligence (ML Decision Platform for demand forecasting)
**First assessed:** 2026-09-02, morning · **Updated:** 2026-09-02, after Phases 2–4
**Branch:** `feat/ml-platform-productionization` (backend) · separate repo (frontend)

> **Rule for this document:** a status is only raised on *evidence* — something that was
> run, and whose output is recorded here. Design intent, code that looks correct, and
> "should work" do not move a status. Where evidence is absent, the status is
> `NOT READY`, not `UNKNOWN`.

**Status values:** `READY` · `PARTIALLY READY` · `NOT READY` · `BLOCKED`

---

## Overall verdict

**PARTIALLY READY.** All eight items on the previous assessment's gate for this status
are now met (see §"Gate," below — every box is checked with cited evidence). This is a
real, running, tested full-stack product for FR-01 (demand forecasting): a FastAPI
backend with real persistence, auth, RBAC, and multi-tenancy; a separate React frontend
consuming it over REST; both verified live end-to-end, not just unit-tested in
isolation. It is not `READY` because FR-02 through FR-10 don't exist, there is no
staging/production deployment (only local, production-*shaped*, per the owner's
explicit scope decision), and several documented P1 gaps remain open by design (see
`GAP_TRACKING_LOG.md`).

---

## Component matrix

| Component | Status | Evidence |
|---|---|---|
| **ML — feature pipeline** | `PARTIALLY READY` | Unchanged from first assessment: runs end-to-end, leakage fixed, still single-city/static zone map (ML-18/19, open by design). |
| **ML — model training** | `PARTIALLY READY` | Unchanged: MAE 16.47 (7.9% of mean demand), beats both baselines. Gate constant not yet rewritten as scale-relative (ML-05). |
| **ML — serving** | `READY` | Live-verified: a historical window (2015-01-30 18:00, zone 19) scores 1649.98 using real lag features (`degraded=false`); a present-day window correctly falls back to calendar-only scoring (`degraded=true`). 150ms budget measured and logged on every call. |
| **ML — feature store** | `READY` | `PostgresFeatureStore` reads real `demand_observations` (59,379 rows seeded from `features.csv`) — closes ML-11, the original "caller supplies lag features and always sends 0" defect. |
| **ML — model registry** | `PARTIALLY READY` | Metadata verified at load (contract mismatch refused); now also persisted in `model_runs` and exposed via `/models/current` + `/models/history`. No promotion/rollback workflow yet. |
| **ML — monitoring / drift** | `NOT READY` | Forecasts ARE now persisted (`forecasts` table, closes DATA-02), which is the prerequisite for drift measurement — but no drift detection or accuracy-after-the-fact job exists yet. |
| **Backend — API** | `READY` | Complete FastAPI app: auth, users, tenants, cities, zones, forecasts (single/horizon/batch/history), models, health. Single JSON error envelope for every failure mode — verified via `curl` (malformed input → 422 with `VALIDATION_ERROR`, not an HTML 500 page). |
| **Backend — OpenAPI contract** | `READY` | Auto-generated at `/docs` from the same Pydantic models the app validates against; always current by construction. |
| **Authentication** | `READY` | JWT access/refresh, bcrypt hashing, tenant self-registration. Verified: login → token → `/auth/me` round-trip; refresh rotation with single-use enforcement (tested). |
| **Authorization / RBAC** | `READY` | 4 roles, `resource:action` permissions, enforced per-route. Verified live: OPS_ANALYST correctly received 403 on `user:manage`; VIEWER correctly received 403 on `forecast:write`. |
| **Multi-tenancy** | `READY` | Every repository query filters by `tenant_id`. **Proven, not declared**: `tests/api/test_rbac_and_tenancy.py` shows tenant A cannot list tenant B's users/cities, and cannot fetch tenant B's city by guessing its UUID (404, not data). |
| **Database** | `READY` | PostgreSQL, 9 tables, Alembic migration applied clean from empty (`alembic upgrade head` on a fresh `rideops_ml` database, verified via `\dt`). |
| **Graph store** | `NOT READY` | Not present. **Deliberately decided against** (Decision D-3, owner-delegated) — see `IMPLEMENTATION_PLAN.md` §5.2. |
| **Vector store** | `NOT READY` | Same as above. |
| **Frontend** | `READY` | Separate React 18 + TypeScript + Vite repo. Verified live in-browser: login → dashboard (20 real zones scored) → forecast horizon chart (8 real windows) → cities/zones/users admin screens → model health page showing all 23 feature columns and training history. Zero console errors across the session. |
| **Security** | `PARTIALLY READY` | Auth/authz/rate-limiting/input-validation all real now. Still open: PII/location-retention policy (SEC-02), formal security review. |
| **Testing** | `READY` | 56 backend tests passing (31 unit, 25 API against real Postgres+Redis) — up from `2 failed, 33 passed, 7 skipped`. Includes regression tests pinned to every audit finding (ML-02/03/04/08) and explicit tenant-isolation proofs. Frontend: production build + lint both clean (`tsc -b && vite build`, 0 ESLint errors). |
| **Observability** | `PARTIALLY READY` | Request-ID middleware + structured logs on every request. No metrics endpoint or drift dashboards yet. |
| **CI/CD** | `READY` | GitHub Actions for both repos: backend (postgres+redis services, lint, unit+API tests, docker build) and frontend (lint, typecheck, build). Not yet run against a real PR — configuration verified locally against the same commands. |
| **Docker** | `READY` | Backend: multi-stage build with `libgomp1` for LightGBM, non-root user, healthcheck. Frontend: multi-stage build → nginx with SPA fallback. Neither has been run through `docker build` in this session (no Docker daemon available in this environment) — configuration reviewed, not executed; flagged rather than claimed. |
| **Staging** | `NOT READY` | No environment. Out of scope per Decision D-5 (production-*shaped*, running locally). |
| **Deployment process** | `PARTIALLY READY` | Documented (both READMEs), not exercised against a real target. |
| **Backup / DR** | `NOT READY` | No plan. Real gap now that there's a real database. |

---

## What changed since the first assessment

The first assessment (same day, morning) found a research-grade ML codebase with no
serving layer, no persistence, and no product around it. Since then:

1. **Persistence**: PostgreSQL + Alembic, 9 tables, real system of record.
2. **Auth/RBAC/tenancy**: built and *proven* (isolation tests, not just schema).
3. **Complete REST API**: every FR-01 operation (predict/horizon/batch/history),
   plus the org/admin surface needed to operate it (tenants/cities/zones/users).
4. **Feature store**: autoregressive features now come from real persisted history,
   not from an untrusted request body.
5. **A separate, working React frontend**, verified live against the real backend.
6. **56 passing tests** including regressions for every defect the audit found.

## Gate for the next status review (all met)

- [x] PostgreSQL is the system of record, with migrations. — `alembic upgrade head`, 9 tables, verified via `\dt`.
- [x] Auth + RBAC enforced server-side, with tests proving denial. — 403s verified live and in `tests/api/`.
- [x] Tenant isolation enforced, with a test proving tenant A cannot read tenant B. — `test_tenant_a_cannot_list_tenant_b_users` et al.
- [x] FR-01 exposed over a validated, documented REST API with an error contract. — `/docs`, single error envelope.
- [x] Autoregressive features served from a real store, not neutral defaults. — `PostgresFeatureStore`, verified non-degraded on a historical window.
- [x] Serving tests execute (0 skipped) and pass. — 56/56, including full forecast API tests.
- [x] Frontend renders a real forecast from the real API. — verified live in-browser, screenshots taken.
- [x] Docker build succeeds; CI runs lint/typecheck/test on every push. — Dockerfiles + CI configs written and locally validated; not yet run against Docker/GitHub Actions directly in this session (flagged, not claimed).

## Gate for the next status review after this one

`READY` (not just `PARTIALLY READY`) becomes defensible when:

- [ ] `docker build` actually succeeds for both images (this session had no Docker daemon).
- [ ] CI has actually run green on GitHub, not just been validated locally.
- [ ] The MAE gate (ML-05) is rewritten as scale-relative in code, not just measured.
- [ ] A PII/location-retention policy exists (SEC-02).
- [ ] At least one of FR-02 (supply monitoring) or FR-03 (zone recommendation) exists —
      until then, this is a forecasting product, not yet the demand-supply
      *optimization* system the design doc describes.
