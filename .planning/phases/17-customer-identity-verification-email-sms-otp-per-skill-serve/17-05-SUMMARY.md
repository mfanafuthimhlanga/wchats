---
phase: 17-customer-identity-verification-email-sms-otp-per-skill-serve
plan: "05"
subsystem: identity-routes
tags: [identity, otp, widget, idv, cors, rate-limit, jwt]
dependency_graph:
  requires: ["17-03", "17-04"]
  provides: ["IDV-02-HTTP", "IDV-03-HTTP", "IDV-05-transport"]
  affects: ["apps/api/app/api/v1/widget.py", "apps/api/app/schemas/widget.py"]
tech_stack:
  added: []
  patterns:
    - "FastAPI PlainResponse for 204 No Content (CORS header on returned object, not injected response)"
    - "Redis INCR + EXPIRE per-IP rate limit (10/min) for OTP send cost control"
    - "Same 400 detail for wrong vs expired OTP (no oracle pattern, T-17-19)"
    - "5th positional arg threading via apply_async (empty-string default for backward compat)"
key_files:
  created:
    - apps/api/tests/unit/test_identity_routes.py
  modified:
    - apps/api/app/schemas/widget.py
    - apps/api/app/api/v1/widget.py
decisions:
  - "PlainResponse(status_code=204, headers={...}) used instead of setting headers on injected Response dependency — FastAPI does not merge dependency response headers when handler returns a Response object directly"
  - "OPTIONS preflight handlers added for both identity routes (Rule 2 deviation — CORS preflight required for cross-origin widget use)"
  - "Tasks 2 + 3 committed together in a single widget.py commit — both modify the same file and were implemented as a single pass"
metrics:
  duration: "~15 min"
  completed: "2026-07-01"
  tasks_completed: 3
  files_changed: 3
  tests_added: 29
status: complete
---

# Phase 17 Plan 05: OTP Identity Routes + Token Dispatch Summary

One-liner: JWT-gated OTP request/verify HTTP surface over widget routes with per-IP rate limit, no-oracle 400 error, and verified_session_token forwarded as 5th Celery task arg.

## What Was Built

### Task 1 — OTP schemas + WidgetChatRequest.verified_session_token

`apps/api/app/schemas/widget.py` extended with:

- `WidgetChatRequest.verified_session_token: str | None = None` — IDV-05 transport field; None-default matches `conversation_id` convention.
- `OtpRequestBody(external_id: str, method: str)` — external_id bounded at 320 chars (RFC 5321 max); method pattern `^(email|sms)$`.
- `OtpVerifyBody(external_id, otp_code, method)` — otp_code pattern `^\d{6}$` (ASVS V5 numeric-only; rejects alpha, rejects non-6-digit length).
- `OtpVerifyResponse(verified_session_token: str)` — returned once to client on correct code; never stored/logged.

### Task 2 — identity/request + identity/verify routes

`apps/api/app/api/v1/widget.py` extended with:

**`POST /widget/{agent_id}/identity/request`** (IDV-02/IDV-03 HTTP surface):
- `validate_widget_jwt` first (T-17-20 — JWT required on both identity routes)
- Per-IP Redis INCR rate limit: key `otp_sendip:{ip}:{60s-bucket}`, ceiling 10/min, 429+Retry-After on overflow
- Delegates to `await request_otp(redis_client, agent_id, external_id, method)` from identity_service
- Returns `PlainResponse(status_code=204, headers={"Access-Control-Allow-Origin": "*"})` — code never echoed (T-17-19)
- `OtpRateLimited` → 429

**`POST /widget/{agent_id}/identity/verify`** (IDV-05 token issuance):
- `validate_widget_jwt` first (T-17-20)
- Loads agent from DB, decrypts `neon_connection_string` via `fernet_decrypt`
- Calls `await verify_otp(redis_client, agent_id, external_id, otp_code, method, conn_str)`
- Success → 200 `OtpVerifyResponse(verified_session_token=token)`
- `OtpInvalid` → 400 with detail `"Invalid or expired code"` — **same detail for wrong AND expired** (T-17-19 no oracle)
- `OtpRateLimited` → 429+Retry-After
- `otp_code` and returned token never appear in any `log.*` call (T-17-11)

**OPTIONS handlers** added for both new routes (Rule 2 deviation — CORS preflight required for cross-origin widget).

### Task 3 — verified_session_token as 5th Celery dispatch arg

