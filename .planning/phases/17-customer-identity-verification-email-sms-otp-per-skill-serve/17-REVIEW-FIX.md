---
phase: 17-customer-identity-verification-email-sms-otp-per-skill-serve
fixed_at: 2026-07-02T00:00:00Z
review_path: .planning/phases/17-customer-identity-verification-email-sms-otp-per-skill-serve/17-REVIEW.md
iteration: 1
findings_in_scope: 7
fixed: 7
skipped: 0
status: all_fixed
---

# Phase 17: Code Review Fix Report

**Fixed at:** 2026-07-02T00:00:00Z
**Source review:** .planning/phases/17-customer-identity-verification-email-sms-otp-per-skill-serve/17-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 7 (2 Warning + 5 Info)
- Fixed: 7
- Skipped: 0

## Fixed Issues

### WR-01: AfricasTalkingProvider lazy import

**Files modified:** `apps/api/app/services/identity_service.py`
**Commit:** f7ec49b
**Applied fix:** Moved `import africastalking` and `africastalking.initialize()` out of `__init__` and into `send()`, mirroring the `TwilioSmsProvider` lazy-import pattern. `__init__` now stores `_api_key`, `_username`, and `_sender_id` as plain attributes. The import (and any `ModuleNotFoundError`) now occurs inside `send()`, which is called from inside `_deliver_otp`'s try/except — keeping the exception contained and preventing a 500 on missing package.

### WR-02: check_verified_session error handling at IDV gate

**Files modified:** `apps/api/app/services/transactional/tools.py`
**Commit:** b566b54
**Applied fix:** Wrapped the `await check_verified_session(agent_id, vst, conn_str)` call (step 2.5 in `_execute_transactional_tool`) in `try/except Exception`. On any exception (e.g., `psycopg2.OperationalError` from `asyncio.to_thread`): logs a `transactional_tool.idv_check_failed` warning, writes exactly one audit row with `error="identity_verification.check_failed"` (preserving AUD-01 symmetry), and returns a structured `is_error` response — failing CLOSED. The mutating tool never proceeds when the IDV check cannot complete. Step 2.5-before-reserve_idempotency ordering is preserved.

### IN-01: Hardcoded test DB credentials

**Files modified:** `apps/api/tests/integration/test_migrations.py`
**Commit:** 407d8d5
**Applied fix:** Added `import os` and replaced the two hardcoded constants with `os.getenv("TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres")` and `os.getenv("TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432")`. The defaults are unchanged for local development; CI can override via environment variables.

### IN-02: agent_id unused in check_verified_session — maintenance trap

**Files modified:** `apps/api/app/services/identity_service.py`
**Commit:** 96b404c
**Applied fix:** Expanded the function signature to multi-line form and added an inline comment on the `agent_id` parameter: `# accepted for call-site symmetry; NOT used in SQL (OD-1 — see docstring)`. This is directly visible to a future maintainer who edits the signature, preventing an incorrect "fix" of adding `agent_id` to the SQL WHERE clause which would break cross-agent session semantics (OD-1). The docstring already contained the full explanation; the inline comment is the first-line defence.

### IN-03: Raw verified_session_token in Celery task args

**Files modified:** `apps/api/app/api/v1/widget.py`
**Commit:** 42a032a
**Applied fix:** Added a `THREAT MODEL NOTE (Phase 17 accepted trade-off)` comment block above the `run_agent_turn.apply_async(...)` call, documenting: mitigations already in place (Redis not a public endpoint; short TTL; limited grant scope), and that full Fernet encryption is deferred to Phase 18 — consistent with the `neon_connection_string` pattern. Behavior is unchanged.

### IN-04: Recipient email address logged as PII

**Files modified:** `apps/api/app/services/identity_service.py`
**Commit:** 7cf22ea
**Applied fix:** Introduced `_log_domain = to_email.split("@")[-1] if "@" in to_email else "<redacted>"` at the top of `send_otp_email`, and replaced all three log calls (`otp_email.not_configured`, `otp_email.sent`, `otp_email.send_failed`) from `to=to_email` to `to_domain=_log_domain`. The full email address no longer flows into any log sink; the domain is sufficient for diagnosing delivery failures without storing PII (POPIA/GDPR compliance).

### IN-05: verify_otp UPSERT failure after Redis DELETE — bare 500

**Files modified:** `apps/api/app/services/identity_service.py`, `apps/api/app/api/v1/widget.py`
**Commit:** 6d1a262
**Applied fix (identity_service.py):** Added `OtpStorageError` exception class (with docstring explaining delete-first invariant and expected recovery path). Wrapped `await asyncio.to_thread(_upsert)` in `try/except Exception`, which logs `verify_otp.upsert_failed` at ERROR level and raises `OtpStorageError` — preserving the delete-first ordering (Redis key already deleted; T-17-05 single-use invariant is not relaxed).
**Applied fix (widget.py):** Added `OtpStorageError` to the import line. Added `except OtpStorageError` handler after `except OtpRateLimited` in the verify OTP route, which returns HTTP 503 with `Retry-After: 30` and a detail string telling the client to request a new OTP. The 400/429 paths are unchanged.

Unit test run post-fix (135 tests across `test_identity_service.py`, `test_identity_routes.py`, `test_transactional_tools.py`): **135 passed**.

---

_Fixed: 2026-07-02T00:00:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
