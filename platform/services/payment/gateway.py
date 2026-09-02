"""PaymentGateway port (PLAN §3.2 Adapter pattern). `FakeGateway` is deliberately
built with *realistic failure modes*, not just a happy-path stub — PLAN §3.2:
"the fakes are how we demo tonight; the ports are how we go live later... the fake
must honour the contract including its failure modes." Specifically the
"charged-but-timed-out" mode exists for exactly one reason: to prove idempotency is
real. A gateway that always succeeds or always fails cleanly could never expose a
double-charge bug; this one can, on command, and the test suite uses it to prove the
bug doesn't happen.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol

from libs.common.ids import new_id


@dataclass(frozen=True)
class ChargeResult:
    success: bool
    gateway_ref: str | None
    timed_out: bool  # charge MAY have gone through on the gateway's side; we don't know
    failure_reason: str | None = None


class PaymentGateway(Protocol):
    async def charge(self, *, idempotency_key: str, amount: float) -> ChargeResult: ...
    async def refund(self, *, idempotency_key: str, amount: float) -> ChargeResult: ...


class FakeGateway:
    """Configurable failure injection for testing the retry/idempotency paths for
    real, not by inspection. `force_outcome` (set per-call in tests) takes priority
    over the random failure rate."""

    def __init__(self, failure_rate: float = 0.0, timeout_rate: float = 0.0) -> None:
        self._failure_rate = failure_rate
        self._timeout_rate = timeout_rate
        self.force_outcome: str | None = None  # "success" | "failure" | "timeout"
        self.charge_call_count = 0
        # Real payment gateways (Stripe et al.) dedupe on the idempotency key
        # THEMSELVES — a second charge() call with a key already seen returns the
        # ORIGINAL result rather than processing a new charge. Modeling that here
        # means a test can prove idempotency holds even if the caller's own
        # bookkeeping were naive, not only when it's careful.
        self._seen: dict[str, ChargeResult] = {}

    async def charge(self, *, idempotency_key: str, amount: float) -> ChargeResult:
        if idempotency_key in self._seen:
            return self._seen[idempotency_key]
        self.charge_call_count += 1
        outcome = self.force_outcome or self._roll()
        if outcome == "timeout":
            # The defining case: the gateway actually processed the charge (money
            # moved on the real rails) but the response never reached us. A caller
            # that treats "no response" as "definitely failed" and blindly retries
            # would double-charge the rider — this is the scenario idempotency
            # (trip_id as the key, checked before any retry) exists to prevent.
            result = ChargeResult(success=True, gateway_ref=f"gw_{new_id()}", timed_out=True)
        elif outcome == "failure":
            result = ChargeResult(success=False, gateway_ref=None, timed_out=False, failure_reason="card_declined")
        else:
            result = ChargeResult(success=True, gateway_ref=f"gw_{new_id()}", timed_out=False)
        if result.success:
            # Only a SUCCESSFUL (or ambiguous-but-money-moved, i.e. timed_out) charge
            # is dedup-worthy — that's the case where retrying for real would double-
            # charge. A card-declined failure moved no money, so it must NOT be
            # cached: a retry with the same idempotency key is exactly the legitimate
            # "try again" flow the failure/retry path exists for, not a double-charge
            # risk. Caching failures too was a real bug caught by this test suite.
            self._seen[idempotency_key] = result
        return result

    async def refund(self, *, idempotency_key: str, amount: float) -> ChargeResult:
        return ChargeResult(success=True, gateway_ref=f"gw_refund_{new_id()}", timed_out=False)

    def _roll(self) -> str:
        r = random.random()
        if r < self._timeout_rate:
            return "timeout"
        if r < self._timeout_rate + self._failure_rate:
            return "failure"
        return "success"
