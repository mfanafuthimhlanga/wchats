---
phase: 17-customer-identity-verification-email-sms-otp-per-skill-serve
reviewed: 2026-07-02T00:00:00Z
depth: standard
files_reviewed: 14
files_reviewed_list:
  - apps/api/app/services/identity_service.py
  - apps/api/app/services/transactional/tools.py
  - apps/api/app/api/v1/widget.py
  - apps/api/app/schemas/widget.py
  - apps/api/app/services/agent_tools.py
  - apps/api/app/worker/tasks/runtime/agent.py
  - apps/api/app/core/config.py
  - apps/api/alembic_tenant/versions/0008_customer_identities.py
  - apps/api/pyproject.toml
  - apps/api/tests/unit/test_identity_service.py
  - apps/api/tests/unit/test_identity_routes.py
  - apps/api/tests/unit/test_transactional_tools.py
  - apps/api/tests/unit/test_agent_tools_contextvar.py
  - apps/api/tests/integration/test_migrations.py
findings:
  critical: 0
  warning: 2
  info: 5
  total: 7
status: issues_found
---

# Phase 17: Code Review Report (Re-review after fix pass)

**Reviewed:** 2026-07-02T00:00:00Z
**Depth:** standard
**Files Reviewed:** 14
**Status:** issues_found

## Summary

This is a re-review after the fix pass that addressed the 2 Critical and 7 Warning findings from the original report. All prior Critical and Warning findings are confirmed resolved in the current code. The 4 prior Info findings remain unaddressed (as expected) and are carried forward. This pass surfaced 2 new Warnings and 1 new Info finding from code paths introduced or modified by the fixes — specifically the `asyncio.to_thread` offload, the IDV gate at step 2.5 in `_execute_transactional_tool`, and the Africa's Talking SMS provider initialisation pattern.

**Prior Critical/Warning fix verification:**
- CR-01 (`keepttl=True` in verify_otp wrong-attempt path): `identity_service.py:374` — confirmed present.
- CR-02 (SMS delivery try/except): `identity_service.py:279-283` — confirmed present, mirrors email pattern.
- WR-01 (replay before rate checks): `tools.py:279-287` — replay short-circuits before `apply_rate_and_constraint_checks`, confirmed.
- WR-02 (args_mismatch explicit error): `tools.py:289-318` — returns explicit `is_error` without executing, confirmed.
- WR-03 (actor require_human reservation released first): `tools.py:401` — `release_idempotency` called before row insert, confirmed.
- WR-04 (args_mismatch audit row): `tools.py:295-317` — `write_audit_row` called on `args_mismatch` path, confirmed.
- WR-05 (confirm_action capability gate): `tools.py:775-789` — `check_capability_access` called before DB write, confirmed.
- WR-06 (cross-field model_validator): `widget.py schemas` — `@model_validator(mode="after")` present on both `OtpRequestBody` and `OtpVerifyBody`, confirmed.
- WR-07 (per-IP rate limit on config endpoint, not per agent_id): `widget.py:92-112` — key is `rate:config:{client_ip}:{bucket}`, confirmed.

---

## Warnings

### WR-01: `AfricasTalkingProvider.__init__` eagerly imports an uninstalled package — `ModuleNotFoundError` escapes the `_deliver_otp` try/except

**File:** `apps/api/app/services/identity_service.py:176-178` and `268-283`

**Issue:** `AfricasTalkingProvider.__init__` executes `import africastalking` synchronously, which means the import runs when `_get_sms_provider()` calls `return AfricasTalkingProvider(...)`. The `africastalking` package is absent from `pyproject.toml` — only `twilio==9.10.9` is declared. If an operator sets `SMS_PROVIDER=africastalking` with valid `AT_API_KEY` and `AT_USERNAME` credentials, the call chain is:

1. `_deliver_otp(method="sms", ...)` — `_get_sms_provider()` is called outside the try/except block (line 276).
2. `AfricasTalkingProvider.__init__` runs `import africastalking` — `ModuleNotFoundError` is raised.
3. This propagates out of `_get_sms_provider()` at line 276, which is above the `try:` block on line 279.
4. The try/except wraps only `provider.send(external_id, body)` — the import exception is never caught.
5. Exception propagates through `request_otp` (no handler) → route handler (catches only `OtpRateLimited`) → FastAPI 500.

Contrast with `TwilioSmsProvider.send()` which places `from twilio.rest import Client` inside `send()` (line 167), safely inside `_deliver_otp`'s try/except. The asymmetry between the two providers means the Africa's Talking code path silently kills OTP requests with a 500 if the package is not installed.

**Fix:** Move `import africastalking` inside `send()` instead of `__init__`, matching the Twilio lazy-import pattern:

