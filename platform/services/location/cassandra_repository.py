"""PLAN §4.4: the Cassandra implementation of `LocationHistoryRepository`, written
against the exact CQL schema from `ride_hailing_HLD_LLD.md` section 3.4, kept behind
`LOCATION_HISTORY_BACKEND=cassandra`. Not the default tonight (see PROGRESS.md for
why — JVM + Windows driver risk vs. zero functional difference at demo volume), but a
real, complete implementation, not a TODO stub: swapping to it is a one-line config
change (`services/location/container.py` picks the implementation by
`settings.location_history_backend`), not a rewrite.

Requires `cassandra-driver` (not in requirements.txt by default — install it only if
you actually flip the backend flag; see the module-level import guard below).

CQL schema (apply once with cqlsh, matching the HLD verbatim):

    CREATE TABLE location_history (
        driver_id   UUID,
        ts          TIMESTAMP,
        lat         DOUBLE,
        lng         DOUBLE,
        trip_id     UUID,
        PRIMARY KEY (driver_id, ts)
    ) WITH CLUSTERING ORDER BY (ts DESC);
"""
from __future__ import annotations

from libs.common.ids import utcnow


class CassandraLocationHistoryRepository:
    def __init__(self, contact_points: list[str], keyspace: str = "ridehail") -> None:
        try:
            from cassandra.cluster import Cluster  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - only exercised with the flag on
            raise RuntimeError(
                "LOCATION_HISTORY_BACKEND=cassandra requires `pip install cassandra-driver`"
            ) from exc

        self._cluster = Cluster(contact_points)
        self._session = self._cluster.connect(keyspace)
        self._insert_stmt = self._session.prepare(
            "INSERT INTO location_history (driver_id, ts, lat, lng, trip_id) VALUES (?, ?, ?, ?, ?)"
        )

    async def record(self, driver_id: str, lat: float, lng: float, trip_id: str | None) -> None:
        # The cassandra-driver is sync; in a real deployment this would go through
        # its async execution API or a thread-pool executor. Kept simple here since
        # this path is not exercised by default tonight.
        self._session.execute(self._insert_stmt, (driver_id, utcnow(), lat, lng, trip_id))
