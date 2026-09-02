## 5. Component Deep Dive

Each component below is documented with the same rubric: Purpose, Responsibilities, Inputs, Outputs, Storage, Scaling, Failure handling, Technology choice, Alternatives, Trade-offs. Operational-platform components are covered briefly (they are production-proven and out of scope for redesign); ML components get full depth.

### 5.1 Operational Platform Components (brief — 30% weight)

#### Trip Service
- **Purpose**: Owns the ride state machine end-to-end.
- **Responsibilities**: Persist state transitions, emit `ride.*` events.
- **Inputs**: Booking requests, driver accept/reject, completion signals.
- **Outputs**: `ride.requested`, `ride.assigned`, `ride.completed`, `ride.cancelled` events.
- **Storage**: PostgreSQL, sharded by city.
- **Scaling**: Horizontal, stateless service instances behind the shard router.
- **Failure handling**: Synchronous DB write before event publish; Kafka replay-safe.
- **Tech choice**: Already production; not revisited here.
- **ML relevance**: This is the single most important upstream data source for the ML platform — every `ride.*` event is a training/feature signal.

#### Matching Service
- **Purpose**: Nearest-driver search and dispatch.
- **ML relevance**: Emits `ride.assigned` (with time-to-match) which is a core label for demand-supply imbalance; also a potential *consumer* of ML output in a future iteration (ML-weighted ranking), explicitly **out of scope** for this phase — v1 keeps matching untouched to avoid coupling ML correctness to the booking critical path.

#### Location Service
- **Purpose**: Ingest and broadcast GPS pings.
- **ML relevance**: `driver.location` events are the raw signal for supply monitoring (FR-02) and are the single highest-volume topic the ML platform consumes.

#### Pricing Service
- **Purpose**: Fare + reactive surge calculation.
- **ML relevance**: Optional consumer of the demand forecast (Section 4.2, sync/pull mode) to smooth surge pre-emptively; falls back to today's reactive-only logic if the ML platform is unavailable.

*(User, Payment, Notification, Ratings services are unchanged and not further detailed here — see the existing HLD/LLD document.)*

---

### 5.2 ML Platform Components (deep — 70% weight)

#### 5.2.1 Feature Service

- **Purpose**: The single ingestion boundary between operational events/external APIs and the ML data plane. Everything the ML platform knows about the world enters through here.
- **Responsibilities**:
  - Consume operational Kafka topics (`ride.*`, `driver.*`, `payment.*`) and external API feeds (weather, events/calendar).
  - Compute streaming features (rolling counts, window aggregations) and write them to the Online Feature Store.
  - Hand off raw/cleaned events to the Data Lake (Bronze layer) for offline/batch feature computation.
  - Enforce feature schemas and reject/quarantine malformed events.
- **Inputs**: Kafka topics (internal), REST/webhook feeds (external weather/events APIs).
- **Outputs**: Online feature writes (Redis), Bronze-layer writes (S3/data lake), feature validation metrics.
- **Storage**: Does not persist state itself beyond a small dedup/idempotency cache (Redis, short TTL).
- **Scaling**: Horizontally scaled consumer group, partitioned identically to the source Kafka topics (by `driverId` / `geohash`) so ordering per entity is preserved without a global bottleneck.
- **Failure handling**: Kafka consumer offsets committed only after successful write; a Feature Service outage means online features go stale (bounded by TTL) but the operational platform is entirely unaffected, since it never waits on this service.
- **Technology choice**: Kafka Streams / Flink-lite consumers for the streaming path (chosen over raw consumer loops for windowing primitives); Spark for the batch/backfill path.
- **Alternatives considered**: A single monolithic "ETL job" instead of a dedicated always-on service — rejected because streaming freshness (FR-02, <1min supply staleness) cannot be met by batch-only ETL.
- **Trade-off**: Running both a streaming and batch path (Lambda-architecture-style) roughly doubles pipeline code vs. batch-only, but is necessary because *some* features (supply counts) need sub-minute freshness while others (7-day rolling demand averages) do not — see Section 10 for the full freshness tiering.

#### 5.2.2 Offline Feature Store & Online Feature Store

