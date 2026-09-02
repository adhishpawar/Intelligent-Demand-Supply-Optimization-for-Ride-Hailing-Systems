from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from libs.security.principal import Principal, Role
from libs.security.rbac import get_principal, require_roles
from services.payment.container import get_payment_service, get_session
from services.payment.repository import LedgerRepository, PaymentRepository
from services.payment.schemas import ChargeResultResponse, LedgerEntryResponse, LedgerResponse
from services.payment.service import PaymentService

router = APIRouter()


@router.post("/v1/payments/{trip_id}/charge", response_model=ChargeResultResponse)
async def charge(
    trip_id: str,
    _principal: Principal = Depends(require_roles(Role.ADMIN)),  # manual/ops retry trigger
    svc: PaymentService = Depends(get_payment_service),
    session=Depends(get_session),
):
    result = await svc.charge_trip(session, trip_id)
    return ChargeResultResponse(**result)


@router.get("/v1/payments/analytics/summary")
async def get_revenue_summary(
    # Round 3 stakeholder council (admin/tech-lead pain): the ledger has always been
    # real and balanced (RIDER_CHARGE / DRIVER_PAYOUT_CREDIT / PLATFORM_COMMISSION_
    # CREDIT per trip), but an admin could only ever see it one trip at a time via
    # the trip-detail ledger view -- there was no "how is the platform doing today"
    # rollup anywhere. Registered ahead of GET /v1/payments/{trip_id} -- same
    # must-precede-the-parameterised-route ordering as GET /v1/trips/mine.
    _principal: Principal = Depends(require_roles(Role.ADMIN)),
    session=Depends(get_session),
):
    row = (
        await session.execute(
            text(
                """
                SELECT
                    COALESCE(SUM(amount) FILTER (WHERE entry_type = 'PLATFORM_COMMISSION_CREDIT'), 0) AS commission_total,
                    COALESCE(SUM(-amount) FILTER (WHERE entry_type = 'RIDER_CHARGE'), 0) AS gross_fares_total,
                    COUNT(DISTINCT trip_id) FILTER (WHERE entry_type = 'RIDER_CHARGE') AS paid_trip_count
                FROM ledger_entries
                WHERE created_at >= date_trunc('day', now())
                """
            )
        )
    ).mappings().first()
    return {
        "commission_total": float(row["commission_total"]),
        "gross_fares_total": float(row["gross_fares_total"]),
        "paid_trip_count": int(row["paid_trip_count"]),
    }


@router.get("/v1/payments/{trip_id}")
async def get_payment(trip_id: str, _principal: Principal = Depends(get_principal), session=Depends(get_session)):
    repo = PaymentRepository(session)
    payment = await repo.get_by_trip(trip_id)
    if payment is None:
        return {"trip_id": trip_id, "status": "NOT_CREATED"}
    return payment.__dict__


@router.get("/v1/payments/{trip_id}/ledger", response_model=LedgerResponse)
async def get_ledger(trip_id: str, _principal: Principal = Depends(get_principal), session=Depends(get_session)):
    rows = (
        await session.execute(
            text("SELECT entry_type, account_type, account_id, amount FROM ledger_entries WHERE trip_id = :id"),
            {"id": trip_id},
        )
    ).mappings().all()
    entries = [
        LedgerEntryResponse(
            entry_type=r["entry_type"], account_type=r["account_type"],
            account_id=str(r["account_id"]) if r["account_id"] is not None else None, amount=float(r["amount"]),
        )
        for r in rows
    ]
    balance = sum(e.amount for e in entries)
    return LedgerResponse(trip_id=trip_id, entries=entries, balance=round(balance, 2))
