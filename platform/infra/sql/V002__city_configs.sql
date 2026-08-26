-- V002__city_configs.sql — Round 1 stakeholder council: ops and tech-lead
-- perspectives independently landed on the same gap — every business rule GAP AS-06
-- flagged as ambiguous (commission %, cancellation fee, surge cap) was a hardcoded
-- Python constant, changeable only by a code deploy. This makes it data, admin-
-- editable, with an audit trail — reusing the exact `trip_events` audit pattern the
-- codebase already has, per the tech lead's own note.

CREATE TABLE city_configs (
    city_id           VARCHAR(20) PRIMARY KEY,
    base_fare         NUMERIC(10, 2) NOT NULL,
    per_km_rate       NUMERIC(10, 2) NOT NULL,
    per_min_rate      NUMERIC(10, 2) NOT NULL,
    booking_fee       NUMERIC(10, 2) NOT NULL,
    surge_cap         NUMERIC(3, 2) NOT NULL DEFAULT 3.00,
    commission_pct    NUMERIC(4, 3) NOT NULL DEFAULT 0.200,
    cancellation_fee  NUMERIC(10, 2) NOT NULL DEFAULT 30.00,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        UUID REFERENCES users (user_id)
);

-- Audit trail for every change — the same "never silently change money-affecting
-- config" principle the ledger and trip_events already embody, applied here.
CREATE TABLE city_config_events (
    event_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    city_id     VARCHAR(20) NOT NULL,
    field       VARCHAR(30) NOT NULL,
    old_value   NUMERIC(10, 3),
    new_value   NUMERIC(10, 3) NOT NULL,
    changed_by  UUID REFERENCES users (user_id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_city_config_events_city ON city_config_events (city_id, created_at DESC);

-- Seed from the values that were hardcoded in libs/geo/cities.py — the DB now takes
-- over as the live source; the Python dataclass becomes the documented fallback
-- default if a city has no row (defensive, not the primary path anymore).
INSERT INTO city_configs (city_id, base_fare, per_km_rate, per_min_rate, booking_fee, surge_cap, commission_pct, cancellation_fee)
VALUES
    ('pune', 50.00, 11.00, 1.50, 5.00, 3.00, 0.200, 30.00),
    ('mumbai', 60.00, 13.00, 1.80, 6.00, 3.00, 0.200, 30.00);
