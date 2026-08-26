"""Event envelope schemas. Every envelope carries `event_id` + `schema_version`
(idempotency / future-proofing) and, per PLAN amendment AM-17, `city_id` and
`geohash7` on every geo-bearing event so the future ML decision layer
(part1.md/part2.md) never has to re-derive geography from raw coordinates and the two
systems cannot disagree about which zone an event belongs to.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from libs.common.ids import new_id, utcnow


class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=new_id)
    schema_version: int = 1
    ts: datetime = Field(default_factory=utcnow)


class RideRequested(EventEnvelope):
    trip_id: str
    rider_id: str
    city_id: str
    pickup_geohash7: str
    pickup_lat: float
    pickup_lng: float
    drop_lat: float
    drop_lng: float
    fare_estimate: float
    surge_multiplier: float


class RideAssigned(EventEnvelope):
    trip_id: str
    driver_id: str
    # Round 11 stakeholder council: rider_id was missing here too (same class of
    # gap Round 10 found on RideCancelled) -- NotificationFanout._recipients_for
    # could only ever notify the driver, never the rider, about their own
    # assignment. The rider does get a live status toast (Round 2), but nothing
    # persisted in their notification history if they were away when it happened.
    rider_id: str
    city_id: str
    time_to_match_seconds: float
    dispatch_attempts: int


class RideCompleted(EventEnvelope):
    trip_id: str
    rider_id: str
    driver_id: str
    city_id: str
    fare_final: float
    distance_m: float
    duration_s: float


class RideCancelled(EventEnvelope):
    trip_id: str
    city_id: str
    # Round 10 stakeholder council: rider_id/driver_id were missing from this event
    # entirely -- services/notification's _recipients_for reads exactly these two
    # fields off the payload to decide who to notify, via payload.get(...) (so no
    # KeyError, just a silently empty recipient list every time). The result: zero
    # ride.cancelled notifications were ever created, for any cancellation, all
    # night -- not a wiring gap this time, a schema/consumer mismatch that never
    # threw, just quietly did nothing.
    rider_id: str
    driver_id: str | None = None
    cancelled_by: Literal["RIDER", "DRIVER", "SYSTEM"]
    reason: str | None = None
    fee_applied: float = 0.0


class DriverLocation(EventEnvelope):
    driver_id: str
    lat: float
    lng: float
    heading: float | None = None
    speed_kmh: float | None = None
    status: str
    city_id: str | None = None
    geohash7: str


class DriverStatus(EventEnvelope):
    driver_id: str
    status: Literal["OFFLINE", "ONLINE", "ON_TRIP"]
    city_id: str | None = None


class PaymentCompleted(EventEnvelope):
    trip_id: str
    payment_id: str
    status: Literal["SUCCEEDED", "FAILED", "PAID_PENDING_RETRY"]
    amount: float


class NotificationDispatch(EventEnvelope):
    user_id: str
    type: str
    title: str
    body: str
    payload: dict = Field(default_factory=dict)
