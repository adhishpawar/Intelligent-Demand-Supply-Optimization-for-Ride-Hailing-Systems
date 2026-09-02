"""Two-tier ETA, per PLAN §6.1 amendment AM-01 (the Contrarian Architect's highest-severity
finding: the matching hot path must never perform network I/O to rank candidates).

Tier 1 - FastEtaEstimator: pure CPU, haversine * winding factor * time-of-day speed
profile. This is what `ScoringStrategy.rank()` calls, for every candidate, on every
dispatch. Budget: microseconds. It is *real* math, not a stub - it is the same
haversine-based approach the routing service also uses, just without a network hop.

Tier 2 - RoutingProvider (in `libs.geo.routing`): a port for a real routing engine,
used only off the matching hot path (rider-facing ETA display, polyline, final-fare
distance), with a hard timeout and a Tier-1 fallback. See routing.py.
"""
from __future__ import annotations

from datetime import datetime

from libs.geo.cities import SpeedProfile
from libs.geo.haversine import haversine_m


class FastEtaEstimator:
    """Zero-I/O ETA used inside the matching scorer. Safe to call thousands of times
    per dispatch cycle with no latency budget concern."""

    def __init__(self, speed_profile: SpeedProfile) -> None:
        self._profile = speed_profile

    def eta_seconds(
        self, lat1: float, lng1: float, lat2: float, lng2: float, at: datetime
    ) -> float:
        distance_m = haversine_m(lat1, lng1, lat2, lng2) * self._profile.road_winding_factor
        speed_kmh = self._profile.base_kmh
        local_hour = at.hour
        for start, end in self._profile.peak_hours:
            if start <= local_hour < end:
                speed_kmh = speed_kmh / self._profile.peak_hour_multiplier
                break
        speed_mps = max(speed_kmh, 4.0) * 1000.0 / 3600.0  # floor: never "0 km/h" gridlock
        return distance_m / speed_mps

    def distance_m(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        return haversine_m(lat1, lng1, lat2, lng2) * self._profile.road_winding_factor
