"""Trip business logic. Every state-changing method goes through
`TripRepository.apply_transition` (which itself is the only path to `fsm.apply()`) and
runs inside a `UnitOfWork` so the state change and its outbox event commit atomically
(PLAN §1.2(c)). PLAN amendment AM-04: user-visible pushes happen synchronously here
(via Redis pub/sub, `libs` WS fan-out) — Kafka/the outbox carry durability and
fan-out to secondary consumers (notifications, future ML), never the thing the rider's
screen depends on.
"""
from __future__ import annotations

from datetime import timedelta

from redis.asyncio import Redis

from libs.common.config import Settings
from libs.common.errors import ConflictError, ForbiddenError
from libs.common.ids import new_id, utcnow
from libs.contracts.events import RideAssigned, RideCancelled, RideCompleted, RideRequested
from libs.contracts.topics import Topic
from libs.geo.cities import city_for, get_city
from libs.geo.eta import FastEtaEstimator
from libs.geo.geohash import encode as geohash_encode
from libs.persistence.unit_of_work import UnitOfWork
from libs.geo.city_config_repo import get_rate_card
from libs.pricing.fare import estimate_fare
from libs.pricing.surge_reader import read_surge_multiplier
from libs.security.principal import Principal, Role
from services.trip.fsm import Event
from services.trip.matching_client import MatchingClient
from services.trip.repository import Increment, TripRecord, TripRepository
from services.trip.ws_fanout import publish_trip_update, publish_driver_offer


