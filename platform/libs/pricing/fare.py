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


def estimate_fare(rate_card: RateCard, distance_m: float, duration_s: float, surge_multiplier: float) -> float:
    distance_km = distance_m / 1000.0
    duration_min = duration_s / 60.0
    base_fare = rate_card.base_fare + (distance_km * rate_card.per_km_rate) + (duration_min * rate_card.per_min_rate)
    final_fare = base_fare * surge_multiplier + rate_card.booking_fee
    return round(final_fare, 2)
