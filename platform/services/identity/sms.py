"""SmsSender port (PLAN §2.1 depth matrix, GAP AS-04). No SMS provider tonight: the
`LoggingSmsSender` logs the OTP and the caller's dev-mode response includes it
directly, so the demo doesn't need a real phone. A Twilio/MSG91 adapter implementing
the same Protocol is a new file, not a rewrite of anything that calls this port.
"""
from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger("identity.sms")


class SmsSender(Protocol):
    async def send_otp(self, phone: str, code: str) -> None: ...


class LoggingSmsSender:
    async def send_otp(self, phone: str, code: str) -> None:
        logger.info("OTP for %s: %s (dev mode — no real SMS sent)", phone, code)
