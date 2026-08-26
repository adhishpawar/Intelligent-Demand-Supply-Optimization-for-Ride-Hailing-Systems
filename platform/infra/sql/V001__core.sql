-- V001__core.sql — core schema for the ride-hailing operational platform.
-- Every geo-bearing row stores raw lat/lng + geohash7 alongside any derived key
-- (PLAN amendment AM-10) so re-sharding/re-zoning later is a batch UPDATE, not
-- archaeology. Every money-moving table enforces its invariant in the schema
-- (AM-13), not just in application code.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================== users / auth
CREATE TABLE users (
    user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role          VARCHAR(10) NOT NULL CHECK (role IN ('RIDER', 'DRIVER', 'ADMIN')),
    phone         VARCHAR(15) UNIQUE NOT NULL,
    name          VARCHAR(100) NOT NULL,
    rating_avg    NUMERIC(3, 2) NOT NULL DEFAULT 5.00,
    rating_count  INTEGER NOT NULL DEFAULT 0,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- OTP login (GAP AS-04: dev-mode returns/logs the code instead of sending SMS;
-- SmsSender is a port — a Twilio adapter is a new file, not a rewrite).
CREATE TABLE otp_codes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone        VARCHAR(15) NOT NULL,
    code         VARCHAR(6) NOT NULL,
    purpose      VARCHAR(20) NOT NULL DEFAULT 'LOGIN',
    expires_at   TIMESTAMPTZ NOT NULL,
    consumed_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_otp_codes_phone ON otp_codes (phone, created_at DESC);

CREATE TABLE vehicles (
    vehicle_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_id     UUID NOT NULL REFERENCES users (user_id),
    make          VARCHAR(50) NOT NULL,
    model         VARCHAR(50) NOT NULL,
    plate_number  VARCHAR(20) NOT NULL,
    vehicle_type  VARCHAR(20) NOT NULL DEFAULT 'SEDAN'
                   CHECK (vehicle_type IN ('SEDAN', 'HATCHBACK', 'SUV', 'AUTO', 'BIKE')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE driver_profiles (
    driver_id        UUID PRIMARY KEY REFERENCES users (user_id),
    vehicle_id       UUID REFERENCES vehicles (vehicle_id),
    status           VARCHAR(15) NOT NULL DEFAULT 'OFFLINE'
                      CHECK (status IN ('OFFLINE', 'ONLINE', 'ON_TRIP')),
    city_id          VARCHAR(20),
    kyc_verified     BOOLEAN NOT NULL DEFAULT FALSE,
    acceptance_rate  NUMERIC(4, 3) NOT NULL DEFAULT 1.000,  -- rolling 30-day, feeds the scorer
    rides_completed  INTEGER NOT NULL DEFAULT 0,
    state_seq        BIGINT NOT NULL DEFAULT 0,  -- PLAN B4: monotonic guard vs. stale/out-of-order writes
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================== trips (FSM)
CREATE TABLE trips (
    trip_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rider_id                UUID NOT NULL REFERENCES users (user_id),
    driver_id               UUID REFERENCES users (user_id),
    city_id                 VARCHAR(20) NOT NULL,  -- derived once from pickup at request time; immutable

    pickup_lat              DOUBLE PRECISION NOT NULL,
    pickup_lng              DOUBLE PRECISION NOT NULL,
    pickup_geohash7         VARCHAR(12) NOT NULL,
    drop_lat                DOUBLE PRECISION NOT NULL,
    drop_lng                DOUBLE PRECISION NOT NULL,
    drop_geohash7           VARCHAR(12) NOT NULL,

    status                  VARCHAR(24) NOT NULL DEFAULT 'REQUESTED' CHECK (status IN (
                                'REQUESTED', 'MATCHING', 'DRIVER_ASSIGNED', 'DRIVER_ARRIVING',
                                'DRIVER_ARRIVED', 'IN_PROGRESS', 'COMPLETED', 'PAID_PENDING',
                                'PAID', 'RATED', 'NO_DRIVER_FOUND', 'CANCELLED_BY_RIDER',
                                'CANCELLED_BY_DRIVER', 'CANCELLED_BY_SYSTEM', 'EXPIRED'
                             )),
    version                 INTEGER NOT NULL DEFAULT 0,  -- optimistic concurrency (PLAN A2)

    fare_estimate           NUMERIC(10, 2),
    fare_final              NUMERIC(10, 2),
    surge_multiplier        NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    currency                VARCHAR(3) NOT NULL DEFAULT 'INR',

    dispatch_attempts       INTEGER NOT NULL DEFAULT 0,       -- PLAN B3: bounds the re-match cycle
    current_offer_driver_id UUID,
    current_offer_id        UUID,
    current_offer_expires_at TIMESTAMPTZ,
    matching_deadline_at    TIMESTAMPTZ,                      -- PLAN AM-18: hard overall deadline

    scheduled_for           TIMESTAMPTZ,                      -- PLAN C2: column exists, SCHEDULED state unreachable tonight

    cancelled_by             VARCHAR(10) CHECK (cancelled_by IN ('RIDER', 'DRIVER', 'SYSTEM')),
    cancellation_reason      VARCHAR(200),
    cancellation_fee_applied NUMERIC(10, 2) NOT NULL DEFAULT 0,

    payment_method_id       VARCHAR(50) NOT NULL DEFAULT 'default_card',
    request_idempotency_key VARCHAR(100),

    requested_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_at              TIMESTAMPTZ,
    arrived_at               TIMESTAMPTZ,
    started_at               TIMESTAMPTZ,
    completed_at              TIMESTAMPTZ,
    cancelled_at              TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_trips_rider ON trips (rider_id, requested_at DESC);
CREATE INDEX ix_trips_driver ON trips (driver_id, requested_at DESC);
CREATE INDEX ix_trips_city_status ON trips (city_id, status);

-- PLAN amendment AM-02: the database-level backstop of the three-layer driver claim.
-- Even if the Redis Lua claim AND the optimistic version check both somehow fail,
-- the database physically refuses to let one driver hold two active trips.
CREATE UNIQUE INDEX ux_trips_one_active_per_driver ON trips (driver_id)
    WHERE status IN ('DRIVER_ASSIGNED', 'DRIVER_ARRIVING', 'DRIVER_ARRIVED', 'IN_PROGRESS');

-- PLAN C1 (Product Expansionist): buy the multi-stop option now, cheaply, rather than
-- unwind a two-flat-column trip model later. Pricing/routing iterate `trip_stops`;
-- the flat pickup/drop columns above remain a denormalised convenience for the common
-- two-stop case.
CREATE TABLE trip_stops (
    trip_id      UUID NOT NULL REFERENCES trips (trip_id),
    seq          INTEGER NOT NULL,
    kind         VARCHAR(10) NOT NULL CHECK (kind IN ('PICKUP', 'WAYPOINT', 'DROP')),
    lat          DOUBLE PRECISION NOT NULL,
    lng          DOUBLE PRECISION NOT NULL,
    geohash7     VARCHAR(12) NOT NULL,
    arrived_at   TIMESTAMPTZ,
    PRIMARY KEY (trip_id, seq)
);

-- The full state-transition audit trail (invariant I7: every transition writes an
-- audit row and an outbox event). This is what the admin console's "trip audit
-- trail" reads from, and what proves a ride was never silently lost.
CREATE TABLE trip_events (
    event_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id      UUID NOT NULL REFERENCES trips (trip_id),
    from_status  VARCHAR(24),
    to_status    VARCHAR(24) NOT NULL,
    event_type   VARCHAR(40) NOT NULL,
    actor_role   VARCHAR(10),
    actor_id     UUID,
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_trip_events_trip ON trip_events (trip_id, created_at);

-- ============================================================== transactional outbox
-- PLAN §1.2(c): every state-changing write and its corresponding event are committed
-- in the SAME transaction (see libs.persistence.unit_of_work.UnitOfWork). A relay task
-- polls `published_at IS NULL` and publishes to Kafka/InProcessEventBus, using
-- `SELECT ... FOR UPDATE SKIP LOCKED` so multiple relay instances are safe (AM-08).
CREATE TABLE outbox_events (
    event_id        UUID PRIMARY KEY,
    topic           VARCHAR(80) NOT NULL,
    partition_key   VARCHAR(100) NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at    TIMESTAMPTZ
);
CREATE INDEX ix_outbox_unpublished ON outbox_events (created_at) WHERE published_at IS NULL;

-- ============================================================== idempotency (HTTP layer)
-- PLAN §3.3: a replay of the same (key, endpoint) returns the stored response; the
-- same key with a DIFFERENT request body is a 409 (checked via request_hash).
CREATE TABLE idempotency_keys (
    idempotency_key  VARCHAR(100) NOT NULL,
    endpoint         VARCHAR(120) NOT NULL,
    principal_id     UUID,
    request_hash     VARCHAR(64) NOT NULL,
    status_code      INTEGER NOT NULL,
    response_body    JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (idempotency_key, endpoint)
);

-- ============================================================== payment / ledger
CREATE TABLE payments (
    payment_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id        UUID NOT NULL UNIQUE REFERENCES trips (trip_id),  -- idempotency_key = trip_id
    status         VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'PAID_PENDING_RETRY')),
    amount         NUMERIC(10, 2) NOT NULL,
    currency       VARCHAR(3) NOT NULL DEFAULT 'INR',
    gateway_ref    VARCHAR(100),
    failure_reason VARCHAR(200),
    attempt_count  INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Double-entry ledger. Invariant I6 ("money moves exactly once per trip, entries sum
-- to zero") is enforced structurally here (unique per trip+entry_type prevents a
-- silent double-credit — it becomes a DB error, not a duplicate row) and asserted by
-- a balance-check integration test over every trip.
CREATE TABLE ledger_entries (
    entry_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id      UUID NOT NULL REFERENCES trips (trip_id),
    entry_type   VARCHAR(40) NOT NULL CHECK (entry_type IN (
                    'RIDER_CHARGE', 'DRIVER_PAYOUT_CREDIT', 'PLATFORM_COMMISSION_CREDIT',
                    'CANCELLATION_FEE_CHARGE', 'CANCELLATION_FEE_PAYOUT_CREDIT',
                    'REFUND_CREDIT', 'REFUND_DEBIT'
                 )),
    account_type VARCHAR(10) NOT NULL CHECK (account_type IN ('RIDER', 'DRIVER', 'PLATFORM')),
    account_id   UUID,
    amount       NUMERIC(10, 2) NOT NULL,  -- signed: debits negative, credits positive; must sum to 0 per trip
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trip_id, entry_type)
);

-- ============================================================== ratings / notifications
CREATE TABLE ratings (
    rating_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id     UUID NOT NULL REFERENCES trips (trip_id),
    rater_role  VARCHAR(10) NOT NULL CHECK (rater_role IN ('RIDER', 'DRIVER')),
    rater_id    UUID NOT NULL REFERENCES users (user_id),
    ratee_id    UUID NOT NULL REFERENCES users (user_id),
    stars       SMALLINT NOT NULL CHECK (stars BETWEEN 1 AND 5),
    comment     VARCHAR(500),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trip_id, rater_role)  -- one rating per side per trip
);

CREATE TABLE notifications (
    notification_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users (user_id),
    type             VARCHAR(40) NOT NULL,
    title            VARCHAR(200) NOT NULL,
    body             VARCHAR(500) NOT NULL,
    payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    read_at          TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_notifications_user ON notifications (user_id, created_at DESC);

-- ============================================================== location history
-- PLAN §4.4: Postgres implementation by default (CassandraLocationHistoryRepository is
-- written separately against the CQL schema from the HLD and enabled by
-- LOCATION_HISTORY_BACKEND=cassandra). Same access pattern: fast "last N pings for
-- this driver" reads, append-heavy, no read-before-write contention.
CREATE TABLE location_history (
    driver_id  UUID NOT NULL,
    ts         TIMESTAMPTZ NOT NULL,
    lat        DOUBLE PRECISION NOT NULL,
    lng        DOUBLE PRECISION NOT NULL,
    geohash7   VARCHAR(12) NOT NULL,
    trip_id    UUID,
    PRIMARY KEY (driver_id, ts)
);
CREATE INDEX ix_location_history_driver_ts_desc ON location_history (driver_id, ts DESC);

-- ============================================================== ledger balance helper
-- Used by the payment idempotency/ledger integration test (I6) and by the admin
-- console to surface any non-zero trip in red.
CREATE VIEW ledger_trip_balances AS
    SELECT trip_id, SUM(amount) AS balance, COUNT(*) AS entry_count
    FROM ledger_entries
    GROUP BY trip_id;