class TripService:
    def __init__(
        self, uow_factory, redis: Redis, matching_client: MatchingClient, settings: Settings
    ) -> None:
        self._uow_factory = uow_factory
        self._redis = redis
        self._matching = matching_client
        self._settings = settings

    async def request_ride(self, rider_id: str, pickup: tuple[float, float], drop: tuple[float, float]) -> TripRecord:
        city = city_for(*pickup)
        if city is None:
            raise ConflictError("pickup location is outside any served city", lat=pickup[0], lng=pickup[1])

        eta_estimator = FastEtaEstimator(city.speed_profile)
        distance_m = eta_estimator.distance_m(pickup[0], pickup[1], drop[0], drop[1])
        duration_s = eta_estimator.eta_seconds(pickup[0], pickup[1], drop[0], drop[1], utcnow())
        pickup_geohash = geohash_encode(pickup[0], pickup[1])
        surge = await read_surge_multiplier(self._redis, city.city_id, pickup_geohash)

        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            # Round 3 stakeholder council (tech lead): a rider could otherwise call
            # this repeatedly (double-click, buggy retry, deliberate abuse) and pile
            # up multiple concurrent live trips -- see
            # TripRepository.find_active_for_rider's docstring. Checked inside the
            # same transaction as the insert below; ux_trips_one_active_per_rider
            # (V003) is the DB-level backstop if this check and a concurrent request
            # somehow race.
            existing = await repo.find_active_for_rider(rider_id)
            if existing is not None:
                raise ConflictError(
                    "you already have an active ride in progress", trip_id=existing.trip_id, status=existing.status,
                )
            # Round 1 stakeholder council: the rate card is now DB-backed and
            # admin-editable (city_configs), not a hardcoded Python constant --
            # read live, inside the same transaction, rather than the in-memory
            # default.
            rate_card = await get_rate_card(uow.session, city.city_id)
            fare_estimate = estimate_fare(rate_card, distance_m, duration_s, surge)
            trip = await repo.create(
                rider_id=rider_id, city_id=city.city_id,
                pickup_lat=pickup[0], pickup_lng=pickup[1], pickup_geohash7=pickup_geohash,
                drop_lat=drop[0], drop_lng=drop[1], drop_geohash7=geohash_encode(drop[0], drop[1]),
                fare_estimate=fare_estimate, surge_multiplier=surge,
                matching_deadline_seconds=self._settings.matching_deadline_seconds,
            )
            trip = await repo.apply_transition(
                trip.trip_id, Event.START_MATCHING, now=utcnow(),
                max_dispatch_attempts=self._settings.max_dispatch_attempts,
                actor_role="RIDER", actor_id=rider_id,
            )
            uow.emit(
                Topic.RIDE_REQUESTED, city.city_id,
                RideRequested(
                    trip_id=trip.trip_id, rider_id=rider_id, city_id=city.city_id,
                    pickup_geohash7=pickup_geohash, pickup_lat=pickup[0], pickup_lng=pickup[1],
                    drop_lat=drop[0], drop_lng=drop[1], fare_estimate=fare_estimate, surge_multiplier=surge,
                ).model_dump(mode="json"),
            )
        return trip

    async def dispatch_step(self, trip_id: str) -> None:
        """One round of matching for one trip: ask Matching for the best available
        driver (excluding anyone already tried on this trip), then apply the result
        as a single guarded FSM transition. Called both immediately (reject/timeout
        paths, for low latency) and from the background poll (the backstop — PLAN
        A3/A4: never trust a single path)."""
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.get(trip_id)
            if trip.status != "MATCHING":
                return  # already resolved by a concurrent path; nothing to do

            if trip.current_offer_driver_id:
                # A prior offer is being superseded by this redispatch round (it
                # timed out — the orchestrator only calls dispatch_step for trips
                # whose offer already expired). Release that driver's claim now
                # rather than waiting out its TTL, so they're immediately eligible
                # again for a DIFFERENT trip's dispatch.
                await self._matching.release_claim(driver_id=trip.current_offer_driver_id, trip_id=trip_id)

            excluded = await self._excluded_driver_ids(trip_id)
            result = await self._matching.dispatch(
                trip_id=trip_id, city_id=trip.city_id,
                pickup_lat=trip.pickup_lat, pickup_lng=trip.pickup_lng, excluded_driver_ids=excluded,
            )

            offer_fields = {}
            if result["driver_id"]:
                offer_fields = {
                    "current_offer_driver_id": result["driver_id"],
                    "current_offer_id": new_id(),
                    "current_offer_expires_at": utcnow() + timedelta(seconds=self._settings.offer_ttl_seconds),
                }
            else:
                offer_fields = {"current_offer_driver_id": None, "current_offer_id": None, "current_offer_expires_at": None}

            try:
                trip = await repo.apply_transition(
                    trip_id, Event.REDISPATCH, now=utcnow(),
                    max_dispatch_attempts=self._settings.max_dispatch_attempts,
                    field_updates={**offer_fields, "dispatch_attempts": Increment(1)},
                    audit_payload={"driver_id": result["driver_id"], "candidates": result["candidates_considered"]},
                )
            except ConflictError:
                # attempts exhausted right now (race with the peek in this method,
                # or a prior round already exhausted them) -> fall through to exhaust.
                trip = await repo.apply_transition(
                    trip_id, Event.EXHAUST_DISPATCH, now=utcnow(),
                    max_dispatch_attempts=self._settings.max_dispatch_attempts,
                )
                await publish_trip_update(self._redis, trip_id, {"status": trip.status})
                return

        if result["driver_id"]:
            # Round 1 stakeholder council, driver pain #1: the offer must carry
            # enough for the driver to actually decide whether to take it --
            # destination and expected fare, not just a countdown and an ETA number.
            await publish_driver_offer(
                self._redis, result["driver_id"],
                {
                    "trip_id": trip_id, "offer_id": trip.current_offer_id,
                    "expires_at": trip.current_offer_expires_at.isoformat(),
                    "pickup_lat": trip.pickup_lat, "pickup_lng": trip.pickup_lng,
                    "drop_lat": trip.drop_lat, "drop_lng": trip.drop_lng,
                    "fare_estimate": trip.fare_estimate, "eta_seconds": result["eta_seconds"],
                },
            )
        await publish_trip_update(self._redis, trip_id, {"status": trip.status, "dispatch_attempts": trip.dispatch_attempts})

    async def exhaust_if_deadline_passed(self, trip_id: str) -> None:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.get(trip_id)
            if trip.status != "MATCHING":
                return
            stale_offer_driver = trip.current_offer_driver_id
            trip = await repo.apply_transition(
                trip_id, Event.EXPIRE, now=utcnow(), max_dispatch_attempts=self._settings.max_dispatch_attempts,
            )
        if stale_offer_driver:
            await self._matching.release_claim(driver_id=stale_offer_driver, trip_id=trip_id)
        await publish_trip_update(self._redis, trip_id, {"status": trip.status})

    async def _excluded_driver_ids(self, trip_id: str) -> list[str]:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            events = await repo.list_audit_trail(trip_id)
        seen = set()
        for e in events:
            driver_id = e.get("payload", {}).get("driver_id")
            if driver_id:
                seen.add(driver_id)
        return list(seen)

    async def respond_to_offer(self, trip_id: str, driver_id: str, action: str) -> TripRecord:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            current = await repo.get(trip_id)
            if current.current_offer_driver_id != driver_id:
                # Layer 3 ownership, applied to the "current offer" resource: a driver
                # may only respond to an offer actually addressed to them. ACCEPT is
                # additionally guarded inside the FSM itself (_accept_guard); REJECT
                # has no FSM-level guard distinguishing offer ownership, so it's
                # checked explicitly here.
                raise ForbiddenError("no active offer for this driver on this trip")

            if action == "ACCEPT":
                trip = await repo.apply_transition(
                    trip_id, Event.ACCEPT, now=utcnow(), max_dispatch_attempts=self._settings.max_dispatch_attempts,
                    responding_driver_id=driver_id,
                    field_updates={"driver_id": driver_id, "assigned_at": utcnow()},
                    actor_role="DRIVER", actor_id=driver_id,
                )
                uow.emit(
                    Topic.RIDE_ASSIGNED, trip.city_id,
                    RideAssigned(
                        trip_id=trip_id, driver_id=driver_id, city_id=trip.city_id,
                        time_to_match_seconds=(utcnow() - trip.requested_at).total_seconds(),
                        dispatch_attempts=trip.dispatch_attempts,
                    ).model_dump(mode="json"),
                )
            elif action == "REJECT":
                trip = await repo.apply_transition(
                    trip_id, Event.REDISPATCH, now=utcnow(), max_dispatch_attempts=self._settings.max_dispatch_attempts,
                    field_updates={"current_offer_driver_id": None, "current_offer_id": None, "current_offer_expires_at": None},
                    actor_role="DRIVER", actor_id=driver_id, audit_payload={"reason": "driver_rejected"},
                )
            else:
                raise ForbiddenError(f"unknown action {action!r}")

        await publish_trip_update(self._redis, trip_id, {"status": trip.status})
        # The claim has served its purpose the instant the driver responds, either
        # way -- release it now rather than waiting out its TTL (see PLAN §matching
        # claim.py docstring / PROGRESS.md for the bug this fixes: a driver
        # completing a ride would otherwise stay unmatchable for up to
        # claim_ttl_seconds even after driver_profiles.status correctly returns to
        # ONLINE).
        await self._matching.release_claim(driver_id=driver_id, trip_id=trip_id)
        if action == "ACCEPT":
            from services.trip.location_client import set_driver_on_trip
            await set_driver_on_trip(self._settings, driver_id, trip_id)
        elif action == "REJECT":
            await self.dispatch_step(trip_id)  # immediate re-dispatch, low latency (PLAN A4)
        return trip

    async def _simple_event(
        self, trip_id: str, event: Event, principal: Principal, field_updates: dict | None = None
    ) -> TripRecord:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.get(trip_id)
            repo.assert_ownership(trip, principal_id=principal.user_id, is_admin=principal.has_role(Role.ADMIN))
            trip = await repo.apply_transition(
                trip_id, event, now=utcnow(), max_dispatch_attempts=self._settings.max_dispatch_attempts,
                field_updates=field_updates, actor_role=principal.role.value, actor_id=principal.user_id,
            )
        await publish_trip_update(self._redis, trip_id, {"status": trip.status})
        return trip

    async def start_navigation(self, trip_id: str, principal: Principal) -> TripRecord:
        return await self._simple_event(trip_id, Event.START_NAVIGATION, principal)

    async def confirm_arrival(self, trip_id: str, principal: Principal) -> TripRecord:
        return await self._simple_event(trip_id, Event.CONFIRM_ARRIVAL, principal, {"arrived_at": utcnow()})

    async def start_trip(self, trip_id: str, principal: Principal) -> TripRecord:
        return await self._simple_event(trip_id, Event.START_TRIP, principal, {"started_at": utcnow()})

    async def complete_trip(self, trip_id: str, principal: Principal) -> TripRecord:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.get(trip_id)
            repo.assert_ownership(trip, principal_id=principal.user_id, is_admin=principal.has_role(Role.ADMIN))
            city = get_city(trip.city_id)
            eta_estimator = FastEtaEstimator(city.speed_profile)
            distance_m = eta_estimator.distance_m(trip.pickup_lat, trip.pickup_lng, trip.drop_lat, trip.drop_lng)
            duration_s = (utcnow() - (trip.requested_at)).total_seconds()
            rate_card = await get_rate_card(uow.session, trip.city_id)
            fare_final = estimate_fare(rate_card, distance_m, duration_s, trip.surge_multiplier)

            trip = await repo.apply_transition(
                trip.trip_id, Event.COMPLETE_TRIP, now=utcnow(), max_dispatch_attempts=self._settings.max_dispatch_attempts,
                field_updates={"completed_at": utcnow(), "fare_final": fare_final},
                actor_role=principal.role.value, actor_id=principal.user_id,
            )
            uow.emit(
                Topic.RIDE_COMPLETED, trip.city_id,
                RideCompleted(
                    trip_id=trip.trip_id, rider_id=trip.rider_id, driver_id=trip.driver_id, city_id=trip.city_id,
                    fare_final=fare_final, distance_m=distance_m, duration_s=duration_s,
                ).model_dump(mode="json"),
            )
        await publish_trip_update(self._redis, trip.trip_id, {"status": trip.status, "fare_final": trip.fare_final})
        from services.trip.location_client import set_driver_online_again
        await set_driver_online_again(self._settings, trip.driver_id)
        from services.trip.identity_client import mark_ride_completed
        await mark_ride_completed(self._settings, trip.driver_id)
        return trip

    async def mark_payment_result(self, trip_id: str, succeeded: bool) -> TripRecord:
        """Called by the Payment service (services/payment/trip_client.py) after a
        charge attempt — never by a client directly. This is the only place
        COMPLETED -> PAID / PAID_PENDING happens, keeping fsm.apply() the single
        source of truth even though the trigger originates in a different service."""
        event = Event.PAYMENT_SUCCEEDED if succeeded else Event.PAYMENT_FAILED
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.apply_transition(
                trip_id, event, now=utcnow(), max_dispatch_attempts=self._settings.max_dispatch_attempts,
                actor_role="SYSTEM", actor_id=None,
            )
        await publish_trip_update(self._redis, trip_id, {"status": trip.status})
        return trip

    async def mark_payment_retry_succeeded(self, trip_id: str) -> TripRecord:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.apply_transition(
                trip_id, Event.PAYMENT_RETRY_SUCCEEDED, now=utcnow(),
                max_dispatch_attempts=self._settings.max_dispatch_attempts, actor_role="SYSTEM", actor_id=None,
            )
        await publish_trip_update(self._redis, trip_id, {"status": trip.status})
        return trip

    async def rate_trip_annotation(self, trip_id: str, rater_role: str, rater_id: str) -> TripRecord:
        """Fires the optional PAID -> RATED annotation transition (the documented I4
        exception in fsm.py). Called ONLY by the Ratings service, as an internal
        system call, AFTER it has independently verified the rater actually owns a
        side of this trip and recorded the rating row — Trip trusts that check here
        and only tracks that a rating happened, not its content. Both rider and
        driver may rate independently, but the FSM only has room for ONE PAID->RATED
        edge — the second rater's call is a deliberate idempotent no-op on the
        status, not an error."""
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.get(trip_id)
            if trip.status != "PAID":
                return trip  # already RATED (or not yet PAID) -- idempotent no-op
            trip = await repo.apply_transition(
                trip_id, Event.RATE, now=utcnow(), max_dispatch_attempts=self._settings.max_dispatch_attempts,
                actor_role=rater_role, actor_id=rater_id,
            )
        await publish_trip_update(self._redis, trip_id, {"status": trip.status})
        return trip

    async def cancel(self, trip_id: str, principal: Principal, reason: str | None) -> TripRecord:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.get(trip_id)
            repo.assert_ownership(trip, principal_id=principal.user_id, is_admin=principal.has_role(Role.ADMIN))

            rate_card = await get_rate_card(uow.session, trip.city_id)
            fee = rate_card.cancellation_fee if trip.driver_id else 0.0  # GAP AS-06: business-ambiguous, now admin-configurable per city

            event = Event.RIDER_CANCEL if principal.role == Role.RIDER else Event.SYSTEM_CANCEL
            trip = await repo.apply_transition(
                trip.trip_id, event, now=utcnow(), max_dispatch_attempts=self._settings.max_dispatch_attempts,
                field_updates={
                    "cancelled_at": utcnow(), "cancelled_by": "RIDER" if principal.role == Role.RIDER else "SYSTEM",
                    "cancellation_reason": reason, "cancellation_fee_applied": fee,
                },
                actor_role=principal.role.value, actor_id=principal.user_id,
            )
            uow.emit(
                Topic.RIDE_CANCELLED, trip.city_id,
                RideCancelled(
                    trip_id=trip.trip_id, city_id=trip.city_id,
                    cancelled_by="RIDER" if principal.role == Role.RIDER else "SYSTEM",
                    reason=reason, fee_applied=fee,
                ).model_dump(mode="json"),
            )
        await publish_trip_update(self._redis, trip.trip_id, {"status": trip.status})
        if trip.driver_id:
            from services.trip.location_client import set_driver_online_again
            await set_driver_online_again(self._settings, trip.driver_id)
        return trip

    async def driver_cancel(self, trip_id: str, driver_id: str) -> TripRecord:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.get(trip_id)
            if trip.driver_id != driver_id:
                raise ForbiddenError("not the assigned driver for this trip")
            try:
                trip = await repo.apply_transition(
                    trip_id, Event.DRIVER_CANCEL, now=utcnow(), max_dispatch_attempts=self._settings.max_dispatch_attempts,
                    field_updates={"driver_id": None}, actor_role="DRIVER", actor_id=driver_id,
                )
            except ConflictError:
                trip = await repo.apply_transition(
                    trip_id, Event.DRIVER_CANCEL_EXHAUSTED, now=utcnow(),
                    max_dispatch_attempts=self._settings.max_dispatch_attempts,
                    field_updates={"driver_id": None, "cancelled_at": utcnow(), "cancelled_by": "DRIVER"},
                    actor_role="DRIVER", actor_id=driver_id,
                )
        await publish_trip_update(self._redis, trip_id, {"status": trip.status})
        from services.trip.location_client import set_driver_online_again
        await set_driver_online_again(self._settings, driver_id)
        if trip.status == "MATCHING":
            await self.dispatch_step(trip_id)
        return trip

    async def get(self, trip_id: str, principal: Principal) -> TripRecord:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.get(trip_id)
            repo.assert_ownership(trip, principal_id=principal.user_id, is_admin=principal.has_role(Role.ADMIN))
            return trip

    async def get_current_offer(self, driver_id: str) -> TripRecord | None:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            return await repo.find_active_offer_for_driver(driver_id)

    async def list_recent(self, limit: int = 50) -> list[TripRecord]:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            return await repo.list_recent(limit)

    async def list_for_rider(self, rider_id: str, limit: int = 50) -> list[TripRecord]:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            return await repo.list_for_rider(rider_id, limit)

    async def audit_trail(self, trip_id: str, principal: Principal) -> list[dict]:
        async with self._uow_factory() as uow:
            repo = TripRepository(uow.session)
            trip = await repo.get(trip_id)
            repo.assert_ownership(trip, principal_id=principal.user_id, is_admin=principal.has_role(Role.ADMIN))
            return await repo.list_audit_trail(trip_id)
