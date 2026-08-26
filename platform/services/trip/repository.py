"""Trip repository: owns every write to the `trips` table and is the ONLY place that
calls `fsm.apply()` against real data — every transition goes through
`apply_transition`, which:

1. `SELECT ... FOR UPDATE` the trip row inside the transaction — this serializes
   concurrent events against the SAME trip (e.g. a double-click on "accept") so the
   FSM computes its transition against a snapshot nothing else can be mutating.
2. Builds a `TransitionContext` from that snapshot and calls the pure `fsm.apply()`.
3. Writes the new status with an optimistic-version-guarded UPDATE anyway (defense in
   depth per PLAN A2 — belt AND braces even though the FOR UPDATE lock already
   prevents the same-trip race the version check is a second, independent guard for).
4. Appends a `trip_events` audit row (invariant I7).

This is the ONLY method every trip mutation goes through — a handler cannot bypass the
FSM and hand-write a status, because there is no other method that writes `status`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.errors import ConflictError, ForbiddenError, NotFoundError
from libs.common.ids import new_id, utcnow
from services.trip.fsm import Event, State, TransitionContext, apply


@dataclass
class TripRecord:
    trip_id: str
    rider_id: str
    driver_id: str | None
    city_id: str
    status: str
    version: int
    pickup_lat: float
    pickup_lng: float
    drop_lat: float
    drop_lng: float
    fare_estimate: float | None
    fare_final: float | None
    surge_multiplier: float
    dispatch_attempts: int
    current_offer_driver_id: str | None
    current_offer_id: str | None
    current_offer_expires_at: datetime | None
    matching_deadline_at: datetime | None
    cancellation_fee_applied: float
    requested_at: datetime

    def __post_init__(self) -> None:
        self.trip_id = str(self.trip_id)
        self.rider_id = str(self.rider_id)
        self.driver_id = str(self.driver_id) if self.driver_id else None
        self.current_offer_driver_id = str(self.current_offer_driver_id) if self.current_offer_driver_id else None
        self.current_offer_id = str(self.current_offer_id) if self.current_offer_id else None
        self.fare_estimate = float(self.fare_estimate) if self.fare_estimate is not None else None
        self.fare_final = float(self.fare_final) if self.fare_final is not None else None
        self.surge_multiplier = float(self.surge_multiplier)
        self.cancellation_fee_applied = float(self.cancellation_fee_applied)


class Increment:
    """Sentinel for a field_updates value meaning "col = col + n", computed inside the
    same FOR UPDATE-locked statement rather than peeked-then-written by the caller —
    the only TOCTOU-free way to bump dispatch_attempts under concurrent transitions on
    the same trip row."""

    def __init__(self, by: int = 1) -> None:
        self.by = by


_COLUMNS = (
    "trip_id, rider_id, driver_id, city_id, status, version, pickup_lat, pickup_lng, drop_lat, drop_lng, "
    "fare_estimate, fare_final, surge_multiplier, dispatch_attempts, current_offer_driver_id, current_offer_id, "
    "current_offer_expires_at, matching_deadline_at, cancellation_fee_applied, requested_at"
)


class TripRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        rider_id: str,
        city_id: str,
        pickup_lat: float,
        pickup_lng: float,
        pickup_geohash7: str,
        drop_lat: float,
        drop_lng: float,
        drop_geohash7: str,
        fare_estimate: float,
        surge_multiplier: float,
        matching_deadline_seconds: int,
    ) -> TripRecord:
        row = (
            await self._session.execute(
                text(
                    f"""
                    INSERT INTO trips (
                        rider_id, city_id, pickup_lat, pickup_lng, pickup_geohash7,
                        drop_lat, drop_lng, drop_geohash7, status, fare_estimate,
                        surge_multiplier, matching_deadline_at
                    ) VALUES (
                        :rider_id, :city_id, :pickup_lat, :pickup_lng, :pickup_geohash7,
                        :drop_lat, :drop_lng, :drop_geohash7, 'REQUESTED', :fare_estimate,
                        :surge_multiplier, now() + make_interval(secs => :deadline_s)
                    ) RETURNING {_COLUMNS}
                    """
                ),
                {
                    "rider_id": rider_id, "city_id": city_id, "pickup_lat": pickup_lat, "pickup_lng": pickup_lng,
                    "pickup_geohash7": pickup_geohash7, "drop_lat": drop_lat, "drop_lng": drop_lng,
                    "drop_geohash7": drop_geohash7, "fare_estimate": fare_estimate,
                    "surge_multiplier": surge_multiplier, "deadline_s": matching_deadline_seconds,
                },
            )
        ).mappings().first()
        trip = TripRecord(**dict(row))
        await self._audit(trip.trip_id, None, "REQUESTED", "TRIP_CREATED", None, None, {})
        return trip

    async def get(self, trip_id: str) -> TripRecord:
        row = (
            await self._session.execute(text(f"SELECT {_COLUMNS} FROM trips WHERE trip_id = :id"), {"id": trip_id})
        ).mappings().first()
        if row is None:
            raise NotFoundError("trip not found", trip_id=trip_id)
        return TripRecord(**dict(row))

    def assert_ownership(self, trip: TripRecord, *, principal_id: str, is_admin: bool) -> None:
        """PLAN §3.1 Layer 3: a principal authorized for a ROLE is not automatically
        authorized for this ROW."""
        if is_admin:
            return
        if principal_id not in (trip.rider_id, trip.driver_id):
            raise ForbiddenError("not authorized for this trip", trip_id=trip.trip_id)

    async def apply_transition(
        self,
        trip_id: str,
        event: Event,
        *,
        now: datetime,
        max_dispatch_attempts: int,
        responding_driver_id: str | None = None,
        actor_role: str | None = None,
        actor_id: str | None = None,
        field_updates: dict | None = None,
        audit_payload: dict | None = None,
    ) -> TripRecord:
        row = (
            await self._session.execute(
                text(f"SELECT {_COLUMNS} FROM trips WHERE trip_id = :id FOR UPDATE"), {"id": trip_id}
            )
        ).mappings().first()
        if row is None:
            raise NotFoundError("trip not found", trip_id=trip_id)
        trip = TripRecord(**dict(row))

        ctx = TransitionContext(
            now=now,
            dispatch_attempts=trip.dispatch_attempts,
            max_dispatch_attempts=max_dispatch_attempts,
            current_offer_driver_id=trip.current_offer_driver_id,
            current_offer_expires_at=trip.current_offer_expires_at,
            responding_driver_id=responding_driver_id,
            matching_deadline_at=trip.matching_deadline_at,
        )
        new_state = apply(State(trip.status), event, ctx)

        updates = dict(field_updates or {})
        updates["status"] = new_state.value

        set_parts = []
        bind_params: dict = {}
        for k, v in updates.items():
            if isinstance(v, Increment):
                set_parts.append(f"{k} = {k} + :{k}")
                bind_params[k] = v.by
            else:
                set_parts.append(f"{k} = :{k}")
                bind_params[k] = v
        set_clause = ", ".join(set_parts)

        result = await self._session.execute(
            text(
                f"UPDATE trips SET {set_clause}, version = version + 1, updated_at = now() "
                f"WHERE trip_id = :trip_id AND version = :expected_version"
            ),
            {**bind_params, "trip_id": trip_id, "expected_version": trip.version},
        )
        if result.rowcount == 0:
            # Defense in depth (PLAN A2): the FOR UPDATE lock above should make this
            # unreachable in practice, but a version mismatch is still checked
            # explicitly rather than trusted implicitly.
            raise ConflictError("trip was modified concurrently — refresh and retry", trip_id=trip_id)

        await self._audit(trip_id, trip.status, new_state.value, event.value, actor_role, actor_id, audit_payload or {})
        return await self.get(trip_id)

    async def _audit(
        self, trip_id: str, from_status: str | None, to_status: str, event_type: str,
        actor_role: str | None, actor_id: str | None, payload: dict
    ) -> None:
        import json

        await self._session.execute(
            text(
                """
                INSERT INTO trip_events (trip_id, from_status, to_status, event_type, actor_role, actor_id, payload)
                VALUES (:trip_id, :from_status, :to_status, :event_type, :actor_role, :actor_id, CAST(:payload AS JSONB))
                """
            ),
            {
                "trip_id": trip_id, "from_status": from_status, "to_status": to_status, "event_type": event_type,
                "actor_role": actor_role, "actor_id": actor_id, "payload": json.dumps(payload, default=str),
            },
        )

    async def list_audit_trail(self, trip_id: str) -> list[dict]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT event_id, from_status, to_status, event_type, actor_role, actor_id, payload, created_at "
                    "FROM trip_events WHERE trip_id = :id ORDER BY created_at"
                ),
                {"id": trip_id},
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def find_trips_needing_dispatch(self, now: datetime) -> list[str]:
        """Trips in MATCHING with no live offer outstanding — either brand new or
        whose offer already expired. This is what the dispatch orchestrator polls;
        the explicit reject/timeout paths ALSO call dispatch immediately for lower
        latency, so this poll is the backstop (PLAN A3/A4: never trust a single path)."""
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT trip_id FROM trips
                    WHERE status = 'MATCHING'
                      AND (current_offer_id IS NULL OR current_offer_expires_at < :now)
                      AND matching_deadline_at > :now
                    ORDER BY requested_at
                    LIMIT 50
                    """
                ),
                {"now": now},
            )
        ).all()
        return [str(r[0]) for r in rows]

    async def find_active_offer_for_driver(self, driver_id: str) -> TripRecord | None:
        """Catch-up path for the driver offer WS (PLAN's "never trust a single path"
        principle, applied here to Redis pub/sub specifically): pub/sub has no
        replay, so a driver whose WebSocket reconnects (a page reload, a network
        blip) after an offer was already published would otherwise never see it
        until it naturally times out. The driver app calls this once on connect to
        recover any already-active offer that the live push may have missed."""
        row = (
            await self._session.execute(
                text(
                    f"SELECT {_COLUMNS} FROM trips WHERE current_offer_driver_id = :driver_id "
                    "AND status = 'MATCHING' AND current_offer_expires_at > now() LIMIT 1"
                ),
                {"driver_id": driver_id},
            )
        ).mappings().first()
        return TripRecord(**dict(row)) if row else None

    async def list_recent(self, limit: int = 50) -> list[TripRecord]:
        """Admin-only aggregate read (PLAN §6.4 D5: the admin console must be able to
        show real trip activity, not a mocked feed)."""
        rows = (
            await self._session.execute(
                text(f"SELECT {_COLUMNS} FROM trips ORDER BY requested_at DESC LIMIT :limit"), {"limit": limit}
            )
        ).mappings().all()
        return [TripRecord(**dict(r)) for r in rows]

    async def find_trips_past_deadline(self, now: datetime) -> list[str]:
        rows = (
            await self._session.execute(
                text("SELECT trip_id FROM trips WHERE status = 'MATCHING' AND matching_deadline_at <= :now LIMIT 50"),
                {"now": now},
            )
        ).all()
        return [str(r[0]) for r in rows]