- **Purpose**: Provide point-in-time-correct features for training (offline) and low-latency features for serving (online), from a single logical feature definition.
- **Responsibilities**: Offline store materializes historical feature values for training-set construction; online store serves the *current* value of the same features at inference time with millisecond latency.
- **Inputs**: Feature Service writes (streaming), Spark batch jobs (offline aggregates).
- **Outputs**: Training-time feature joins (offline); `GET /features?entity=zone_123` lookups (online).
- **Storage**: Offline — Parquet on S3, partitioned by date/zone, queried via Spark/Athena. Online — Redis, keyed by entity ID, values as feature vectors.
- **Scaling**: Offline scales with S3/Spark (effectively unbounded, cost-bound). Online scales as a Redis cluster sharded by entity (zone/driver) key.
- **Failure handling**: Online store miss → Inference Service falls back to the last cached model output or a static default (never blocks, never errors upstream to the driver app).
- **Technology choice**: Feast-style feature store pattern (open-source feature store or an equivalent in-house layer) specifically because it guarantees the offline/online consistency contract — the same feature definition produces the same value shape in both stores, preventing training/serving skew.
- **Alternatives**: Compute features ad hoc in each service — rejected, this is exactly how training/serving skew silently creeps in (the classic ML production failure mode).
- **Trade-off**: A dedicated feature store adds operational surface area (one more system to run) versus embedding feature logic in each model's serving code, but the skew risk it eliminates is worth far more at this scale — a demand model with silently-skewed features fails invisibly, not loudly.

#### 5.2.3 Training Pipeline

- **Purpose**: Reproducible, orchestrated model training from versioned data to a registered model artifact.
- **Responsibilities**: Pull point-in-time features from the offline store, train, evaluate against held-out data, register the artifact with metrics attached.
- **Inputs**: Offline feature store snapshot + labels (e.g., actual ride counts per zone/window).
- **Outputs**: Trained model artifact + evaluation report, pushed to Model Registry.
- **Storage**: Training data snapshots versioned (DVC-style or lakehouse table versioning); model artifacts in the registry's backing store (S3).
- **Scaling**: Spark for feature joins at scale; distributed training only where the model family requires it (the primary demand model, LightGBM-class, does not need distributed GPU training; a future deep model might).
- **Failure handling**: A failed training run never touches production — it simply fails to produce a registry candidate; the currently-deployed model keeps serving.
- **Technology choice**: Airflow for orchestration (DAG dependencies between data validation → feature build → train → eval → register are naturally expressed as a DAG), MLflow for experiment tracking and the registry.
- **Alternatives**: Kubeflow Pipelines — viable alternative, more Kubernetes-native; chosen against here mainly because Airflow already has broader team familiarity and the DAGs here are not GPU-heavy enough to need Kubeflow's training-job-native primitives. Documented as a valid alternative, not a wrong choice (see Section 17).
- **Trade-off**: Airflow's scheduler-based model is slightly less "cloud native" than Kubeflow's Kubernetes-CRD-based jobs, but is operationally simpler to run and debug for a team already running Airflow for other data pipelines.

#### 5.2.4 Model Registry

- **Purpose**: Single source of truth for "which model version is approved, and what are its metrics."
- **Responsibilities**: Version artifacts, store evaluation metrics, gate promotion (staging → production) behind explicit approval.
- **Inputs**: Candidate models from the training pipeline.
- **Outputs**: Serves the Inference Service with the currently-approved artifact URI per model name/stage.
- **Storage**: MLflow Model Registry (metadata) + S3 (artifacts).
- **Scaling**: Low write volume (one write per training run), read-heavy at deploy time only — not a scaling concern.
- **Failure handling**: Inference Service caches the last-resolved artifact locally; a Registry outage does not cause serving to stop, only blocks *new* deployments.
- **Technology choice**: MLflow — chosen for being tool-agnostic (works with LightGBM, PyTorch, sklearn alike) and because it's already the tracking tool used elsewhere in this org's ML work (per existing project context).
- **Alternatives**: SageMaker Model Registry — viable if the org standardizes on SageMaker end-to-end; trade-off is vendor lock-in vs. tighter AWS-native integration (Section 17 has the full comparison).

#### 5.2.5 Inference Service

- **Purpose**: Serve real-time predictions (demand forecast, zone scores) with a hard latency budget.
- **Responsibilities**: Load the approved model, perform online feature lookup, run prediction, apply the timeout/fallback contract.
- **Inputs**: Entity ID (zone, driver), triggers online feature lookup internally.
- **Outputs**: Prediction (e.g., predicted demand for zone X in next 15 min), always with a confidence/staleness indicator.
- **Storage**: Stateless; models loaded into memory per pod, model artifact pulled from Registry/S3 at startup and on hot-reload.
- **Scaling**: Horizontal pod autoscaling on Kubernetes, keyed on request rate and p99 latency.
- **Failure handling**: 150ms hard timeout on feature lookup + inference combined; on timeout or feature-store miss, returns the last cached prediction for that entity (Redis-cached, few-minute TTL) or a static seasonal-average fallback if even that is missing. **Never returns an error to the calling driver app** — worst case is "no recommendation shown."
- **Technology choice**: A lightweight model-server (e.g., a FastAPI/Triton-style server) chosen for CPU-first serving of gradient-boosted models, which is the dominant model family here (Section 9) and does not benefit from GPU.
- **Alternatives**: SageMaker real-time endpoints — viable, trades operational simplicity for less control over the custom fallback logic described above, which is easier to implement in a self-managed server.
- **Trade-off**: Self-hosting the serving layer means more ops burden (patching, scaling config) than a managed endpoint, but the custom timeout/fallback contract is a hard product requirement (Section 1.5) and is simpler to guarantee in code we control.

#### 5.2.6 Recommendation Service

- **Purpose**: Turns raw model output (a demand score per zone) into an actionable, ranked recommendation for a specific driver.
- **Responsibilities**: Combine the Inference Service's demand forecast with the driver's current location, distance to candidate zones, and driver-specific context (rating, acceptance rate) into a ranked list; apply the fleet-level de-duplication logic from FR-04 so not every driver in an area gets pointed at the same single hot zone.
- **Inputs**: Demand forecast (from Inference Service), live driver location (from Location Service via Kafka), supply snapshot (from Feature Service).
- **Outputs**: `ml.recommendation.issued` event + synchronous API response to the driver app.
- **Storage**: Short-lived recommendation state in Redis (which drivers were recently pointed where, to implement the fleet-level de-duplication / avoid herding).
- **Scaling**: Horizontal, stateless beyond the Redis-backed de-duplication window.
- **Failure handling**: Falls back to "no recommendation" — the driver app simply shows nothing extra, exactly as it does today without the ML platform.
- **Technology choice**: Custom service (not a generic model server) because the ranking/de-duplication logic is business logic layered on top of a model score, not itself a model in v1 (though FR-06 online learning eventually moves *this* layer to a contextual bandit — see Section 9.6).

#### 5.2.7 Monitoring & Drift Detection

- **Purpose**: Continuously answer "is the ML platform still doing more good than harm."
- **Responsibilities**: Track prediction distributions vs. training distributions (data drift), track feature freshness, track recommendation adoption and downstream outcome metrics, trigger alerts and, where configured, automated rollback.
- **Inputs**: Live prediction logs, live feature snapshots, `ml.feedback` events (FR-05).
- **Outputs**: Prometheus metrics, Grafana dashboards, alerts, automated rollback triggers to the Model Registry.
- **Storage**: Metrics in Prometheus/long-term store (e.g., Thanos/Mimir for retention); structured prediction logs in the data lake for offline drift analysis.
- **Scaling**: Metrics pipeline scales independently of the serving path — sampling can be applied under extreme load without affecting inference latency.
- **Technology choice**: Prometheus + Grafana for metrics, OpenTelemetry for distributed tracing across Feature Service → Inference Service → Recommendation Service, a dedicated drift-detection job (e.g., population stability index computation) run on a schedule via Airflow.

---

## 6. Event-Driven Architecture

### 6.1 Kafka Topic Catalog

