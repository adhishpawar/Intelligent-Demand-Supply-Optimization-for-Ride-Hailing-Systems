"""The topic registry (PLAN §3.2: "shared contracts library" — one place that defines
every Kafka payload shape, so producers and consumers cannot silently drift). Mirrors
the topic catalog in ride_hailing_HLD_LLD.md section 6.1 / part2.md section 6.1.

`publish()` in every service goes through `Topic` — an unregistered topic string is a
programming error caught at call time, not a silent typo in production.
"""
from __future__ import annotations

from enum import Enum


class Topic(str, Enum):
    RIDE_REQUESTED = "ride.requested"
    RIDE_ASSIGNED = "ride.assigned"
    RIDE_COMPLETED = "ride.completed"
    RIDE_CANCELLED = "ride.cancelled"
    DRIVER_LOCATION = "driver.location"
    DRIVER_STATUS = "driver.status"
    PAYMENT_COMPLETED = "payment.completed"
    NOTIFICATION_DISPATCH = "notification.dispatch"


# partition key field per topic — mirrors the HLD's "Key" column (city_id/driver_id/trip_id)
PARTITION_KEY_FIELD: dict[Topic, str] = {
    Topic.RIDE_REQUESTED: "city_id",
    Topic.RIDE_ASSIGNED: "city_id",
    Topic.RIDE_COMPLETED: "city_id",
    Topic.RIDE_CANCELLED: "city_id",
    Topic.DRIVER_LOCATION: "driver_id",
    Topic.DRIVER_STATUS: "driver_id",
    Topic.PAYMENT_COMPLETED: "trip_id",
    Topic.NOTIFICATION_DISPATCH: "user_id",
}
