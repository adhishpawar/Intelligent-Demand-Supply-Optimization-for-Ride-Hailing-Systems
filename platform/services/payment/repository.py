"""Payment + ledger repository. `payments.trip_id` is UNIQUE (the idempotency key,
per PLAN §3.6/§3.3 mechanism 3) and `ledger_entries` has `UNIQUE(trip_id, entry_type)`
— a double-credit is a database error, not a silent duplicate row (invariant I6).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class PaymentRecord:
    payment_id: str
    trip_id: str
    status: str
    amount: float
    gateway_ref: str | None
    attempt_count: int

    def __post_init__(self) -> None:
        self.payment_id = str(self.payment_id)
        self.trip_id = str(self.trip_id)
        self.amount = float(self.amount)


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_trip(self, trip_id: str) -> PaymentRecord | None:
        row = (
            await self._session.execute(
                text("SELECT payment_id, trip_id, status, amount, gateway_ref, attempt_count FROM payments WHERE trip_id = :id"),
                {"id": trip_id},
            )
        ).mappings().first()
        return PaymentRecord(**dict(row)) if row else None

    async def create_pending(self, trip_id: str, amount: float) -> PaymentRecord:
        row = (
            await self._session.execute(
                text(
                    """
                    INSERT INTO payments (trip_id, status, amount, attempt_count)
                    VALUES (:trip_id, 'PENDING', :amount, 0)
                    ON CONFLICT (trip_id) DO UPDATE SET trip_id = payments.trip_id
                    RETURNING payment_id, trip_id, status, amount, gateway_ref, attempt_count
                    """
                ),
                {"trip_id": trip_id, "amount": amount},
            )
        ).mappings().first()
        return PaymentRecord(**dict(row))

    async def mark_result(self, trip_id: str, *, status: str, gateway_ref: str | None, failure_reason: str | None) -> None:
        await self._session.execute(
            text(
                """
                UPDATE payments SET status = :status, gateway_ref = :ref, failure_reason = :reason,
                       attempt_count = attempt_count + 1, updated_at = now()
                WHERE trip_id = :trip_id
                """
            ),
            {"status": status, "ref": gateway_ref, "reason": failure_reason, "trip_id": trip_id},
        )
        await self._session.commit()


class LedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_entries(self, trip_id: str) -> bool:
        row = (await self._session.execute(text("SELECT 1 FROM ledger_entries WHERE trip_id = :id LIMIT 1"), {"id": trip_id})).first()
        return row is not None

    async def post_entry(self, trip_id: str, entry_type: str, account_type: str, account_id: str | None, amount: float) -> None:
        await self._session.execute(
            text(
                """
                INSERT INTO ledger_entries (trip_id, entry_type, account_type, account_id, amount)
                VALUES (:trip_id, :entry_type, :account_type, :account_id, :amount)
                ON CONFLICT (trip_id, entry_type) DO NOTHING
                """
            ),
            {"trip_id": trip_id, "entry_type": entry_type, "account_type": account_type, "account_id": account_id, "amount": amount},
        )

    async def balance_for_trip(self, trip_id: str) -> float:
        row = (await self._session.execute(text("SELECT COALESCE(SUM(amount), 0) FROM ledger_entries WHERE trip_id = :id"), {"id": trip_id})).first()
        return float(row[0])

    async def commit(self) -> None:
        await self._session.commit()
