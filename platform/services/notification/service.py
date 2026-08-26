"""Notification — REAL-LITE per PLAN §2.1: a real Kafka consumer on every ride.*/
payment.* topic, writing a real persisted inbox, delivered live over the recipient's
WebSocket via the same Redis pub/sub pattern trip tracking uses (AM-07). No
APNs/FCM/SMS (PushChannel is a documented TODO — DEBT-03); WebSocketChannel and
InboxChannel are both real.
"""
from __future__ import annotations

import json
import logging

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger("notification.service")


def user_channel(user_id: str) -> str:
    return f"user:{user_id}:notifications"


TEMPLATES = {
    "ride.requested": ("Ride requested", "Looking for a nearby driver..."),
    "ride.assigned": ("Driver assigned", "Your driver is on the way."),
    # Round 10 stakeholder council: {fare_final} rendered whatever str(float) gave --
    # no currency symbol, no guaranteed 2-decimal formatting. Cosmetic on its own,
    # but worth fixing alongside ride.cancelled below.
    "ride.completed": ("Trip completed", "Your trip has ended. Fare: ₹{fare_final:.2f}"),
    "ride.cancelled": ("Trip cancelled", "Your trip was cancelled."),
    "payment.completed": ("Payment {status}", "Your payment of {amount} {status_lower}."),
}


class NotificationFanout:
    def __init__(self, sessionmaker: async_sessionmaker, redis: Redis) -> None:
        self._sessionmaker = sessionmaker
        self._redis = redis

    async def handle_event(self, topic: str, payload: dict) -> None:
        recipients = self._recipients_for(topic, payload)
        if not recipients:
            return
        title, body = self._render(topic, payload)
        for user_id in recipients:
            async with self._sessionmaker() as session:
                row = (
                    await session.execute(
                        text(
                            """
                            INSERT INTO notifications (user_id, type, title, body, payload)
                            VALUES (:user_id, :type, :title, :body, CAST(:payload AS JSONB))
                            RETURNING notification_id, created_at
                            """
                        ),
                        {"user_id": user_id, "type": topic, "title": title, "body": body, "payload": json.dumps(payload, default=str)},
                    )
                ).first()
                await session.commit()
            await self._redis.publish(
                user_channel(user_id),
                json.dumps({"notification_id": str(row[0]), "type": topic, "title": title, "body": body}, default=str),
            )

    def _recipients_for(self, topic: str, payload: dict) -> list[str]:
        if topic in ("ride.requested", "ride.completed", "ride.cancelled"):
            ids = [payload.get("rider_id")]
            if payload.get("driver_id"):
                ids.append(payload["driver_id"])
            return [i for i in ids if i]
        if topic == "ride.assigned":
            return [payload["driver_id"]] if payload.get("driver_id") else []
        if topic == "payment.completed":
            return []  # payment.completed carries trip_id, not rider_id -- resolved via ride.completed's own notification instead
        return []

    def _render(self, topic: str, payload: dict) -> tuple[str, str]:
        if topic == "ride.cancelled":
            # Round 10 stakeholder council: this notification -- sent to both rider
            # and driver (see _recipients_for) -- said "cancelled" and nothing else,
            # even after Round 9 made cancellation fees a real charge/payout. One
            # neutral wording works for both sides: a rider reads it as what they
            # were charged, a driver reads it as what they were compensated.
            fee = payload.get("fee_applied") or 0
            if fee > 0:
                return "Trip cancelled", f"Your trip was cancelled. A ₹{fee:.2f} cancellation fee applies."
            return "Trip cancelled", "Your trip was cancelled."

        title_tpl, body_tpl = TEMPLATES.get(topic, (topic, "{}"))
        try:
            ctx = {**payload, "status_lower": str(payload.get("status", "")).lower()}
            return title_tpl.format(**ctx), body_tpl.format(**ctx)
        except (KeyError, IndexError):
            return title_tpl, str(payload)
