"""PLAN §3.3: I1-I4 proven by walking `TRANSITIONS` itself, so any future edit to the
table is automatically re-checked — this is what the Chairman synthesis (PLAN §8,
decision 2) means by "the proof survives future edits by someone who has not read
this document."
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from services.trip.fsm import TERMINAL_STATES, TRANSITIONS, Event, State, TransitionContext, apply


def _reachable_from(start: State) -> set[State]:
    seen = {start}
    queue = deque([start])
    while queue:
        s = queue.popleft()
        for (from_state, _event), transition in TRANSITIONS.items():
            if from_state == s and transition.to not in seen:
                seen.add(transition.to)
                queue.append(transition.to)
    return seen


def test_i1_every_state_reachable_from_requested() -> None:
    reachable = _reachable_from(State.REQUESTED)
    all_states = set(State)
    unreachable = all_states - reachable
    assert not unreachable, f"unreachable states from REQUESTED: {unreachable}"


def test_i2_every_non_terminal_state_has_an_outgoing_transition() -> None:
    non_terminal = set(State) - TERMINAL_STATES
    for state in non_terminal:
        outgoing = [k for k in TRANSITIONS if k[0] == state]
        assert outgoing, f"non-terminal state {state} has no outgoing transitions (dead end)"


def test_i3_no_state_event_pair_is_ambiguous() -> None:
    # TRANSITIONS is a dict keyed by (state, event) -- Python dict semantics already
    # make a duplicate key impossible to express, but this test protects against a
    # future refactor to a list-of-tuples losing that guarantee silently.
    keys = list(TRANSITIONS.keys())
    assert len(keys) == len(set(keys)), "duplicate (state, event) transition detected"


def test_i4_terminal_states_have_no_outgoing_transitions_except_the_documented_paid_exception() -> None:
    for state in TERMINAL_STATES:
        outgoing = [k for k in TRANSITIONS if k[0] == state]
        assert outgoing == [], f"terminal state {state} has outgoing transitions: {outgoing}"

    # PAID is NOT in TERMINAL_STATES (it has exactly one outgoing edge) -- assert that
    # exception is exactly what's documented: one edge, RATE -> RATED, nothing else.
    paid_outgoing = [k for k in TRANSITIONS if k[0] == State.PAID]
    assert paid_outgoing == [(State.PAID, Event.RATE)], (
        f"PAID's outgoing transitions changed unexpectedly: {paid_outgoing} "
        "-- if this is intentional, update the I4 exception documentation in fsm.py"
    )
    assert TRANSITIONS[(State.PAID, Event.RATE)].to == State.RATED


def test_terminal_states_match_expected_set() -> None:
    expected = {
        State.NO_DRIVER_FOUND,
        State.EXPIRED,
        State.CANCELLED_BY_RIDER,
        State.CANCELLED_BY_DRIVER,
        State.CANCELLED_BY_SYSTEM,
        State.RATED,
    }
    assert TERMINAL_STATES == expected


def _ctx(**overrides) -> TransitionContext:
    base = dict(
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        dispatch_attempts=0,
        max_dispatch_attempts=3,
        current_offer_driver_id=None,
        current_offer_expires_at=None,
    )
    base.update(overrides)
    return TransitionContext(**base)


def test_accept_requires_matching_driver_and_unexpired_offer() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    ok_ctx = _ctx(
        now=now,
        current_offer_driver_id="driver-1",
        current_offer_expires_at=datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc),
        responding_driver_id="driver-1",
    )
    assert apply(State.MATCHING, Event.ACCEPT, ok_ctx) == State.DRIVER_ASSIGNED

    wrong_driver_ctx = _ctx(
        now=now,
        current_offer_driver_id="driver-1",
        current_offer_expires_at=datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc),
        responding_driver_id="driver-2",
    )
    import pytest

    from libs.common.errors import IllegalTransitionError

    with pytest.raises(IllegalTransitionError):
        apply(State.MATCHING, Event.ACCEPT, wrong_driver_ctx)

    expired_ctx = _ctx(
        now=now,
        current_offer_driver_id="driver-1",
        current_offer_expires_at=datetime(2026, 1, 1, 11, 59, 0, tzinfo=timezone.utc),
        responding_driver_id="driver-1",
    )
    with pytest.raises(IllegalTransitionError):
        apply(State.MATCHING, Event.ACCEPT, expired_ctx)


def test_dispatch_attempts_gate_redispatch_vs_exhaust() -> None:
    import pytest

    from libs.common.errors import IllegalTransitionError

    remain = _ctx(dispatch_attempts=1, max_dispatch_attempts=3)
    assert apply(State.MATCHING, Event.REDISPATCH, remain) == State.MATCHING
    with pytest.raises(IllegalTransitionError):
        apply(State.MATCHING, Event.EXHAUST_DISPATCH, remain)

    exhausted = _ctx(dispatch_attempts=3, max_dispatch_attempts=3)
    assert apply(State.MATCHING, Event.EXHAUST_DISPATCH, exhausted) == State.NO_DRIVER_FOUND
    with pytest.raises(IllegalTransitionError):
        apply(State.MATCHING, Event.REDISPATCH, exhausted)


def test_driver_cancel_reassigns_or_exhausts_deterministically() -> None:
    import pytest

    from libs.common.errors import IllegalTransitionError

    remain = _ctx(dispatch_attempts=1, max_dispatch_attempts=3)
    assert apply(State.DRIVER_ASSIGNED, Event.DRIVER_CANCEL, remain) == State.MATCHING
    with pytest.raises(IllegalTransitionError):
        apply(State.DRIVER_ASSIGNED, Event.DRIVER_CANCEL_EXHAUSTED, remain)

    exhausted = _ctx(dispatch_attempts=3, max_dispatch_attempts=3)
    assert apply(State.DRIVER_ASSIGNED, Event.DRIVER_CANCEL_EXHAUSTED, exhausted) == State.CANCELLED_BY_DRIVER


def test_illegal_transition_raises() -> None:
    import pytest

    from libs.common.errors import IllegalTransitionError

    with pytest.raises(IllegalTransitionError):
        apply(State.COMPLETED, Event.START_MATCHING, _ctx())

    with pytest.raises(IllegalTransitionError):
        apply(State.RATED, Event.RATE, _ctx())
