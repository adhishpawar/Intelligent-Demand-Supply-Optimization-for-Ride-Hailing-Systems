"""The ride state machine (PLAN §3.2 "Table-driven State Machine", §3.3 invariants
I1-I4). This module is pure — no I/O, no config coupling beyond what's passed into
`TransitionContext` — precisely so `tests/unit/test_fsm_invariants.py` can prove I1-I4
by walking `TRANSITIONS` itself, and so any future edit to the table is automatically
re-checked by that test rather than trusted by eye.

Design notes on the two departures from the HLD's literal chain (both driven by the
PLAN §6.2 adversarial review, finding B3):

1. `CANCELLED_BY_DRIVER` is NOT what a driver cancellation always produces. A driver
   cancelling after accepting returns the trip to MATCHING for re-dispatch (the rider
   still wants a ride) UNLESS this cancellation is what exhausts
   `max_dispatch_attempts` - in that case the trip terminates as CANCELLED_BY_DRIVER,
   distinct from NO_DRIVER_FOUND (a supply problem - no candidates were ever
   available) and from EXPIRED (a latency-budget problem - the 90s matching deadline
   was hit). Three distinct terminal failure modes, three distinct ops-analytics
   signals, not one overloaded "cancelled" bucket.
2. `PAID` is a documented, deliberate exception to I4 ("terminal states have no
   outgoing transitions"): rating is optional, so most trips end at PAID and never
   reach RATED. PAID is treated as *closed* (driver freed, ledger settled, the trip is
   "done" for every operational purpose) but keeps exactly one legal annotation edge
   to RATED. `tests/unit/test_fsm_invariants.py` computes the strict I4 terminal set
   as "zero outgoing transitions" (which correctly excludes PAID) and separately
   asserts PAID's only edge is the RATE annotation - the exception is proven, not
   assumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable

from libs.common.errors import IllegalTransitionError


class State(str, Enum):
    REQUESTED = "REQUESTED"
    MATCHING = "MATCHING"
    DRIVER_ASSIGNED = "DRIVER_ASSIGNED"
    DRIVER_ARRIVING = "DRIVER_ARRIVING"
    DRIVER_ARRIVED = "DRIVER_ARRIVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    PAID_PENDING = "PAID_PENDING"
    PAID = "PAID"
    RATED = "RATED"
    NO_DRIVER_FOUND = "NO_DRIVER_FOUND"
    CANCELLED_BY_RIDER = "CANCELLED_BY_RIDER"
    CANCELLED_BY_DRIVER = "CANCELLED_BY_DRIVER"
    CANCELLED_BY_SYSTEM = "CANCELLED_BY_SYSTEM"
    EXPIRED = "EXPIRED"


class Event(str, Enum):
    START_MATCHING = "START_MATCHING"
    ACCEPT = "ACCEPT"
    REDISPATCH = "REDISPATCH"                        # offer rejected/timed out, attempts remain
    EXHAUST_DISPATCH = "EXHAUST_DISPATCH"             # attempts exhausted, no driver ever accepted
    EXPIRE = "EXPIRE"                                 # overall matching deadline breached
    START_NAVIGATION = "START_NAVIGATION"
    CONFIRM_ARRIVAL = "CONFIRM_ARRIVAL"
    START_TRIP = "START_TRIP"
    COMPLETE_TRIP = "COMPLETE_TRIP"
    PAYMENT_SUCCEEDED = "PAYMENT_SUCCEEDED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_RETRY_SUCCEEDED = "PAYMENT_RETRY_SUCCEEDED"
    RATE = "RATE"
    RIDER_CANCEL = "RIDER_CANCEL"
    DRIVER_CANCEL = "DRIVER_CANCEL"                   # attempts remain -> back to MATCHING
    DRIVER_CANCEL_EXHAUSTED = "DRIVER_CANCEL_EXHAUSTED"  # this cancellation exhausts attempts
    SYSTEM_CANCEL = "SYSTEM_CANCEL"


@dataclass
class TransitionContext:
    """Everything a guard might need, injected rather than imported, so fsm.py stays
    pure and every guard is a plain function of (trip_snapshot, ctx)."""

    now: datetime
    dispatch_attempts: int
    max_dispatch_attempts: int
    current_offer_driver_id: str | None
    current_offer_expires_at: datetime | None
    responding_driver_id: str | None = None
    matching_deadline_at: datetime | None = None


Guard = Callable[[TransitionContext], bool]


def _always(ctx: TransitionContext) -> bool:
    return True


def _accept_guard(ctx: TransitionContext) -> bool:
    return (
        ctx.current_offer_driver_id is not None
        and ctx.responding_driver_id == ctx.current_offer_driver_id
        and ctx.current_offer_expires_at is not None
        and ctx.now <= ctx.current_offer_expires_at
    )


def _attempts_remain(ctx: TransitionContext) -> bool:
    return ctx.dispatch_attempts < ctx.max_dispatch_attempts


def _attempts_exhausted(ctx: TransitionContext) -> bool:
    return ctx.dispatch_attempts >= ctx.max_dispatch_attempts


@dataclass(frozen=True)
class Transition:
    to: State
    guard: Guard = _always


# The table. (State, Event) -> Transition. This IS the state machine — `apply()`
# below is a five-line lookup, deliberately, so the graph lives in one place a
# reviewer (or a test) can read start to finish.
TRANSITIONS: dict[tuple[State, Event], Transition] = {
    (State.REQUESTED, Event.START_MATCHING): Transition(State.MATCHING),
    (State.REQUESTED, Event.RIDER_CANCEL): Transition(State.CANCELLED_BY_RIDER),

    (State.MATCHING, Event.ACCEPT): Transition(State.DRIVER_ASSIGNED, _accept_guard),
    (State.MATCHING, Event.REDISPATCH): Transition(State.MATCHING, _attempts_remain),
    (State.MATCHING, Event.EXHAUST_DISPATCH): Transition(State.NO_DRIVER_FOUND, _attempts_exhausted),
    (State.MATCHING, Event.EXPIRE): Transition(State.EXPIRED),
    (State.MATCHING, Event.RIDER_CANCEL): Transition(State.CANCELLED_BY_RIDER),

    (State.DRIVER_ASSIGNED, Event.START_NAVIGATION): Transition(State.DRIVER_ARRIVING),
    (State.DRIVER_ASSIGNED, Event.DRIVER_CANCEL): Transition(State.MATCHING, _attempts_remain),
    (State.DRIVER_ASSIGNED, Event.DRIVER_CANCEL_EXHAUSTED): Transition(State.CANCELLED_BY_DRIVER, _attempts_exhausted),
    (State.DRIVER_ASSIGNED, Event.RIDER_CANCEL): Transition(State.CANCELLED_BY_RIDER),

    (State.DRIVER_ARRIVING, Event.CONFIRM_ARRIVAL): Transition(State.DRIVER_ARRIVED),
    (State.DRIVER_ARRIVING, Event.DRIVER_CANCEL): Transition(State.MATCHING, _attempts_remain),
    (State.DRIVER_ARRIVING, Event.DRIVER_CANCEL_EXHAUSTED): Transition(State.CANCELLED_BY_DRIVER, _attempts_exhausted),
    (State.DRIVER_ARRIVING, Event.RIDER_CANCEL): Transition(State.CANCELLED_BY_RIDER),

    (State.DRIVER_ARRIVED, Event.START_TRIP): Transition(State.IN_PROGRESS),
    (State.DRIVER_ARRIVED, Event.RIDER_CANCEL): Transition(State.CANCELLED_BY_RIDER),

    (State.IN_PROGRESS, Event.COMPLETE_TRIP): Transition(State.COMPLETED),
    (State.IN_PROGRESS, Event.SYSTEM_CANCEL): Transition(State.CANCELLED_BY_SYSTEM),

    (State.COMPLETED, Event.PAYMENT_SUCCEEDED): Transition(State.PAID),
    (State.COMPLETED, Event.PAYMENT_FAILED): Transition(State.PAID_PENDING),

    (State.PAID_PENDING, Event.PAYMENT_RETRY_SUCCEEDED): Transition(State.PAID),

    (State.PAID, Event.RATE): Transition(State.RATED),  # the documented I4 exception
}

TERMINAL_STATES: frozenset[State] = frozenset(
    s for s in State if not any(k[0] == s for k in TRANSITIONS)
)


def apply(current: State, event: Event, ctx: TransitionContext) -> State:
    """The entire state machine. Raises IllegalTransitionError (409) if there is no
    transition for (current, event) or its guard fails — per PLAN §1.2(d), a rejected
    transition almost always means the caller's view of the trip is stale."""
    key = (current, event)
    transition = TRANSITIONS.get(key)
    if transition is None:
        raise IllegalTransitionError(
            f"no transition for state={current.value} event={event.value}",
            state=current.value,
            event=event.value,
        )
    if not transition.guard(ctx):
        raise IllegalTransitionError(
            f"transition guard failed for state={current.value} event={event.value}",
            state=current.value,
            event=event.value,
        )
    return transition.to
