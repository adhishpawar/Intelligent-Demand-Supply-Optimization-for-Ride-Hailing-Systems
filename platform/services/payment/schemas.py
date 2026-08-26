from __future__ import annotations

from pydantic import BaseModel


class ChargeResultResponse(BaseModel):
    status: str
    already_processed: bool = False
    gateway_ref: str | None = None
    reason: str | None = None


class LedgerEntryResponse(BaseModel):
    entry_type: str
    account_type: str
    account_id: str | None
    amount: float


class LedgerResponse(BaseModel):
    trip_id: str
    entries: list[LedgerEntryResponse]
    balance: float
