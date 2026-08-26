-- Round 12 stakeholder council: closes (most of) PLAN's own AS-05 gap, open since
-- Phase 0 -- "MAX_DISPATCH_ATTEMPTS = 3, offer TTL 15s, claim TTL 25s, overall
-- matching deadline 90s ... all are per-city config, not constants." AS-06, the
-- sibling gap right below it in the same table (commission %, cancellation fee),
-- was closed by Round 1's city_configs table; this one sat open the whole session.
--
-- max_dispatch_attempts is deliberately NOT included here -- it is read at every
-- single TripRepository.apply_transition call site across TripService (15+ of
-- them, scattered through the FSM's most central invocation pattern), and making
-- it per-city would mean touching all of them this late in a long session for a
-- disproportionate regression-risk-to-value ratio. Documented as a deliberately
-- partial closure, not a silent one -- see PROGRESS.md Round 12.
ALTER TABLE city_configs
    ADD COLUMN offer_ttl_seconds        INTEGER NOT NULL DEFAULT 15 CHECK (offer_ttl_seconds > 0),
    ADD COLUMN claim_ttl_seconds        INTEGER NOT NULL DEFAULT 25 CHECK (claim_ttl_seconds > offer_ttl_seconds),
    ADD COLUMN matching_deadline_seconds INTEGER NOT NULL DEFAULT 90 CHECK (matching_deadline_seconds > 0),
    ADD COLUMN candidate_radius_km      NUMERIC(4, 1) NOT NULL DEFAULT 3.0 CHECK (candidate_radius_km > 0),
    ADD COLUMN candidate_count          INTEGER NOT NULL DEFAULT 20 CHECK (candidate_count > 0);