```python
def send(self, to: str, body: str) -> None:
    import africastalking  # noqa: PLC0415  — lazy import, same pattern as TwilioSmsProvider
    # self._sms and self._sender_id must be set lazily too, or initialise africastalking here
    africastalking.initialize(self._username, self._api_key)
    sms = africastalking.SMS
    kwargs: dict = {"message": body, "recipients": [to]}
    if self._sender_id:
        kwargs["senderId"] = self._sender_id
    sms.send(**kwargs)
```

Alternatively, add `africastalking` to `pyproject.toml` as a required (or optional) dependency, or catch `ModuleNotFoundError` in `_get_sms_provider()` and fall back to `NullSmsProvider` with a warning.

---

### WR-02: `check_verified_session` at IDV gate step 2.5 has no error handling — DB failures propagate as unhandled exceptions from tool handlers

**File:** `apps/api/app/services/transactional/tools.py:242-270`

**Issue:** The IDV gate (step 2.5) calls `await check_verified_session(agent_id, vst, conn_str)` at line 244 with no surrounding try/except. `check_verified_session` uses `asyncio.to_thread` to run a `psycopg2.connect` call. If the tenant DB is unavailable (e.g., transient connection error, Neon cold start timeout), `psycopg2.OperationalError` propagates through:

- `_query()` inside `asyncio.to_thread`
- `check_verified_session` (no catch)
- `_execute_transactional_tool` (no catch at step 2.5)
- The calling tool handler (e.g., `place_order_tool`) — has try/except only around `PlaceOrderInput(**args)`

The tool handler raises an uncaught exception rather than returning a structured `{"is_error": True, "content": [...]}` response. Every other rejection path in the dispatcher (capability denial, args_mismatch, rate denial, actor block, adapter error) returns a structured error. The IDV gate step is the only path that can silently escalate a DB error into an unhandled exception reaching the Claude Agent SDK.

Side effects of the unhandled exception:
- No `tool_calls_audit` row written (audit gap — AUD-01 asymmetry on DB-error path).
- Idempotency slot not consumed (safe for retry, but the error is invisible to operators).
- The SDK turn likely fails, emitting `agent.failed` without a clear cause.

This issue is specifically exposed by the `asyncio.to_thread` offload introduced in the fix pass: the offload is correct for psycopg2 blocking calls, but the exception propagation path from the thread pool is unguarded at the call site.

**Fix:** Wrap the `check_verified_session` call in a try/except and return a structured error:

```python
if snapshot.get("requires_identity_verification", False):
    vst = _verified_session_token_var.get()
    if not vst:
        await write_audit_row(..., error="identity_verification.required")
        return {"content": [...], "is_error": True}
    try:
        from app.services.identity_service import check_verified_session  # noqa: PLC0415
        session_valid = await check_verified_session(agent_id, vst, conn_str)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "transactional_tool.idv_check_failed",
            agent_id=agent_id,
            skill=skill,
            error=str(exc),
        )
        await write_audit_row(..., error=f"identity_verification.check_error:{exc}")
        return {
            "content": [{"type": "text", "text": "Identity verification check failed. Please try again."}],
            "is_error": True,
        }
    if not session_valid:
        await write_audit_row(..., error="identity_verification.invalid_or_expired")
        return {"content": [...], "is_error": True}
```

---

## Info

### IN-01: Hardcoded local Postgres credentials in integration test file

**File:** `apps/api/tests/integration/test_migrations.py:51-52`

**Issue:** `_ADMIN_DB_URL = "postgresql://wchats:wchats@localhost:5432/postgres"` and `_LOCAL_BASE = "postgresql://wchats:wchats@localhost:5432"` are hardcoded. If the developer's local Postgres uses different credentials or these files are committed to a shared repo with CI secrets scanning, this creates a false-positive secret alert. The credentials (`wchats:wchats`) are clearly development-only but they are hard-coded rather than read from environment variables.

**Fix:** Read from environment with a sensible default:
```python
import os
_ADMIN_DB_URL = os.getenv("TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres")
_LOCAL_BASE = os.getenv("TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432")
```

---

### IN-02: `check_verified_session` accepts `agent_id` but never uses it (OD-1 maintenance trap)

**File:** `apps/api/app/services/identity_service.py:424`

**Issue:** The function signature is `check_verified_session(agent_id: str, raw_token: str, conn_str: str)` but `agent_id` is not referenced anywhere in the function body. The SQL WHERE clause queries only `session_token_hash` and `session_expires_at`. The docstring acknowledges this: "agent_id accepted for call-site symmetry but NOT used in SQL WHERE clause (OD-1: uniqueness is enforced on external_id alone across the whole tenant)."

The risk is a maintenance trap: a future developer seeing `agent_id` in the signature may assume it is enforced in SQL (which it is not), or may add incorrect SQL that narrows the query to a specific agent when the table is intentionally cross-agent. The parameter also appears in the IDV gate call (`check_verified_session(agent_id, vst, conn_str)` at `tools.py:244`), perpetuating the impression that it has enforcement significance.