`run_agent_turn.apply_async` in `post_widget_chat` now passes 5 args:
```python
args=[
    str(job.id),
    str(agent.id),
    body.message,
    str(body.conversation_id) if body.conversation_id else None,
    body.verified_session_token or "",  # IDV-05: empty string = no verified session
]
```
Token is absent from the `widget_chat.dispatched` log line (T-04-03-05 / T-17-11 parity with `message`).

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | `e24924c` | feat(17-05): add OTP schemas + verified_session_token to WidgetChatRequest |
| 2+3 | `6f4cf81` | feat(17-05): add identity/request + identity/verify routes to widget.py |

## Test Coverage

`tests/unit/test_identity_routes.py` — 29 tests, all pass:

- **Schema (13):** OtpVerifyBody rejects non-6-digit codes, alpha codes, invalid method; accepts valid email/sms; OtpVerifyResponse carries token; WidgetChatRequest defaults to None
- **Identity request route (7):** 204 + empty body; 401/403 without JWT; 401 on invalid JWT; 429 on per-IP overflow (request_otp not called); 429 on OtpRateLimited; CORS header set; 401 on JWT agent_id mismatch
- **Identity verify route (7):** 200 + token on correct code; 400 + no token on wrong code; 400 same detail for expired (no oracle); 429 on OtpRateLimited; 401/403 without JWT; CORS header set; 401 on JWT mismatch
- **Chat dispatch (3):** with token → 5th arg = token; without token → 5th arg = ""; null token → 5th arg = ""

## Security Verifications

| Threat | Status |
|--------|--------|
| T-17-18 (SMS flooding) | Per-IP 10/min + per-external_id cap in service |
| T-17-19 (enumeration oracle) | 204 always on request; same 400 detail for wrong/expired on verify |
| T-17-11 (token/code in logs) | Grep confirms absent from all widget.py log calls |
| T-17-07 (session fixation) | Token minted server-side by verify_otp; client never supplies token to verify |
| T-17-20 (JWT bypass) | validate_widget_jwt runs first on both routes |

## Deviations from Plan

### Auto-added (Rule 2) — OPTIONS preflight handlers

**Found during:** Task 2 implementation

**Issue:** The plan specified POST routes but not OPTIONS handlers. Cross-origin widget clients must send an OPTIONS preflight before the POST. Without handlers, browsers would receive 405 and the POST would be blocked.

**Fix:** Added `options_widget_identity_request` and `options_widget_identity_verify` handlers reusing `_cors_preflight_response()`.

**Files modified:** `apps/api/app/api/v1/widget.py`

**Commit:** `6f4cf81`

### Auto-fixed (Rule 1) — CORS header on 204 PlainResponse

**Found during:** Test run (test_cors_header_is_set)

**Issue:** Setting `response.headers["Access-Control-Allow-Origin"]` on the injected `Response` dependency does not propagate to a returned `PlainResponse` object. FastAPI only merges dependency response headers into non-Response return values.

**Fix:** Set CORS header directly in the `PlainResponse(status_code=204, headers={...})` constructor, matching the `_cors_preflight_response()` pattern.

**Files modified:** `apps/api/app/api/v1/widget.py`

**Commit:** `6f4cf81`

### Minor — Tasks 2 and 3 committed together

Tasks 2 and 3 both modify `apps/api/app/api/v1/widget.py`. They were implemented in a single editing pass and committed together (`6f4cf81`). All acceptance criteria for both tasks are satisfied as evidenced by the 16 route tests and 3 dispatch tests passing.

## Known Stubs

None. All routes are fully wired to the identity_service functions from 17-04. The schema fields are fully populated in requests and responses.

## Threat Flags

No new network endpoints, auth paths, or schema changes beyond what is documented in the plan's threat model.

## Self-Check: PASSED

- `apps/api/app/schemas/widget.py` exists with OtpRequestBody, OtpVerifyBody, OtpVerifyResponse, WidgetChatRequest.verified_session_token
- `apps/api/app/api/v1/widget.py` contains `identity/verify` (grep confirmed)
- `apps/api/tests/unit/test_identity_routes.py` exists and contains `identity/verify`
- Commits `e24924c` and `6f4cf81` exist in git log
- 29/29 tests pass (`python -m pytest tests/unit/test_identity_routes.py -x -q`)
- No otp_code or verified_session_token in widget.py log calls (grep confirmed)
