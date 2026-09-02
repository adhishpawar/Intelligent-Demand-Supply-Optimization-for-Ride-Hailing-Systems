"""The city registry — the seam through which every city-specific business rule
(rate cards, surge cap, dispatch tuning, commission, cancellation fee) becomes *data*,
not code (GAP AS-06/AS-08: cancellation fee, commission, surge cap are business-
ambiguous and are implemented here as config precisely so the answer can change without
a deploy).

Also the fix for PLAN finding B1 (border supply): each city declares its immediate
`neighbors`, and `DriverIndexRepository.search` (libs not shown here; see
services/matching) fans a GEOSEARCH out across a city's own set *and* its neighbours'
sets before scoring, so a driver just across an administrative boundary is not silently
invisible to a rider standing near it.

Membership (`city_for`) uses a simple bounding-box check for tonight's two seed cities;
production would use a proper polygon/H3-cell membership test — noted as a known
simplification, not hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BBox:
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float

    def contains(self, lat: float, lng: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lng <= lng <= self.max_lng


@dataclass(frozen=True)
class RateCard:
    base_fare: float
    per_km_rate: float
    per_min_rate: float
    booking_fee: float
    surge_cap: float = 3.0
    commission_pct: float = 0.20  # GAP: AS-06, business-ambiguous, implemented as config
    cancellation_fee: float = 30.0  # GAP: AS-06, flat fee after driver assignment, per-city


@dataclass(frozen=True)
class DispatchConfig:
    """Round 12 stakeholder council: closes (most of) PLAN's own AS-05 gap --
    "MAX_DISPATCH_ATTEMPTS = 3, offer TTL 15s, claim TTL 25s, overall matching
    deadline 90s ... all are per-city config, not constants" -- open since Phase 0,
    while its sibling AS-06 (commission/cancellation fee) was closed in Round 1.
    These defaults are the fallback if a city has no `city_configs` row yet, mirroring
    `RateCard`'s own fallback role -- `libs.geo.city_config_repo.get_dispatch_config`
    is the live, DB-backed, admin-editable path every real reader goes through.

    `max_dispatch_attempts` deliberately stays a global `Settings` value, not a field
    here -- it's read at every one of TripService's 15+ `apply_transition` call
    sites, and making it per-city would mean touching all of them; see
    PROGRESS.md Round 12 for why that's a deliberate, documented partial closure."""

    offer_ttl_seconds: int = 15
    claim_ttl_seconds: int = 25
    matching_deadline_seconds: int = 90
    candidate_radius_km: float = 3.0
    candidate_count: int = 20


@dataclass(frozen=True)
class SpeedProfile:
    """Feeds the pure-CPU FastEtaEstimator (PLAN amendment AM-01) — no network call."""

    base_kmh: float = 28.0  # a mid-size Indian city's typical average moving speed
    road_winding_factor: float = 1.35  # straight-line distance underestimates road distance
    peak_hour_multiplier: float = 1.6  # applied 08-11h and 17-21h local
    peak_hours: tuple[tuple[int, int], ...] = ((8, 11), (17, 21))


@dataclass(frozen=True)
class CityConfig:
    city_id: str
    name: str
    bbox: BBox
    center_lat: float
    center_lng: float
    neighbors: tuple[str, ...] = field(default_factory=tuple)
    rate_card: RateCard = field(default_factory=lambda: RateCard(60.0, 12.0, 1.5, 5.0))
    speed_profile: SpeedProfile = field(default_factory=SpeedProfile)
    dispatch_config: DispatchConfig = field(default_factory=DispatchConfig)


# Seed registry — GAP AS-09: Pune + Mumbai chosen specifically so they are wired as
# geographic neighbours in the config, exercising the B1 border-supply fix even though
# the two cities are not literally adjacent in reality; what matters for the demo/tests
# is that the neighbour-fanout code path is real and exercised, not the real-world
# geographic accuracy of the "neighbour" label.
CITIES: dict[str, CityConfig] = {
    "pune": CityConfig(
        city_id="pune",
        name="Pune",
        bbox=BBox(min_lat=18.40, max_lat=18.65, min_lng=73.75, max_lng=73.95),
        center_lat=18.5204,
        center_lng=73.8567,
        neighbors=("mumbai",),
        rate_card=RateCard(base_fare=50.0, per_km_rate=11.0, per_min_rate=1.5, booking_fee=5.0),
    ),
    "mumbai": CityConfig(
        city_id="mumbai",
        name="Mumbai",
        bbox=BBox(min_lat=18.90, max_lat=19.30, min_lng=72.75, max_lng=73.05),
        center_lat=19.0760,
        center_lng=72.8777,
        neighbors=("pune",),
        rate_card=RateCard(base_fare=60.0, per_km_rate=13.0, per_min_rate=1.8, booking_fee=6.0),
    ),
}


def city_for(lat: float, lng: float) -> CityConfig | None:
    for city in CITIES.values():
        if city.bbox.contains(lat, lng):
            return city
    return None


def get_city(city_id: str) -> CityConfig:
    try:
        return CITIES[city_id]
    except KeyError as exc:
        raise KeyError(f"unknown city_id={city_id!r}") from exc


def neighbor_ids(city_id: str) -> tuple[str, ...]:
    return get_city(city_id).neighbors