**Fix:** Either drop the parameter (breaking change to callers, but callers can be updated) or add a `# noqa` comment that makes the intent unambiguous. If kept for API symmetry, mark it explicitly:
```python
async def check_verified_session(
    agent_id: str,  # accepted for call-site symmetry; NOT used in SQL (OD-1 — see docstring)
    raw_token: str,
    conn_str: str,
) -> bool:
```

---

### IN-03: Raw `verified_session_token` stored in Celery task message (Redis at-rest exposure)

**File:** `apps/api/app/api/v1/widget.py:431-439`

**Issue:** `body.verified_session_token or ""` is passed as the 5th positional argument to `run_agent_turn.apply_async(args=[...])`. Celery serializes task arguments to JSON and stores them in Redis (`runtime` queue). The raw session token is therefore at rest in Redis for the duration of queue processing (typically seconds, but potentially longer under load or on worker failure).

The docstring in `agent.py:418-422` acknowledges this: "NEVER logged (parity with message, T-04-03-05)". The logging invariant is maintained — no structlog line references the token. However, the at-rest exposure in Redis differs in nature from the logged-plaintext risk addressed by T-04-03-05.

This is a documented design trade-off (threat model note, not an oversight). Mitigations in place: Redis is not a public endpoint; the token has a short TTL (VERIFIED_SESSION_TTL_SECONDS=3600); the token grants access to IDV-gated tools only (not admin or tenant-level access). Full mitigation would require encrypting the token before placing it in task args, which is out of scope for Phase 17.

**Fix (Phase 18 candidate):** Encrypt the token with the existing Fernet key before placing it in task args, and decrypt at the start of `run_agent_turn` — consistent with the `neon_connection_string` pattern.

---

### IN-04: Recipient email address logged as a structured field (POPIA/GDPR PII-in-logs)

**File:** `apps/api/app/services/identity_service.py:241, 262, 265`

**Issue:** Three log calls in `send_otp_email` include `to=to_email` as a structured log field:

```python
log.warning("otp_email.not_configured", to=to_email)   # line 241
log.info("otp_email.sent", to=to_email)                 # line 262
log.warning("otp_email.send_failed", error=str(exc), to=to_email)  # line 265
```

An email address is personal information under both POPIA (South Africa) and GDPR. Logging it as a structured field means it flows into any configured log sink (Sentry, Langfuse, CloudWatch, etc.) and may persist beyond the OTP TTL. The OTP code itself is never logged (T-17-08 satisfied), but the delivery address is.

**Fix:** Replace `to=to_email` with a truncated or hashed identifier to preserve debuggability without storing the raw address:
```python
# Pseudonymise: log domain only (user@example.com → example.com)
_log_id = to_email.split("@")[-1] if "@" in to_email else "<redacted>"
log.info("otp_email.sent", to_domain=_log_id)
```
Or hash consistently: `to_hash=hashlib.sha256(to_email.encode()).hexdigest()[:12]`.

---

### IN-05: `verify_otp` UPSERT failure after Redis DELETE leaves OTP consumed, no session created

**File:** `apps/api/app/services/identity_service.py:380-421`

**Issue:** The correct-code path in `verify_otp` deletes the Redis key at line 381 (T-17-05 single-use invariant) and then calls `await asyncio.to_thread(_upsert)` at line 420. If `_upsert` raises (e.g., `psycopg2.OperationalError` on DB timeout), the exception propagates through `asyncio.to_thread` and out of `verify_otp`. The route handler catches only `OtpInvalid` and `OtpRateLimited`, so a DB error becomes a 500.

The user is now in a degraded state:
- Their OTP is consumed (deleted from Redis) — single-use enforced even on failure.
- No `customer_identities` row was created.
- They receive a 500 error.
- Recovery requires requesting a new OTP, which is rate-limited by `OTP_SEND_MAX_PER_WINDOW`.

The delete-first ordering is correct per T-17-05 (security invariant must not be relaxed). This is an inherent tradeoff of the delete-first pattern. The issue is not new to the `asyncio.to_thread` rewrite (the same propagation would occur with an inline blocking call), but the thread offload makes it more explicit that the DB failure path is uncaught.

**Fix:** Catch DB errors from `asyncio.to_thread(_upsert)` in `verify_otp` and raise a distinct exception (`OtpDeliveryError` or similar) so the route handler can return a retriable 503 rather than a generic 500:

```python
try:
    await asyncio.to_thread(_upsert)
except Exception as exc:  # noqa: BLE001
    log.error("verify_otp.upsert_failed", agent_id=agent_id, error=str(exc))
    raise OtpStorageError("Session record could not be created — please try again") from exc
```

Route handler then catches `OtpStorageError` → 503 with `Retry-After: 30`.

---

_Reviewed: 2026-07-02T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
