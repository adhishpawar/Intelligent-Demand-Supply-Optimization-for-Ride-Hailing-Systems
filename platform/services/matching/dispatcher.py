"""The dispatch step: one call = candidate retrieval -> Postgres validation -> score
-> sequential claim attempts -> a single winning offer (or "no driver available").

Sequential-only tonight (PLAN §5.1 cut-order item 6: "Batch-offer dispatch mode cut
before sequential dispatch" — batch/simultaneous-offer-to-top-N is the stretch goal,
not built). Everything up to and including scoring is zero-network-I/O (PLAN A1); the
one Redis claim call and the one Postgres validation call are each a single round
trip regardless of candidate count.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from libs.geo.cities import CityConfig
from libs.geo.driver_index import DriverIndexRepository
from services.matching.claim import DriverClaimService
from services.matching.repository import validate_and_enrich
from services.matching.scoring import DriverCandidateInfo, ScoringStrategy


@dataclass(frozen=True)
class DispatchResult:
    driver_id: str | None
    eta_seconds: float | None
    distance_m: float | None
    candidates_considered: int


class Dispatcher:
    def __init__(self, index: DriverIndexRepository, claims: DriverClaimService, scorer: ScoringStrategy) -> None:
        self._index = index
        self._claims = claims
        self._scorer = scorer

    async def dispatch_once(
        self,
        session: AsyncSession,
        *,
        trip_id: str,
        city: CityConfig,
        pickup_lat: float,
        pickup_lng: float,
        excluded_driver_ids: set[str],
        radius_km: float,
        count: int,
        at: datetime,
    ) -> DispatchResult:
        raw_candidates = await self._index.search_with_neighbors(
            city.city_id, city.neighbors, pickup_lat, pickup_lng, radius_km, count
        )
        raw_candidates = [c for c in raw_candidates if c.driver_id not in excluded_driver_ids]
        if not raw_candidates:
            return DispatchResult(None, None, None, 0)

        enrichment = await validate_and_enrich(session, [c.driver_id for c in raw_candidates])
        candidate_infos = [
            DriverCandidateInfo(
                driver_id=c.driver_id, lat=c.lat, lng=c.lng,
                rating_avg=enrichment[c.driver_id]["rating_avg"], acceptance_rate=enrichment[c.driver_id]["acceptance_rate"]
            )
            for c in raw_candidates
            if c.driver_id in enrichment  # "the geo index proposes, the database disposes" (PLAN A5)
        ]
        if not candidate_infos:
            return DispatchResult(None, None, None, 0)

        ranked = await self._scorer.rank(candidate_infos, pickup_lat, pickup_lng, at)

        for scored in ranked:
            won = await self._claims.try_claim(scored.driver_id, trip_id)
            if won:
                return DispatchResult(scored.driver_id, scored.eta_seconds, scored.distance_m, len(candidate_infos))
            # someone else's dispatch cycle claimed this driver a moment ago -- try the next-ranked candidate.

        return DispatchResult(None, None, None, len(candidate_infos))
