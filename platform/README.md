# RideOps — Ride-Hailing Operational Platform

A working, demoable, full-stack ride-hailing system built from the design in
[`ride_hailing_HLD_LLD.md`](../Sys) — real geospatial matching, a real state machine,
real RBAC, real idempotent payments, running on real infra (native binaries tonight;
`infra/docker-compose.yml` for a machine where containers actually work).

**If you're picking this up cold, read [`PROGRESS.md`](../../../Users/ADISH/Documents/Obsidian%20Vault/prj-RideHailing/PROGRESS.md) in the Obsidian vault first** (`C:\Users\ADISH\Documents\Obsidian Vault\prj-RideHailing\PROGRESS.md`) — it is the single source of truth for what's built, what's stubbed, what's broken, and what's next, updated at every checkpoint through the build. `PLAN.md` in the same vault has the full design rationale and the adversarial review that shaped it.

## Quick start

```powershell
.\run.ps1
```

One command, idempotent, safe to re-run. From a clean clone with nothing installed,
this downloads and initializes native Postgres/Redis/Kafka/Node.js, runs migrations
and seed data, starts all 9 backend services + the gateway, and starts the React
frontend — then prints the URLs and seeded demo credentials. Re-running it when
everything is already up just confirms and exits (tested — see PROGRESS.md's C10
entry for the actual cold-start timing).

```powershell
.\run.ps1 -Fresh      # wipe and reinitialize all data from scratch
.\run.ps1 -SkipSeed   # bring infra/services up without reseeding
.\stop.ps1            # stop everything run.ps1 started
```

Then open **http://localhost:8080/login** — one login screen for all three roles;
which interface you land on is decided by the account's actual role, not a picker.

**Seeded accounts** (phone + OTP; dev mode returns the code directly in the response,
no real SMS is sent):

| Role | Phones |
|---|---|
| Admin | `+910000000001` |
| Rider | `+919000000001` .. `+919000000005` |
| Driver | `+918000000001` .. `+918000000012` |

## Prove it works without touching the UI

```powershell
.\venv\Scripts\python.exe -m tools.demo
```

Drives a complete trip through the real running services headlessly — login, online,
request, real dispatch match, accept, full lifecycle, automatic payment via the live
Kafka consumer, rating — and prints the real audit trail and a ledger-balance check.
This is the release gate: if this doesn't pass clean, the system isn't demoable.

```powershell
.\venv\Scripts\python.exe -m pytest         # full test suite
```

## Architecture at a glance

Nine services (`services/`), each a standalone FastAPI app, no cross-service
internal imports (enforced by `tests/architecture/test_import_boundaries.py`):

| Service | Port | Depth |
|---|---|---|
| gateway | 8000 | Full — Layer 1 RBAC, deny-by-default routing |
| identity | 8001 | Full — OTP auth, JWT, driver profile/KYC |
| location | 8002 | Full — Redis geo-index, atomic Lua ingest, live tracking |
| matching | 8003 | Full — weighted scoring, three-layer driver claim |
| trip | 8004 | Full — table-driven FSM, transactional outbox, dispatch orchestrator |
| pricing | 8005 | Full — fare + live surge recompute loop |
| payment | 8007 | Full logic, fake gateway — idempotent charge, double-entry ledger |
| notification | 8008 | Real-lite — Kafka consumer, persisted inbox, WS push |
| ratings | 8009 | Real-lite — feeds back into the matching scorer for real |

Frontend: `web-react/` — Vite + React 19, role-based login/routing, Leaflet maps,
self-reconnecting WebSockets. Shared libraries in `libs/` (geo math, event contracts,
persistence/outbox, security/RBAC) — services depend on `libs/`, never on each other.

Full design rationale, the five-advisor adversarial review that shaped every major
decision, and the hour-by-hour build log live in the Obsidian vault (`PLAN.md` /
`PROGRESS.md`), not duplicated here — this file is the "how do I run it" doc; those
are the "why does it look like this" doc.

## Known gaps (see PROGRESS.md for the full, current list)

- Cassandra location-history backend is written (`services/location/cassandra_repository.py`)
  but not the default (`LOCATION_HISTORY_BACKEND=postgres`) — a Windows/JVM
  environment cost, not a missing feature.
- No real SMS/push/card gateway — every external integration is a Port with a
  working Adapter fake that reproduces real failure modes, not just the happy path.
- Single-instance, single-region — this is a demo build, not a production
  deployment; `infra/docker-compose.yml` documents the intended shape.
