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
from libs.geo.city_config_repo import get_rate_card
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
                # Round 9 stakeholder council (tech lead, financial correctness): this
                # read `get_city(...).rate_card.commission_pct` -- the hardcoded
                # libs.geo.cities default -- completely bypassing the DB-backed,
                # admin-editable city_configs table Round 1 built specifically so
                # commission_pct (and every other rate-card field) takes effect
                # without a deploy. Every other reader (Trip's fare estimate/final,
                # Pricing's /v1/estimate) already went through get_rate_card; this was
                # the one silent holdout -- an admin changing a city's commission in
                # the Round 1 panel had zero effect on what was actually charged.
                rate_card = await get_rate_card(session, trip["city_id"])
                commission = rate_card.commission_pct
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

    async def charge_cancellation_fee(self, session, trip_id: str) -> dict:
        """Round 9 stakeholder council (tech lead, financial correctness): a rider
        cancelling after a driver was already assigned has always shown "a
        cancellation fee applies" (Round 1's cancel-confirmation UI) and the trip row
        has always recorded a real, non-zero `cancellation_fee_applied` -- but
        `CANCELLATION_FEE_CHARGE`/`CANCELLATION_FEE_PAYOUT_CREDIT` (both defined in
        the ledger_entries CHECK constraint since V001) were never actually posted by
        anything, anywhere. Payment never even subscribed to `ride.cancelled`. The
        rider was never really charged; the driver was never really compensated for
        the wasted trip to pickup.

        Deliberately ledger-only, not routed through the gateway the way
        `charge_trip` is: modelling a genuine card-charge-with-its-own-retry-
        semantics for this would need a parallel payments-style table (the current
        one is keyed 1:1 on trip_id for the ride fare specifically) -- a
        proportionally larger change than a demo-scale fixed fee justifies tonight.
        No platform cut (unlike the ride fare): the fee compensates the driver's
        wasted trip, so `CANCELLATION_FEE_PAYOUT_CREDIT` is the driver's in full --
        the two entries balance to zero on their own, matching every other trip's
        ledger invariant."""
        ledger = LedgerRepository(session)
        if await ledger.has_entries(trip_id):
            return {"status": "SKIPPED", "reason": "already_posted"}

        trip = await get_trip(self._settings, trip_id)
        fee = trip.get("cancellation_fee_applied") or 0.0
        if fee <= 0 or not trip.get("driver_id"):
            return {"status": "SKIPPED", "reason": "no_fee_or_no_driver"}

        await ledger.post_entry(trip_id, "CANCELLATION_FEE_CHARGE", "RIDER", trip["rider_id"], -fee)
        await ledger.post_entry(trip_id, "CANCELLATION_FEE_PAYOUT_CREDIT", "DRIVER", trip["driver_id"], fee)
        await ledger.commit()
        return {"status": "POSTED", "fee": fee}
