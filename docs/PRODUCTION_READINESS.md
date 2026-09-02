# PRODUCTION READINESS

**System:** Intelligent Demand–Supply Optimization (ML Decision Platform)
**Assessed:** 2026-09-02 · **Branch:** `feat/ml-platform-productionization`

> **Rule for this document:** a status is only raised on *evidence* — something that was
> run, and whose output is recorded here. Design intent, code that looks correct, and
> "should work" do not move a status. Where evidence is absent, the status is
> `NOT READY`, not `UNKNOWN`.

**Status values:** `READY` · `PARTIALLY READY` · `NOT READY` · `BLOCKED`

---

## Overall verdict

**NOT READY for production.** This is expected and not alarming — the system has, until
now, been a research/ML codebase plus a design document, not a deployed product. It has
no database, no authentication, and no user interface. The forecasting core is real and
now demonstrably works; almost everything required to operate it as a business product
does not yet exist.

---

## Component matrix

| Component | Status | Evidence / Reason |
|---|---|---|
| **ML — feature pipeline** | `PARTIALLY READY` | Runs end-to-end: 12,392,801 trips → 59,539 (zone, window) rows → 59,379 after lag warm-up. Leakage defect (ML-03) fixed. Still single-city, static zone map (ML-18/19). |
| **ML — model training** | `PARTIALLY READY` | Trained `v20260902124734`. **MAE 16.47**, RMSE 26.32, MAPE 51.9%. Beats naive `lag1` (18.91) and zone×hour mean (69.28). MAE is **7.9% of mean demand (207.8)**. Acceptance gate needs recalibration (ML-05). |
| **ML — serving** | `NOT READY` | New service layer written but not yet exposed over HTTP or load-tested. Autoregressive features still come from a null store, so live predictions would be calendar-only (ML-11). No static fallback (ML-16). |
| **ML — model registry** | `PARTIALLY READY` | Sidecar metadata written at train time and verified at load; contract mismatch is refused. No promotion workflow, no rollback automation (ML-09). |
| **ML — monitoring / drift** | `NOT READY` | No prediction logging, no drift detection, no accuracy-after-the-fact measurement. Forecasts are not persisted (DATA-02). |
| **Backend — API** | `NOT READY` | FastAPI skeleton exists; no endpoints implemented yet. Legacy Flask app cannot start (its model path was empty) and has no validation or error contract. |
| **Backend — OpenAPI contract** | `NOT READY` | Nothing published. Frontend cannot generate a typed client. |
| **Authentication** | `NOT READY` | None. Every endpoint public. |
| **Authorization / RBAC** | `NOT READY` | None. No roles or permissions exist. |
| **Multi-tenancy** | `NOT READY` | None. No tenant scoping on any record. |
| **Database** | `NOT READY` | No database at all. State is CSV files on local disk. |
| **Graph store** | `NOT READY` | Not present. **Deliberately deferred** — see Decision D-3. |
| **Vector store** | `NOT READY` | Not present. **Deliberately deferred** — see Decision D-3. |
| **Frontend** | `NOT READY` | The ML platform has no UI. (`platform/web-react/` is the *operational* ride-hailing UI, a different product surface.) |
| **Security** | `NOT READY` | No authn/authz/rate-limiting/input-validation. Precise location data handled with no retention or anonymisation policy. |
| **Testing** | `NOT READY` | `2 failed, 33 passed, 7 skipped`. All 7 serving tests skip. No integration, E2E, or tenant-isolation tests. |
| **Observability** | `NOT READY` | `logging.basicConfig` only. No request IDs, metrics, or error tracking. |
| **CI/CD** | `NOT READY` | No pipeline for this backend or the planned frontend. |
| **Docker** | `NOT READY` | No Dockerfile for the ML backend. |
| **Staging** | `NOT READY` | No environment defined. |
| **Deployment process** | `NOT READY` | Undocumented. |
| **Backup / DR** | `NOT READY` | Nothing to back up yet (no database); no plan. |

---

## What is genuinely working today

Recorded because an honest readiness document should not read as if nothing exists.

1. **Data cleaning is real and defensible.** Domain-justified rules (NYC bounding box,
   fare/distance/duration bounds), 12.4M rows survive from the raw feed.
2. **Zone construction works.** KMeans k=20; centroids land on plausible NYC geography
   (Midtown, JFK, LaGuardia, Downtown Brooklyn).
3. **Feature engineering is sound.** Lags are correctly zone-grouped; rolling statistics
   are computed on shifted data; cyclic encodings are correct.
4. **The model beats its baselines.** Not by a landslide (13% better than "same as 15
   minutes ago"), but genuinely, on a chronological holdout.
5. **The 504-line test suite is well-written** — it tests invariants rather than
   snapshots. Its weakness was that a third of it could not execute.

---

## Gate for the next status review

`PARTIALLY READY` overall becomes defensible when **all** of the following hold:

- [ ] PostgreSQL is the system of record, with migrations.
- [ ] Auth + RBAC enforced server-side, with tests proving denial.
- [ ] Tenant isolation enforced, with a test proving tenant A cannot read tenant B.
- [ ] FR-01 exposed over a validated, documented REST API with an error contract.
- [ ] Autoregressive features served from a real store, not neutral defaults.
- [ ] Serving tests execute (0 skipped) and pass.
- [ ] Frontend renders a real forecast from the real API.
- [ ] Docker build succeeds; CI runs lint/typecheck/test on every push.
