-- Round 14 stakeholder council (tech lead, security): Round 2 rate-limited
-- OTP *requests* (SMS-cost/harassment abuse), but nothing ever rate-limited OTP
-- *verify* attempts -- a 6-digit code (1 in 1,000,000) with no lockout is
-- brute-forceable well within its 5-minute TTL at any meaningful request rate,
-- against anyone whose phone number an attacker already knows. This tracks wrong
-- guesses against the specific pending code, not just calls in general -- the
-- textbook-correct defense (lock the code out after N wrong guesses, not just
-- throttle the endpoint).
ALTER TABLE otp_codes ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
