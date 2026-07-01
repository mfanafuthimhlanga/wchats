---
phase: 17-customer-identity-verification-email-sms-otp-per-skill-serve
fixed_at: 2026-07-01T00:00:00Z
review_path: .planning/phases/17-customer-identity-verification-email-sms-otp-per-skill-serve/17-REVIEW.md
iteration: 1
findings_in_scope: 9
fixed: 9
skipped: 0
status: all_fixed
---

# Phase 17: Code Review Fix Report

**Fixed at:** 2026-07-01
**Source review:** `.planning/phases/17-customer-identity-verification-email-sms-otp-per-skill-serve/17-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 9 (CR-01, CR-02, WR-01 through WR-07)
- Fixed: 9
- Skipped: 0

All fixes were verified with the unit test suite (141 tests, 0 failures):
`pytest tests/unit/test_identity_service.py tests/unit/test_identity_routes.py tests/unit/test_transactional_tools.py tests/unit/test_agent_tools_contextvar.py`

---

## Fixed Issues

### CR-01: `verify_otp` strips the OTP challenge TTL on every wrong attempt

**Files modified:** `apps/api/app/services/identity_service.py`, `apps/api/tests/unit/test_identity_service.py`
**Commit:** d7d36f0
**Applied fix:** Changed the bare `await redis.set(key, json.dumps(data))` at line 374 to `await redis.set(key, json.dumps(data), keepttl=True)` so Redis preserves the original TTL on write-back after a wrong attempt. Also added a `keepttl=True` assertion to `test_otp_wrong_code` to lock in this invariant — the gap that allowed the bug to pass the full test suite undetected.

Security invariants preserved: delete-first single-use (T-17-05), SHA-256 at rest (T-17-08), hmac.compare_digest (T-17-06), no secret in logs — all unchanged.

---

### CR-02: SMS delivery exceptions propagate uncaught, breaking the "always 204" invariant

**Files modified:** `apps/api/app/services/identity_service.py`
**Commit:** 4db962b
**Applied fix:** Wrapped `provider.send(external_id, body)` in the SMS branch of `_deliver_otp` with `try/except Exception` (mirroring the fire-and-forget pattern in `send_otp_email`). On any exception, `log.warning("otp_sms.send_failed", ...)` is called and the exception is swallowed — the route always returns 204. The existing `test_sms_provider_called` test continues to pass since `MagicMock.send` does not raise by default, confirming the happy path is unaffected.

---

### WR-01: `_validate_conv_owner` blocks the async event loop in a FastAPI route

**Files modified:** `apps/api/app/api/v1/widget.py`
**Commit:** 4877f83
**Applied fix:** Wrapped the synchronous `_validate_conv_owner(...)` call in `post_widget_chat` with `await asyncio.to_thread(...)` so the blocking psycopg2 connection runs in a thread-pool executor rather than the uvicorn event loop. The `asyncio` module was already imported in `widget.py`.

---

### WR-02: INCR+EXPIRE race condition can permanently disable rate-limit windows

**Files modified:** `apps/api/app/services/identity_service.py`, `apps/api/app/api/v1/widget.py`
**Commit:** 8b2cd31
**Applied fix:** Replaced all four INCR+EXPIRE two-step patterns with the atomic `SET key 0 NX EX ttl` followed by `INCR key` pattern. The `SET NX EX` atomically initialises the key with TTL only if absent; the subsequent INCR then increments it. A crash between the two calls now leaves the key with its TTL intact (set by the NX write) rather than permanently stranded with no TTL.

The four locations fixed (three from WR-02, plus one fourth instance identified during the fix):
1. `identity_service.py` — OTP per-external_id send rate limiter (`otp_sendlimit:...`)
2. `widget.py` — config endpoint rate limiter (`rate:config:...`) inside `_check_config_rate_limit`
3. `widget.py` — chat endpoint rate limiter (`rate:{agent_id}:...`) inside `post_widget_chat`
4. `widget.py` — per-IP OTP send rate limiter (`otp_sendip:...`) inside `post_widget_identity_request` (same race, identified while applying fixes — not in REVIEW.md but corrected for consistency)

---

### WR-03: `asyncio.get_event_loop()` deprecated inside async functions

**Files modified:** `apps/api/app/services/agent_tools.py`
**Commit:** 57dcd8c
**Applied fix:** Replaced all three occurrences of `loop = asyncio.get_event_loop()` with `loop = asyncio.get_running_loop()` (lines 306, 440, 514 — `retrieve_tool`, `lookup_structured_tool`, `escalate_to_human_tool`). `get_running_loop()` is the correct call inside an already-running async context on Python 3.10+ (the project targets Python 3.12).

---

### WR-04: Dead CORS header on injected `response` parameter is never sent

**Files modified:** `apps/api/app/api/v1/widget.py`
**Commit:** faa097c
**Applied fix:** Removed the dead `response.headers["Access-Control-Allow-Origin"] = _CORS_ALLOW_ORIGIN` assignment (and its surrounding comment block) from `post_widget_identity_request`. The working CORS header is still correctly set on the returned `PlainResponse` object at the end of the handler. A clarifying comment was added at the rate-limit step explaining that CORS is set on the returned `PlainResponse` directly.

---

### WR-05: `AfricasTalkingProvider.send()` reinitializes the SDK on every OTP delivery

**Files modified:** `apps/api/app/services/identity_service.py`
**Commit:** a6a62ac
**Applied fix:** Moved `import africastalking`, `africastalking.initialize(username, api_key)`, and `self._sms = africastalking.SMS` into `AfricasTalkingProvider.__init__`. The `send()` method now uses `self._sms.send(...)` without re-importing or re-initializing. The `_api_key` and `_username` instance variables are no longer needed and were removed.

---

### WR-06: No cross-field validation that `external_id` format matches `method`

**Files modified:** `apps/api/app/schemas/widget.py`
**Commit:** a8d0f57
**Applied fix:** Added `import re` and `model_validator` from pydantic, then added identical `@model_validator(mode="after")` methods to both `OtpRequestBody` and `OtpVerifyBody`. The validator rejects:
- `method="sms"` with `external_id` not matching `^\+\d{7,15}$` (E.164)
- `method="email"` with `external_id` containing no `@`

This prevents wasted SMS credits and malformed Redis challenge keys before any delivery is attempted.

---

### WR-07: Unused `agent_id` parameter in `_check_config_rate_limit`

**Files modified:** `apps/api/app/api/v1/widget.py`
**Commit:** 77dc305
**Applied fix:** Removed `agent_id: str` from the `_check_config_rate_limit` function signature (the parameter was never used in the rate-limit key) and updated the single call site in `get_widget_config` from `await _check_config_rate_limit(str(agent_id), request.client.host, redis_client)` to `await _check_config_rate_limit(request.client.host, redis_client)`. Added a docstring note clarifying that the limit is per-IP only.

---

## Skipped Issues

None — all 9 in-scope findings were fixed.

---

_Fixed: 2026-07-01_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
