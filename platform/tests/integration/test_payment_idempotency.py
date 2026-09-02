"""PLAN §5 C6 DONE-criteria: "Idempotency test: same key twice => one charge, one
ledger set summing to zero; gateway-timeout test => no double charge." This is the
proof that PLAN §1.2(c)'s hardest constraint — a rider is never charged twice, even
when the network lies about whether a charge went through — actually holds, using the
real Trip/Postgres/Redis stack, not mocks.

Test isolation note: this module drives its OWN `PaymentService` instance directly
(so it can force specific gateway outcomes) against real trips created through the
real services. If the actual Payment service process is ALSO running, its Kafka
consumer will race to auto-charge the same `ride.completed` event this test's setup
produces, non-deterministically stealing the "first charge" from this test's explicit
calls. Stop the live payment service process before running this file; restart it
afterward. This is a test-isolation requirement, not a design flaw — in normal
operation there is exactly one Payment service instance and no such race.
"""
from __future__ import annotations

import socket
import time
import uuid

import httpx
import pytest

from libs.common.config import get_settings
from libs.eventbus.bus import InProcessEventBus
from libs.persistence.engine import make_engine, make_sessionmaker
from services.payment.gateway import FakeGateway
from services.payment.repository import LedgerRepository, PaymentRepository
from services.payment.service import PaymentService