| Topic | Producer | Consumers | Key | Partitions (guide) | Retention |
|---|---|---|---|---|---|
| `ride.requested` | Trip Service | Feature Service, Pricing Service | `city_id` | 64 | 7 days |
| `ride.assigned` | Matching Service | Feature Service | `city_id` | 64 | 7 days |
| `ride.completed` | Trip Service | Feature Service, Payment Service | `city_id` | 64 | 30 days |
| `ride.cancelled` | Trip Service | Feature Service | `city_id` | 32 | 30 days |
| `driver.location` | Location Service | Feature Service, Recommendation Service | `driver_id` | 256 | 24 hours |
| `driver.status` | User/Driver Service | Feature Service | `driver_id` | 64 | 7 days |
| `payment.completed` | Payment Service | Notification Service, Feature Service | `trip_id` | 32 | 30 days |
| `ml.recommendation.issued` | Recommendation Service | Driver App (via gateway), Monitoring | `driver_id` | 64 | 7 days |
| `ml.feedback` | Driver App / Trip Service | Feature Service, Training Pipeline (via lake) | `driver_id` | 64 | 90 days |
| `ml.hotspot.detected` | Hotspot Detection job | Notification Service, Ops Dashboard | `zone_id` | 16 | 7 days |
| `ml.model.deployed` | CI/CD | Monitoring, Inference Service (cache-bust) | `model_name` | 4 | 90 days |
| `dlq.*` (one per source topic) | Any consumer on poison-message | Ops/replay tooling | same as source | 8 | 30 days |

### 6.2 Event Schema Example

```json
// Topic: driver.location
{
  "event_id": "uuid",
  "schema_version": 1,
  "driver_id": "uuid",
  "lat": 18.5204,
  "lng": 73.8567,
  "heading": 134.0,
  "speed_kmh": 22.5,
  "status": "ONLINE_AVAILABLE",
  "geohash7": "tehk1zc",
  "h3_res8": "88283082e3fffff",
  "ts": "2026-07-08T10:15:32.412Z"
}

// Topic: ml.recommendation.issued
{
  "event_id": "uuid",
  "schema_version": 1,
  "driver_id": "uuid",
  "recommended_zone_h3": "88283082dbfffff",
  "current_zone_h3": "88283082e3fffff",
  "predicted_demand_score": 0.82,
  "expected_wait_reduction_min": 4.2,
  "model_version": "demand-lgbm-v14",
  "issued_at": "2026-07-08T10:15:40.000Z",
  "expires_at": "2026-07-08T10:30:40.000Z"
}

// Topic: ml.feedback
{
  "event_id": "uuid",
  "schema_version": 1,
  "driver_id": "uuid",
  "recommendation_event_id": "uuid",
  "followed": true,
  "time_to_next_ride_min": 6,
  "ts": "2026-07-08T10:22:10.000Z"
}
```
All schemas carry `schema_version` explicitly and are registered in a schema registry (Avro or JSON Schema); consumers reject/quarantine events with an unrecognized version rather than guessing field shapes.

### 6.3 Delivery Semantics

