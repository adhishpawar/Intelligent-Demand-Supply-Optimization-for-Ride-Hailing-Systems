-- Round 8 stakeholder council: closes the "REAL-LITE, hardcoded" note Round 7 left
-- on VEHICLE_TYPE_MULTIPLIERS -- same DB-backed/admin-editable/audited pattern
-- Round 1 already established for city_configs, applied here to the vehicle-type
-- fare multipliers instead of business rules being a silent Python constant an
-- admin can't see or change without a deploy.
CREATE TABLE vehicle_type_multipliers (
    vehicle_type  VARCHAR(20) PRIMARY KEY CHECK (vehicle_type IN ('SEDAN', 'HATCHBACK', 'SUV', 'AUTO', 'BIKE')),
    multiplier    NUMERIC(4, 2) NOT NULL CHECK (multiplier > 0),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    UUID REFERENCES users (user_id)
);

CREATE TABLE vehicle_type_multiplier_events (
    event_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_type  VARCHAR(20) NOT NULL,
    old_value     NUMERIC(4, 2),
    new_value     NUMERIC(4, 2) NOT NULL,
    changed_by    UUID REFERENCES users (user_id),
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed with the exact values Round 7 hardcoded in libs/pricing/fare.py, so this
-- migration changes where the numbers live, not what they are.
INSERT INTO vehicle_type_multipliers (vehicle_type, multiplier) VALUES
    ('BIKE', 0.45),
    ('AUTO', 0.65),
    ('HATCHBACK', 0.90),
    ('SEDAN', 1.00),
    ('SUV', 1.35);