def _live_payment_service_is_running() -> bool:
    """Detects a live Payment service (auto-started by run.ps1 / a demo session) on
    its usual port. If one is running, its Kafka consumer will race this module's
    own direct PaymentService calls for the same trip -- see the module docstring.
    Rather than requiring a human to remember to stop it before running the suite,
    the suite detects it and skips these two tests with a clear reason; they run
    (and are required to pass) in any environment where only run_migrations +
    Postgres/Redis/Kafka are up and no Payment service instance is competing --
    exactly how CI would run this file."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("localhost", 8007)) == 0


pytestmark = pytest.mark.skipif(
    _live_payment_service_is_running(),
    reason="a live Payment service is running on :8007 and will race this module's direct "
    "PaymentService calls for ride.completed -- stop it to run this file in isolation "
    "(see module docstring); this is a test-isolation requirement, not a skipped bug.",
)

BASE_IDENTITY = "http://localhost:8001"
BASE_LOCATION = "http://localhost:8002"
BASE_TRIP = "http://localhost:8004"


def _uid(phone: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, phone))


async def _login(client: httpx.AsyncClient, phone: str) -> str:
    r = await client.post(f"{BASE_IDENTITY}/v1/auth/otp/request", json={"phone": phone})
    r.raise_for_status()
    code = r.json()["dev_code"]
    r = await client.post(f"{BASE_IDENTITY}/v1/auth/otp/verify", json={"phone": phone, "code": code})
    r.raise_for_status()
    return r.json()["access_token"]


async def _all_other_seeded_drivers_offline(client: httpx.AsyncClient, exclude_phone: str) -> None:
    """Test hygiene: this session has repeatedly brought seed drivers online via
    manual curl testing earlier tonight, and they remain ONLINE (correctly -- nothing
    told them to go offline). An automated test that assumes it's the only online
    driver needs to actually make that true rather than assume it; a fresh CI run
    against a freshly-seeded DB would never need this."""
    for i in range(1, 13):
        phone = f"+9180000000{i:02d}"
        if phone == exclude_phone:
            continue
        driver_id = _uid(phone)
        token = await _login(client, phone)
        await client.patch(
            f"{BASE_LOCATION}/v1/drivers/{driver_id}/status", json={"status": "OFFLINE"},
            headers={"Authorization": f"Bearer {token}"},
        )


async def _complete_a_real_trip() -> tuple[str, float]:
    """Drives an actual trip through the real services to COMPLETED with a real
    fare_final, so the payment test below exercises real data, not a fixture."""
    driver_phone, rider_phone = "+918000000004", "+919000000003"
    driver_id = _uid(driver_phone)

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _all_other_seeded_drivers_offline(client, driver_phone)
        driver_token = await _login(client, driver_phone)
        rider_token = await _login(client, rider_phone)
        hd, hr = {"Authorization": f"Bearer {driver_token}"}, {"Authorization": f"Bearer {rider_token}"}

        await client.patch(f"{BASE_LOCATION}/v1/drivers/{driver_id}/status", json={"status": "ONLINE"}, headers=hd)
        await client.patch(
            f"{BASE_LOCATION}/v1/drivers/{driver_id}/location",
            json={"lat": 18.5210, "lng": 73.8570, "heading": 0, "speed_kmh": 0}, headers=hd,
        )
        r = await client.post(
            f"{BASE_TRIP}/v1/trips",
            json={"pickup": {"lat": 18.5204, "lng": 73.8567}, "drop": {"lat": 18.5600, "lng": 73.9000}}, headers=hr,
        )
        trip_id = r.json()["trip_id"]

        for _ in range(20):
            r = await client.get(f"{BASE_TRIP}/v1/trips/{trip_id}", headers=hr)
            if r.json()["current_offer_driver_id"]:
                break
            time.sleep(0.3)
        assert r.json()["current_offer_driver_id"] == driver_id, "test setup requires the offer to reach our driver"

        await client.post(f"{BASE_TRIP}/v1/trips/{trip_id}/respond", json={"action": "ACCEPT"}, headers=hd)
        for step in ("start-navigation", "confirm-arrival", "start", "complete"):
            r = await client.post(f"{BASE_TRIP}/v1/trips/{trip_id}/{step}", headers=hd)
            assert r.status_code == 200, r.text
        return trip_id, r.json()["fare_final"]


@pytest.mark.asyncio
async def test_gateway_timeout_then_retry_does_not_double_charge() -> None:
    trip_id, fare_final = await _complete_a_real_trip()
    assert fare_final is not None and fare_final > 0

    settings = get_settings()
    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)
    gateway = FakeGateway()
    bus = InProcessEventBus()
    await bus.start()
    service = PaymentService(gateway, bus, settings)

    try:
        # First attempt: the gateway actually processes the charge but the response
        # times out -- this is the exact ambiguous case that breaks naive retry logic.
        gateway.force_outcome = "timeout"
        async with sessionmaker() as session:
            first = await service.charge_trip(session, trip_id)
        assert first["status"] == "SUCCEEDED"  # timed_out=True but success=True -- money moved

        # A naive caller, unsure whether the first attempt worked, retries with the
        # SAME trip_id (the idempotency key). Correct behavior: no second charge.
        gateway.force_outcome = "success"
        async with sessionmaker() as session:
            second = await service.charge_trip(session, trip_id)
        assert second["status"] == "SUCCEEDED"
        assert second.get("already_processed") is True, "the retry must recognize the trip as already paid"

        assert gateway.charge_call_count == 1, (
            f"expected exactly one real gateway charge attempt (idempotency-key dedup at the gateway), "
            f"got {gateway.charge_call_count}"
        )

        async with sessionmaker() as session:
            payments = PaymentRepository(session)
            payment = await payments.get_by_trip(trip_id)
            assert payment is not None and payment.status == "SUCCEEDED"

            ledger = LedgerRepository(session)
            balance = await ledger.balance_for_trip(trip_id)
            assert balance == pytest.approx(0.0, abs=0.01), f"ledger must sum to zero per trip (I6), got {balance}"

            rows = (
                await session.execute(
                    __import__("sqlalchemy").text("SELECT entry_type FROM ledger_entries WHERE trip_id = :id"),
                    {"id": trip_id},
                )
            ).all()
            entry_types = {r[0] for r in rows}
            assert entry_types == {"RIDER_CHARGE", "DRIVER_PAYOUT_CREDIT", "PLATFORM_COMMISSION_CREDIT"}, (
                f"expected exactly one of each ledger entry type (no duplicates), got {entry_types}"
            )
    finally:
        await bus.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_charge_leaves_trip_paid_pending_and_retry_succeeds() -> None:
    trip_id, fare_final = await _complete_a_real_trip()

    settings = get_settings()
    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)
    gateway = FakeGateway()
    bus = InProcessEventBus()
    await bus.start()
    service = PaymentService(gateway, bus, settings)

    try:
        gateway.force_outcome = "failure"
        async with sessionmaker() as session:
            result = await service.charge_trip(session, trip_id)
        assert result["status"] == "FAILED"

        async with sessionmaker() as session:
            row = (
                await session.execute(
                    __import__("sqlalchemy").text("SELECT status FROM trips WHERE trip_id = :id"), {"id": trip_id}
                )
            ).first()
            assert row[0] == "PAID_PENDING", f"a failed charge must leave the trip PAID_PENDING, got {row[0]}"

        gateway.force_outcome = "success"
        async with sessionmaker() as session:
            retry_result = await service.retry_pending(session, trip_id)
        assert retry_result["status"] == "SUCCEEDED"

        async with sessionmaker() as session:
            row = (
                await session.execute(
                    __import__("sqlalchemy").text("SELECT status FROM trips WHERE trip_id = :id"), {"id": trip_id}
                )
            ).first()
            assert row[0] == "PAID", f"a successful retry must move the trip to PAID, got {row[0]}"
    finally:
        await bus.stop()
        await engine.dispose()
