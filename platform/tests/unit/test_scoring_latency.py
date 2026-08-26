"""PLAN §6.1 finding A1 / §8 Chairman synthesis: "Measured, asserted at <25ms p99 for
20 candidates end-to-end." This is that measurement — not an estimate, an actual
timed run of the real `WeightedScoreStrategy.rank()` against 20 candidates, repeated
to compute a real p99. If this test ever fails, the matching hot path has regressed
into doing something more expensive than pure arithmetic — exactly the class of bug
finding A1 exists to prevent.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone

import pytest

from libs.geo.cities import get_city
from services.matching.scoring import DriverCandidateInfo, WeightedScoreStrategy

PUNE_LAT, PUNE_LNG = 18.5204, 73.8567


def _make_candidates(n: int) -> list[DriverCandidateInfo]:
    rng = random.Random(42)
    return [
        DriverCandidateInfo(
            driver_id=f"driver-{i}",
            lat=PUNE_LAT + rng.uniform(-0.02, 0.02),
            lng=PUNE_LNG + rng.uniform(-0.02, 0.02),
            rating_avg=rng.uniform(3.5, 5.0),
            acceptance_rate=rng.uniform(0.6, 1.0),
        )
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_scoring_20_candidates_p99_under_25ms() -> None:
    city = get_city("pune")
    scorer = WeightedScoreStrategy(city)
    candidates = _make_candidates(20)
    now = datetime.now(timezone.utc)

    samples_ms: list[float] = []
    for _ in range(200):  # enough samples for a meaningful p99
        start = time.perf_counter()
        ranked = await scorer.rank(candidates, PUNE_LAT, PUNE_LNG, now)
        elapsed_ms = (time.perf_counter() - start) * 1000
        samples_ms.append(elapsed_ms)
        assert len(ranked) == 20

    samples_ms.sort()
    p99 = samples_ms[int(len(samples_ms) * 0.99) - 1]
    p50 = samples_ms[len(samples_ms) // 2]

    assert p99 < 25.0, f"scoring p99={p99:.3f}ms exceeds the 25ms budget (p50={p50:.3f}ms) -- see PLAN A1"
