"""Strategy pattern for matching (PLAN §3.2): the scoring algorithm/weights are
swappable because [[part2]] section 5.1 names ML-weighted ranking as a future
consumer of this exact interface. `rank()` is declared `async` even though tonight's
implementation performs zero I/O (PLAN amendment AM-16 / C5 in the review) so that a
future `MLWeightedStrategy` calling the inference service (with its own timeout and
fallback, per part2 section 5.2.5) is a new implementation, not a signature change at
every call site.

Nothing in this module performs network I/O (PLAN A1 - the matching hot path must
never do that) - `FastEtaEstimator` is the pure-CPU two-tier ETA from libs.geo.eta.
`tests/architecture/test_no_network_io_in_matching_hotpath.py` asserts this holds.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from libs.geo.cities import CityConfig
from libs.geo.driver_index import Candidate
from libs.geo.eta import FastEtaEstimator


@dataclass(frozen=True)
class DriverCandidateInfo:
    """What the scorer needs about a candidate beyond raw distance -- pulled from the
    one batched Postgres validation query (PLAN A5: "the geo index proposes, the
    database disposes"), never a per-candidate round trip."""

    driver_id: str
    lat: float
    lng: float
    rating_avg: float
    acceptance_rate: float


@dataclass(frozen=True)
class ScoredCandidate:
    driver_id: str
    score: float
    eta_seconds: float
    distance_m: float


class ScoringStrategy(Protocol):
    async def rank(
        self, candidates: list[DriverCandidateInfo], pickup_lat: float, pickup_lng: float, at: datetime
    ) -> list[ScoredCandidate]: ...


class WeightedScoreStrategy:
    """The default. Mirrors ride_hailing_HLD_LLD.md section 3.2's formula:
        score = w1*(1/distance) + w2*(1/eta) + w3*rating + w4*acceptance_rate
    Weights favor ETA over raw distance by default, matching the HLD's rationale that
    a driver 1km away against traffic can be slower than one 2km away on a clear road.
    """

    def __init__(
        self, city: CityConfig, w_distance: float = 0.2, w_eta: float = 0.4, w_rating: float = 0.2, w_acceptance: float = 0.2
    ) -> None:
        self._eta_estimator = FastEtaEstimator(city.speed_profile)
        self._w = (w_distance, w_eta, w_rating, w_acceptance)

    async def rank(
        self, candidates: list[DriverCandidateInfo], pickup_lat: float, pickup_lng: float, at: datetime
    ) -> list[ScoredCandidate]:
        w_distance, w_eta, w_rating, w_acceptance = self._w
        scored: list[ScoredCandidate] = []
        for c in candidates:
            distance_m = self._eta_estimator.distance_m(c.lat, c.lng, pickup_lat, pickup_lng)
            eta_s = self._eta_estimator.eta_seconds(c.lat, c.lng, pickup_lat, pickup_lng, at)
            score = (
                w_distance * (1.0 / max(distance_m, 1.0))
                + w_eta * (1.0 / max(eta_s, 1.0))
                + w_rating * (c.rating_avg / 5.0)
                + w_acceptance * c.acceptance_rate
            )
            scored.append(ScoredCandidate(driver_id=c.driver_id, score=score, eta_seconds=eta_s, distance_m=distance_m))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored


class NearestOnlyStrategy:
    """Baseline/ablation strategy -- pure distance ranking, no rating/acceptance
    weighting. Useful for A/B comparison against the weighted default and for tests
    that want a deterministic, trivially-reasoned-about ranking."""

    def __init__(self, city: CityConfig) -> None:
        self._eta_estimator = FastEtaEstimator(city.speed_profile)

    async def rank(
        self, candidates: list[DriverCandidateInfo], pickup_lat: float, pickup_lng: float, at: datetime
    ) -> list[ScoredCandidate]:
        scored = [
            ScoredCandidate(
                driver_id=c.driver_id,
                score=-self._eta_estimator.distance_m(c.lat, c.lng, pickup_lat, pickup_lng),
                eta_seconds=self._eta_estimator.eta_seconds(c.lat, c.lng, pickup_lat, pickup_lng, at),
                distance_m=self._eta_estimator.distance_m(c.lat, c.lng, pickup_lat, pickup_lng),
            )
            for c in candidates
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored
