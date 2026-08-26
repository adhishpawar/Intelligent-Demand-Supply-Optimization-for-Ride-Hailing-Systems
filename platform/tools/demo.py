"""The scripted end-to-end smoke run (PLAN §5, C9 DONE-criteria: "drives a complete
trip headlessly and prints the audit trail"). This is the release gate: if this
script doesn't pass clean, the system isn't demoable, regardless of what the unit
test suite says. Requires every service to be running (see run.ps1).

Drives: rider login -> driver login -> driver online -> rider requests a ride ->
real dispatch match -> driver accepts -> full lifecycle -> automatic payment (via the
live Kafka consumer, not a manual trigger) -> rider rates the driver -> prints the
complete, real, timestamped FSM audit trail and the double-entry ledger, asserting
the ledger balances to zero.

Usage:
    python -m tools.demo
"""
from __future__ import annotations

import sys
import time
import uuid

import httpx

BASE = {
    "identity": "http://localhost:8001",
    "location": "http://localhost:8002",
    "trip": "http://localhost:8004",
    "payment": "http://localhost:8007",
    "notification": "http://localhost:8008",
}

RIDER_PHONE = "+919000000005"
DRIVER_PHONE = "+918000000012"


def uid(phone: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, phone))


def login(client: httpx.Client, phone: str) -> str:
    r = client.post(f"{BASE['identity']}/v1/auth/otp/request", json={"phone": phone})
    r.raise_for_status()
    code = r.json()["dev_code"]
    r = client.post(f"{BASE['identity']}/v1/auth/otp/verify", json={"phone": phone, "code": code})
    r.raise_for_status()
    return r.json()["access_token"]


def step(label: str) -> None:
    print(f"\n=== {label} ===")


def all_other_drivers_offline(client: httpx.Client, keep_phone: str) -> None:
    """Test hygiene: earlier manual/automated runs may have left other seed drivers
    ONLINE. A demo run should be deterministic about which driver gets the offer."""
    for i in range(1, 13):
        phone = f"+9180000000{i:02d}"
        if phone == keep_phone:
            continue
        driver_id = uid(phone)
        token = login(client, phone)
        client.patch(
            f"{BASE['location']}/v1/drivers/{driver_id}/status", json={"status": "OFFLINE"},
            headers={"Authorization": f"Bearer {token}"},
        )


def main() -> int:
    with httpx.Client(timeout=15.0) as client:
        step("Setup")
        all_other_drivers_offline(client, DRIVER_PHONE)
        driver_id = uid(DRIVER_PHONE)
        driver_token = login(client, DRIVER_PHONE)
        rider_token = login(client, RIDER_PHONE)
        hd, hr = {"Authorization": f"Bearer {driver_token}"}, {"Authorization": f"Bearer {rider_token}"}
        print(f"driver_id = {driver_id}")

        step("Driver goes online and pings location")
        r = client.patch(f"{BASE['location']}/v1/drivers/{driver_id}/status", json={"status": "ONLINE"}, headers=hd)
        assert r.status_code == 200, r.text
        r = client.patch(
            f"{BASE['location']}/v1/drivers/{driver_id}/location",
            json={"lat": 18.5210, "lng": 73.8570, "heading": 0, "speed_kmh": 0}, headers=hd,
        )
        assert r.status_code == 200 and r.json()["accepted"], r.text

        step("Rider requests a ride")
        r = client.post(
            f"{BASE['trip']}/v1/trips",
            # vehicle_type: AUTO -- Round 7 stakeholder council added vehicle-type
            # filtering to dispatch; DRIVER_PHONE (seed driver #12, "Lata Driver") is
            # seeded as AUTO (tools/seed.py's DRIVER_SPECS), so the request must
            # actually ask for that type or the (now real) filter would correctly
            # exclude this driver and the demo would go to NO_DRIVER_FOUND.
            json={
                "pickup": {"lat": 18.5204, "lng": 73.8567}, "drop": {"lat": 18.5600, "lng": 73.9000},
                "vehicle_type": "AUTO",
            },
            headers=hr,
        )
        assert r.status_code == 200, r.text
        trip = r.json()
        trip_id = trip["trip_id"]
        print(f"trip_id = {trip_id}  fare_estimate = Rs.{trip['fare_estimate']}")

        step("Waiting for real dispatch to offer our driver")
        for _ in range(20):
            r = client.get(f"{BASE['trip']}/v1/trips/{trip_id}", headers=hr)
            trip = r.json()
            if trip["current_offer_driver_id"]:
                break
            time.sleep(0.5)
        assert trip["current_offer_driver_id"] == driver_id, (
            f"expected the offer to reach our driver, got {trip['current_offer_driver_id']} "
            f"(status={trip['status']}, attempts={trip['dispatch_attempts']})"
        )
        print(f"offer reached driver after {trip['dispatch_attempts']} attempt(s)")

        step("Driver accepts and drives the full lifecycle")
        r = client.post(f"{BASE['trip']}/v1/trips/{trip_id}/respond", json={"action": "ACCEPT"}, headers=hd)
        assert r.status_code == 200 and r.json()["status"] == "DRIVER_ASSIGNED", r.text
        for endpoint, expected in [
            ("start-navigation", "DRIVER_ARRIVING"),
            ("confirm-arrival", "DRIVER_ARRIVED"),
            ("start", "IN_PROGRESS"),
            ("complete", "COMPLETED"),
        ]:
            r = client.post(f"{BASE['trip']}/v1/trips/{trip_id}/{endpoint}", headers=hd)
            assert r.status_code == 200 and r.json()["status"] == expected, r.text
            print(f"  {endpoint} -> {expected}")
        fare_final = r.json()["fare_final"]
        print(f"fare_final = Rs.{fare_final}")

        step("Waiting for automatic payment (real Kafka consumer, no manual trigger)")
        for _ in range(20):
            r = client.get(f"{BASE['trip']}/v1/trips/{trip_id}", headers=hr)
            status = r.json()["status"]
            if status in ("PAID", "PAID_PENDING"):
                break
            time.sleep(0.5)
        assert status == "PAID", f"expected PAID, got {status} (payment gateway failure injection or event delay)"
        print("payment status: PAID")

        step("Ledger check")
        r = client.get(f"{BASE['payment']}/v1/payments/{trip_id}/ledger", headers=hr)
        ledger = r.json()
        for entry in ledger["entries"]:
            print(f"  {entry['entry_type']:30s} {entry['account_type']:10s} Rs.{entry['amount']:>10.2f}")
        assert abs(ledger["balance"]) < 0.01, f"ledger must balance to zero (invariant I6), got {ledger['balance']}"
        print(f"ledger balance: {ledger['balance']} -- BALANCED")

        step("Rider rates the driver")
        r = client.post(f"http://localhost:8009/v1/trips/{trip_id}/rate", json={"stars": 5, "comment": "demo run"}, headers=hr)
        assert r.status_code == 200, r.text
        r = client.get(f"{BASE['trip']}/v1/trips/{trip_id}", headers=hr)
        assert r.json()["status"] == "RATED", r.json()

        step("Full audit trail")
        r = client.get(f"{BASE['trip']}/v1/trips/{trip_id}/audit", headers=hr)
        for event in r.json():
            from_status = event["from_status"] or "(start)"
            print(f"  {event['created_at']}  {from_status:>20s} -> {event['to_status']:<20s} ({event['event_type']})")

        print("\n=== DEMO: FULL CORE LOOP + PAYMENT + RATING: PASSED ===")
        return 0


if __name__ == "__main__":
    sys.exit(main())