- **At-least-once, everywhere.** Exactly-once is not used — it adds coordination overhead (transactional producers/consumers) that this system does not need, because every consumer is designed to be **idempotent** instead (see below). This is a deliberate simplicity trade-off: idempotent at-least-once is easier to reason about and debug than exactly-once semantics across a multi-service pipeline.
- **Idempotency**: Every event carries a unique `event_id`. The Feature Service and Training Pipeline both dedup on `event_id` (short-TTL Redis set for streaming, a dedup step in the Spark batch job for the lake) before applying an update. This makes replays and duplicate deliveries safe by construction.
- **Ordering**: Guaranteed only *within a partition*. Keys are chosen (`driver_id`, `city_id`) specifically so that events that must be ordered relative to each other (e.g., a driver's consecutive location pings) land in the same partition. Cross-entity ordering (e.g., between two different drivers) is never assumed anywhere in the system.
- **Partition strategy**: High-cardinality, roughly-uniform keys (`driver_id`) get many partitions (256) to spread load; lower-cardinality/coarser keys (`city_id`) get fewer (32–64) since a single city's throughput, while high, doesn't need per-driver-level partition fan-out.
- **Dead Letter Queue**: Every consumer that fails to process a message after N retries (with exponential backoff) routes it to a `dlq.<topic>` topic rather than blocking the partition. DLQ messages are alerted on and can be manually or automatically replayed once the root cause (usually a schema mismatch or a downstream store being down) is fixed.
- **Replay**: Because Kafka retains 7–30 days of raw events and the Bronze data-lake layer retains everything indefinitely, both the Feature Service and the Training Pipeline can be replayed from any point — this is the core mechanism for backfilling a new feature definition or recovering from a bad deployment that corrupted derived data without touching the source-of-truth events.

---

## 7. Data Architecture

### 7.1 Storage Layer Overview

| Layer | Purpose | Technology | Consistency |
|---|---|---|---|
| OLTP | Operational source of truth (trips, users, payments) | PostgreSQL (existing) | Strong |
| Streaming storage | Durable event log, replay source | Kafka | At-least-once, ordered per partition |
| Data Lake — Bronze | Raw events, as-received, immutable | S3 (Parquet/JSON), partitioned by event date | Eventual |
| Data Lake — Silver | Cleaned, deduplicated, schema-validated | S3 (Parquet), partitioned by date + zone | Eventual |
| Data Lake — Gold | Business-level aggregates, training-ready feature tables | S3 (Parquet) / Lakehouse tables (Delta/Iceberg-style) | Eventual, versioned |
| Offline Feature Store | Point-in-time feature snapshots for training | Built on Gold layer, queried via Spark | Versioned, point-in-time correct |
| Online Feature Store | Low-latency current feature values | Redis (separate cluster from operational Redis) | Eventual, TTL-bound |
| Warehouse / OLAP | Ad hoc analytics, admin dashboard queries | Redshift/BigQuery-style columnar warehouse, fed from Gold | Eventual |
| Metadata Store | Feature definitions, schema versions, lineage | Feature store's own metadata catalog + MLflow | Strong (small, low-write) |
| Historical storage (ride ops) | Long-term ride history for compliance/history views | Cassandra (existing, unchanged) | Eventual |
| Vector Store | Not required in v1 — no embedding-similarity use case exists yet (all v1 models are tabular). Flagged as a future extension if, e.g., a trip-embedding-based rider-clustering feature is added. | — | — |

### 7.2 Why a Medallion (Bronze/Silver/Gold) Lake

- **Bronze** preserves the raw event exactly as received, forever (cheap object storage) — this is the ultimate replay/audit source, decoupled from any bug in downstream processing.
- **Silver** applies schema validation, deduplication (by `event_id`), and light normalization (geohash/H3 enrichment) — this is the layer feature engineering jobs actually read from, so a bad raw event never corrupts a feature without a chance to be caught first.
- **Gold** is where business logic lives: rolling demand aggregates, driver quality scores, zone-level joined tables — this is what the offline feature store and the warehouse both read from, so there is exactly one place that defines "what is a zone's 15-minute demand count," not one definition per consumer.

### 7.3 Point-in-Time Correctness

The single most common way ML systems silently fail in production is **training/serving skew via label leakage from the future**. Concretely: if a training example for "predict demand for zone X at 10:00" is accidentally built using a feature computed from data as of 10:05, the model looks great offline and fails in production because that future information doesn't exist at serving time.

**Mitigation**: The offline feature store enforces point-in-time joins — every training row is joined against feature values *as they existed* at the label's timestamp, using the Gold layer's partitioning by date/hour plus an event-time watermark. This is implemented as a standard "AS OF" join in the Spark feature-retrieval job, not a manual timestamp filter developers might get wrong per-model.

### 7.4 Data Versioning

- **Gold tables**: versioned via a lakehouse table format (Delta Lake or Iceberg semantics) — every write is a new snapshot, old snapshots queryable for reproducibility and rollback.
- **Feature definitions**: versioned in the feature store's metadata catalog; a model always records *which version* of each feature definition it was trained against.
- **Training datasets**: each training run's exact input snapshot is logged (as a Delta/Iceberg version pointer, not a full copy) so any past training run can be exactly reproduced.

---

## 8. Geospatial System Design

### 8.1 Why Geospatial Indexing Is Core to This System

Every FR in this document is fundamentally a spatial aggregation problem: "how much demand in this area," "how much supply in this area," "which nearby zone should this driver go to." The choice of spatial indexing scheme determines the granularity, the join performance, and the interpretability of every downstream model.

### 8.2 GeoHash vs. H3 vs. S2 — and the choice made here

| Scheme | Shape | Pros | Cons |
|---|---|---|---|
| **GeoHash** | Rectangular cells, string-prefix encoding | Simple, already used by the operational Redis GEO index; sortable as a string | Cell size distorts with latitude (non-uniform area); rectangular cells don't tile a "neighbor ring" cleanly — edge/corner adjacency is inconsistent |
| **S2** | Spherical hierarchical cells (quadratic projection) | True spherical geometry, very accurate at global scale | Steeper learning curve, less common tooling in the Python/Spark ML ecosystem this team uses |
| **H3 (chosen)** | Hexagonal hierarchical cells | Uniform neighbor distance in all 6 directions (hexagons have consistent adjacency, unlike squares), good multi-resolution hierarchy (parent/child cells), excellent tooling (Uber's own open-source library, native Spark/Pandas support) | Slightly more conceptual overhead than GeoHash for engineers unfamiliar with it; not natively supported by Redis GEO commands (requires a translation layer) |

**Decision**: The ML platform uses **H3 at resolution 8** (~0.7 km² hexagons) as its primary spatial unit for demand/supply aggregation and model features, while **continuing to use the existing GeoHash-backed Redis GEO index unchanged for the operational matching path** (Section 5.1 — that system is not touched).

**Why hexagons for the ML side specifically**: demand forecasting and zone recommendation are inherently about "spreading" supply across neighboring areas — hexagonal cells have uniform edge-adjacency (every neighbor is equidistant from the center), which makes "recommend the best of my 6 neighboring zones" a clean, symmetric computation. With square/rectangular GeoHash cells, diagonal neighbors are farther away than edge neighbors, which subtly biases any distance-based ranking unless corrected for — a correction that H3 makes unnecessary.

**Why not replace the operational Redis GeoHash index with H3**: that index is proven, sub-100ms, and untouched per the core constraint in Section 1.5. Introducing H3 there would be a change to the booking critical path for a benefit (uniform adjacency) that the *matching* use case doesn't actually need — nearest-driver search doesn't care about hexagonal symmetry, only about accurate radius queries, which GeoHash already does well via Redis's native `GEOSEARCH`.

### 8.3 Zone Partitioning for This System

- **H3 resolution 8** (~0.74 km² per cell) is the default granularity for demand/supply aggregation — fine enough to be locally actionable, coarse enough to keep the cell count per city manageable (a mid-size city is a few thousand res-8 cells, not millions).
- **H3 resolution 6** (~36 km² per cell) is used for the coarser fleet-level redistribution view (FR-04) and the admin dashboard's city-wide heatmap, where too much granularity would just be visual noise.
- The H3 hierarchy (`h3ToParent`) lets the system roll res-8 predictions up to res-6 for the dashboard without a separate model or a separate aggregation job — it's a pure function of the H3 index.

### 8.4 Nearest-Neighbor / Candidate Zone Search

For FR-03/FR-09 (zone and return-trip recommendation), candidate zones for a given driver are generated via H3's `kRing` function (all cells within k hex-steps of the driver's current cell), typically k=3–5 (roughly a 2–4 km radius at resolution 8). This candidate set is then scored by the demand model (Section 9.3) and ranked — the geospatial step here is a cheap, exact, O(1)-per-ring lookup, not a search problem, which is exactly why hierarchical hex indexing is preferable to a radius query against a point index for this specific access pattern.

### 8.5 Trade-offs Summary

| Decision | Upside | Downside | Mitigation |
|---|---|---|---|
| H3 for ML, GeoHash for matching (two systems) | Each system uses the indexing scheme best suited to its access pattern | Two spatial vocabularies in the codebase, need a translation layer at the integration boundary | Feature Service performs GeoHash→H3 (and lat/lng→H3) conversion once, at ingestion, so no other component needs to know both schemes |
| Res-8 as default granularity | Good balance of actionability vs. cell-count/model-complexity | Some very dense urban cores may want finer granularity | Resolution is a per-city-tunable config, not hardcoded — dense cities can run res-9 |
