---
status: testing
phase: 17-customer-identity-verification-email-sms-otp-per-skill-serve
source: [17-VERIFICATION.md]
started: 2026-07-01T20:05:00Z
updated: 2026-07-01T20:05:00Z
---

## Current Test

number: 1
name: Live migration application — customer_identities table in a real tenant DB
expected: |
  After run_tenant_migrations(conn_str), the customer_identities table exists with
  UNIQUE(external_id), ix_customer_identities_token_hash, and ix_customer_identities_expires_at;
  get_current_alembic_revision returns '0008'; a second call is idempotent (no error, same revision).
awaiting: user response

## Tests

### 1. Live migration application — customer_identities table in a real tenant DB
expected: After run_tenant_migrations(conn_str), customer_identities exists with UNIQUE(external_id), ix_customer_identities_token_hash, ix_customer_identities_expires_at; get_current_alembic_revision returns '0008'; second call is a no-op. Run: `cd apps/api && python -m pytest tests/integration/test_migrations.py -k "0008 or customer_identities" -x -q` (requires local PostgreSQL at wchats:wchats@localhost:5432).
result: [pending]

### 2. Live OTP round-trip — verify_otp UPSERT + check_verified_session against a real tenant DB
expected: verify_otp with a correct 6-digit code (1) deletes the Redis challenge key, (2) UPSERTs a customer_identities row storing session_token_hash (not plaintext), (3) returns a raw token once. check_verified_session with that token within TTL returns True; after TTL or with a wrong token returns False. (Unit tests mock psycopg2 and pass; this needs a provisioned tenant DB connection string.)
result: [pending]

### 3. Live Twilio SMS OTP delivery
expected: With SMS_PROVIDER=twilio and valid TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER, calling POST /widget/{agent_id}/identity/request with method=sms delivers an OTP SMS to the recipient E.164 number. (Provider seam is unit-tested; twilio==9.10.9 pin passed the 17-02 human supply-chain gate.)
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
