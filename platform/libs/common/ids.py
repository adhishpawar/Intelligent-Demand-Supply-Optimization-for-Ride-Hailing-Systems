"""Central id / clock helpers.

Every id in this system is a UUID4 string. Every timestamp is UTC, generated through
`utcnow()` so that tests can monkeypatch a single function to control time instead of
patching `datetime` everywhere.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id() -> str:
    """A new random identifier. Used for trip_id, event_id, driver_id, etc."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """The single clock the entire platform reads from.

    Deliberately a function, not `datetime.utcnow()` scattered everywhere, so that
    (a) it is always timezone-aware (naive datetimes are a recurring source of subtle
    off-by-some-hours bugs when they meet Postgres `timestamptz`), and (b) tests can
    monkeypatch `libs.common.ids.utcnow` to freeze time for state-machine timeout tests.
    """
    return datetime.now(timezone.utc)
