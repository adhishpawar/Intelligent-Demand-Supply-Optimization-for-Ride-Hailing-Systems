"""Payment orchestration. `charge_trip` is the one method that matters for the
idempotency story (PLAN §3.3 mechanism 3): it checks OUR OWN recorded state
(`payments.status`) before ever touching the gateway again, so a redelivered
`ride.completed` event, a retried HTTP call, or a gateway timeout followed by a naive
retry all converge on "charge exactly once" rather than "trust the gateway to dedupe."
"""
from __future__ import annotations

import logging

from libs.common.config import Settings
from libs.contracts.events import PaymentCompleted
from libs.contracts.topics import Topic
from libs.eventbus.bus import EventBus
from libs.geo.cities import get_city
from services.payment.gateway import PaymentGateway
from services.payment.repository import LedgerRepository, PaymentRepository
from services.payment.trip_client import get_trip, report_payment_result

logger = logging.getLogger("payment.service")

MAX_RETRY_ATTEMPTS = 3


class PaymentService:
    def __init__(self, gateway: PaymentGateway, event_bus: EventBus, settings: Settings) -> None:
        self._gateway = gateway
        self._bus = event_bus
        self._settings = settings

    async def charge_trip(self, session, trip_id: str) -> dict:
        payments = PaymentRepository(session)
        ledger = LedgerRepository(session)

        existing = await payments.get_by_trip(trip_id)
        if existing and existing.status == "SUCCEEDED":
            return {"status": "SUCCEEDED", "already_processed": True}
        if existing and existing.attempt_count >= MAX_RETRY_ATTEMPTS:
            return {"status": "FAILED", "reason": "max_retry_attempts_exceeded"}
        is_retry = existing is not None and existing.status == "FAILED"

        trip = await get_trip(self._settings, trip_id)
        amount = trip["fare_final"]
        if amount is None:
            raise ValueError(f"trip {trip_id} has no fare_final yet — cannot charge")

        await payments.create_pending(trip_id, amount)
        result = await self._gateway.charge(idempotency_key=trip_id, amount=amount)

        if result.success:
            if not await ledger.has_entries(trip_id):
                city = get_city(trip["city_id"])
                commission = city.rate_card.commission_pct
                driver_payout = round(amount * (1 - commission), 2)
                platform_cut = round(amount - driver_payout, 2)  # ensures exact sum-to-zero, no rounding drift
                await ledger.post_entry(trip_id, "RIDER_CHARGE", "RIDER", trip["rider_id"], -amount)
                await ledger.post_entry(trip_id, "DRIVER_PAYOUT_CREDIT", "DRIVER", trip["driver_id"], driver_payout)
                await ledger.post_entry(trip_id, "PLATFORM_COMMISSION_CREDIT", "PLATFORM", None, platform_cut)
                await ledger.commit()

            await payments.mark_result(trip_id, status="SUCCEEDED", gateway_ref=result.gateway_ref, failure_reason=None)
            # Which FSM event applies depends on whether the trip's status is still
            # COMPLETED (first attempt) or already PAID_PENDING (a retry) — those are
            # two different transitions in fsm.py (PAYMENT_SUCCEEDED vs
            # PAYMENT_RETRY_SUCCEEDED), so the caller's history determines the verb.
            await report_payment_result(
                self._settings, trip_id, "PAID_PENDING_RETRY_SUCCEEDED" if is_retry else "SUCCEEDED"
            )
            await self._bus.publish(
                Topic.PAYMENT_COMPLETED.value, trip_id,
                PaymentCompleted(trip_id=trip_id, payment_id=trip_id, status="SUCCEEDED", amount=amount).model_dump(mode="json"),
            )
            return {"status": "SUCCEEDED", "gateway_ref": result.gateway_ref}

        await payments.mark_result(trip_id, status="FAILED", gateway_ref=None, failure_reason=result.failure_reason)
        await report_payment_result(self._settings, trip_id, "FAILED")
        await self._bus.publish(
            Topic.PAYMENT_COMPLETED.value, trip_id,
            PaymentCompleted(trip_id=trip_id, payment_id=trip_id, status="FAILED", amount=amount).model_dump(mode="json"),
        )
        return {"status": "FAILED", "reason": result.failure_reason}

    async def retry_pending(self, session, trip_id: str) -> dict:
        return await self.charge_trip(session, trip_id)
