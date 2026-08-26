"""Fare + surge formulas, mirroring `ride_hailing_HLD_LLD.md` section 3.5 exactly.
Pure functions — shared by the Trip service (which needs a fare at ride-request time,
reading the live surge value directly from the same Redis key the Pricing service's
recompute loop writes, per that section's own Redis-structures design) and the
dedicated Pricing service's `/v1/estimate` endpoint (services/pricing), so there is
exactly one definition of "what is the fare," not two that can drift (PLAN §3.2 DRY).
"""
from __future__ import annotations

from libs.geo.cities import RateCard

SURGE_RATIO_FREE_THRESHOLD = 1.0
SURGE_SLOPE = 0.5

# Round 7 stakeholder council: the vehicle_type taxonomy (V001) and the seeded
# drivers' deliberately diverse type spread (tools/seed.py) existed all night with
# zero fare differentiation anywhere -- every ride cost the same regardless of
# requested vehicle type. Applied to the base fare, before surge, since vehicle
# class is a baseline-rate difference, not a demand phenomenon; surge still
# multiplies on top of whatever the base already reflects.
#
# Round 8: these values now live in `vehicle_type_multipliers`
# (libs/pricing/vehicle_type_repo.py), DB-backed and admin-editable, the same
# pattern Round 1 already established for city rate cards. This dict is the
# defensive fallback the repo falls back to for a type with no DB row yet -- kept
# here (not deleted) so `estimate_fare` stays a pure function with no DB coupling of
# its own; every real caller resolves the multiplier via the repo first.
VEHICLE_TYPE_MULTIPLIERS: dict[str, float] = {
    "BIKE": 0.45,
    "AUTO": 0.65,
    "HATCHBACK": 0.90,
    "SEDAN": 1.00,
    "SUV": 1.35,
}


def compute_surge_multiplier(active_requests: int, available_drivers: int, cap: float) -> float:
    """HLD 3.5:
        ratio = active_ride_requests / available_drivers
        surge = 1.0                          if ratio <= 1
              = min(1 + (ratio-1)*0.5, cap)   if ratio > 1
    Capped and geo-localized to a small cell by construction — the caller passes
    counts already scoped to one geohash cell, never a whole city, so a stadium
    letting out doesn't spike prices city-wide (HLD's explicit rationale).
    """
    if available_drivers <= 0:
        return cap if active_requests > 0 else 1.0
    ratio = active_requests / available_drivers
    if ratio <= SURGE_RATIO_FREE_THRESHOLD:
        return 1.0
    return min(1.0 + (ratio - 1.0) * SURGE_SLOPE, cap)


def estimate_fare(
    rate_card: RateCard, distance_m: float, duration_s: float, surge_multiplier: float,
    vehicle_type_multiplier: float = 1.0,
) -> float:
    """`vehicle_type_multiplier` is a resolved numeric value, not a type string --
    callers fetch it via `libs.pricing.vehicle_type_repo.get_vehicle_type_multiplier`
    first, the same way they already fetch `rate_card` via `get_rate_card` before
    calling this. Keeps this function pure/DB-free, matching its own docstring."""
    distance_km = distance_m / 1000.0
    duration_min = duration_s / 60.0
    base_fare = rate_card.base_fare + (distance_km * rate_card.per_km_rate) + (duration_min * rate_card.per_min_rate)
    base_fare *= vehicle_type_multiplier
    final_fare = base_fare * surge_multiplier + rate_card.booking_fee
    return round(final_fare, 2)
