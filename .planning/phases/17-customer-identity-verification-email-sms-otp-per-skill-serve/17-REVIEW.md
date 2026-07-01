---
phase: 17-customer-identity-verification-email-sms-otp-per-skill-serve
reviewed: 2026-07-01T00:00:00Z
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
  critical: 2
  warning: 7
  info: 4
  total: 13
status: issues_found
---

# Phase 17: Code Review Report

**Reviewed:** 2026-07-01
**Depth:** standard
**Files Reviewed:** 14
**Status:** issues_found

## Summary

Phase 17 adds OTP identity verification (email + SMS), a verified-session DB table, a Step 2.5 IDV gate in the transactional dispatcher, and JWT-gated OTP routes on the widget API. The general architecture is sound: crypto primitives are correctly chosen (secrets.randbelow, SHA-256, hmac.compare_digest), the delete-first ordering in verify_otp is correct, the JWT guard fires first on both identity routes, the AUD-01 audit-row symmetry is maintained for both IDV-block branches, and the ContextVar plumbing for the session token is correctly isolated per-task.

Two blockers require immediate fixes before shipping: a Redis SET call that silently strips the OTP TTL on every wrong attempt (breaking the stated 10-minute/5-minute expiry window), and uncaught SMS provider exceptions that turn the "always 204" request endpoint into an intermittent HTTP 500 oracle. Seven warnings and four informational findings are documented below.

The unit-test coverage is thorough for the happy path and the major negative paths. The TTL-preservation gap (CR-01) is the main blind spot — the test for wrong-code attempts does not verify that the TTL was preserved, so the bug passes all tests undetected.

---

## Critical Issues

### CR-01: `verify_otp` strips the OTP challenge TTL on every wrong attempt

**File:** `apps/api/app/services/identity_service.py:374`

**Issue:** When a submitted code does not match, the service increments the attempts counter and writes the challenge payload back to Redis using a bare `redis.set(key, json.dumps(data))` — no `ex=` or `keepttl=True` parameter. In Redis, `SET key value` without a TTL modifier **removes** any existing TTL. The challenge was originally stored with `ex=ttl` by `store_otp_challenge`, but after the very first wrong attempt the key becomes persistent (no expiry).

Consequence: The 10-minute email / 5-minute SMS OTP window stated in `OTP_EMAIL_TTL_SECONDS` / `OTP_SMS_TTL_SECONDS` is silently bypassed after the first failed attempt. An attacker can attempt the remaining 4 guesses at any time in the future (until the key is overwritten by a new `request_otp` call). The lockout key written after the 5th attempt also has no TTL, so the locked challenge persists indefinitely in Redis.

The test `test_otp_wrong_code` does not assert on the TTL of the re-written key, so the regression passes the full test suite undetected.

**Fix:**
```python
# identity_service.py line 374 — preserve the original TTL
await redis.set(key, json.dumps(data), keepttl=True)
```

`redis-py 6.x` (pinned at 6.4.0 in pyproject.toml) supports `keepttl=True`. Add a corresponding assertion to `test_otp_wrong_code` verifying the key still has a TTL after the wrong attempt.

---

### CR-02: SMS delivery exceptions propagate uncaught, breaking the "always 204" invariant

**File:** `apps/api/app/services/identity_service.py:278-282` — `_deliver_otp`
**Also:** `apps/api/app/api/v1/widget.py:594-601` — `post_widget_identity_request`

**Issue:** `_deliver_otp` delegates SMS delivery to `provider.send()` with no exception handling. Three exception sources exist:

1. `NullSmsProvider.send()` raises `ProviderNotConfiguredError` when `SMS_PROVIDER` credentials are absent.
2. `TwilioSmsProvider.send()` raises `twilio.base.exceptions.TwilioRestException` on invalid numbers, suspended accounts, etc.
3. `AfricasTalkingProvider.send()` similarly raises on delivery failures.

None of these are caught in `_deliver_otp`. They propagate through `request_otp()` and out of the route handler. The route handler (`post_widget_identity_request`) only catches `OtpRateLimited`; everything else becomes an unhandled HTTP 500.

This breaks two stated invariants:
- The route docstring: "Always returns 204 regardless of whether external_id is known to the system — no enumeration oracle."
- A 500 for `method="sms"` when credentials are unset while `method="email"` returns 204 leaks SMS provider configuration state to unauthenticated callers with a valid JWT.

Additionally, the OTP challenge was already stored in Redis before delivery is attempted (lines 316-319 of `request_otp`). A failed delivery leaves an orphaned challenge key that will expire on its own TTL but could confuse operators expecting delivery confirmation.

The email path correctly uses a fire-and-forget pattern (`try/except Exception: log.warning(...)` in `send_otp_email`). The SMS path has no equivalent.

**Fix:**
```python
# identity_service.py — _deliver_otp, SMS branch
elif method == "sms":
    provider = _get_sms_provider()
    ttl_minutes = settings.OTP_SMS_TTL_SECONDS // 60
    body = f"Your W Chats verification code is {code}. Valid for {ttl_minutes} minutes."
    try:
        provider.send(external_id, body)
    except Exception as exc:  # noqa: BLE001
        # Fire-and-forget: log but NEVER re-raise (mirrors email pattern, T-17-08)
        log.warning("otp_sms.send_failed", error=str(exc), method=method)
```

---

## Warnings

### WR-01: `_validate_conv_owner` blocks the async event loop in a FastAPI route

**File:** `apps/api/app/api/v1/widget.py:219-228` (function), called at line 387

**Issue:** `_validate_conv_owner` opens a synchronous `psycopg2.connect()` connection inside an `async def` FastAPI route handler (`post_widget_chat`). A synchronous DB call in an async context blocks the uvicorn event loop for its entire duration. During this window, all other concurrent requests — including health checks and SSE streams — cannot be processed.

**Fix:** Wrap the call in `asyncio.to_thread`:
```python
if body.conversation_id is not None:
    owned = await asyncio.to_thread(
        _validate_conv_owner,
        agent.neon_connection_string,
        body.conversation_id,
        agent.id,
    )
```

---

### WR-02: INCR+EXPIRE race condition can permanently disable rate-limit windows

**Files:**
- `apps/api/app/services/identity_service.py:305-311` (OTP send-rate limiter)
- `apps/api/app/api/v1/widget.py:104-107` (config endpoint rate limiter)
- `apps/api/app/api/v1/widget.py:354-356` (chat rate limiter)

**Issue:** All three rate limiters use the two-step pattern:
```python
count = await redis.incr(key)   # step 1
if count == 1:
    await redis.expire(key, ttl)  # step 2
```

Between step 1 and step 2, if the process crashes (OOM kill, SIGKILL, restart under load), the key exists in Redis with no TTL. It will never expire, permanently blocking the affected external_id, IP address, or agent_id until a Redis operator manually deletes the key.

**Fix (atomic, no Lua required):**
```python
# Use SETNX + EXPIRE via the redis-py pipeline or SET with NX+EX:
added = await redis.set(key, 0, nx=True, ex=ttl)  # only sets if absent
count = await redis.incr(key)
if count > ceiling:
    raise ...
```
This sets the TTL atomically on first creation. Note: combine with an `expire` only if you need to refresh it.

---

### WR-03: `asyncio.get_event_loop()` is deprecated inside async functions (Python 3.10+)

**File:** `apps/api/app/services/agent_tools.py:306, 440, 514`

**Issue:** Three async tool functions call `loop = asyncio.get_event_loop()` inside their bodies:
- `retrieve_tool` (line 306)
- `lookup_structured_tool` (line 440)
- `escalate_to_human_tool` (line 514)

`asyncio.get_event_loop()` emits a `DeprecationWarning` in Python 3.10+ when called inside an already-running event loop. The project targets Python 3.12 (`target-version = "py312"` in pyproject.toml). Inside `async def` functions, the correct call is `asyncio.get_running_loop()`.

**Fix:**
```python
# Replace in all three tools:
loop = asyncio.get_running_loop()
```

---

### WR-04: Dead code — CORS header set on injected `Response` parameter is never sent

**File:** `apps/api/app/api/v1/widget.py:573`

**Issue:** In `post_widget_identity_request`, line 573 sets:
```python
response.headers["Access-Control-Allow-Origin"] = _CORS_ALLOW_ORIGIN
```
However, the handler returns a `PlainResponse(...)` object directly. When FastAPI handlers return a `Response` subclass directly, the framework does NOT merge headers from the injected `response` dependency parameter into the returned object. The CORS header on `response` at line 573 is never sent to the client. The actual working CORS header is correctly set on the returned `PlainResponse` at lines 606-608.

The dead assignment creates a false impression that coverage is shared, and could lead future editors to believe they only need to update the `response.headers` line.

**Fix:** Remove line 573 entirely. The comment at line 604 already explains the `PlainResponse` pattern.

---

### WR-05: `AfricasTalkingProvider.send()` reinitializes the SDK on every OTP delivery

**File:** `apps/api/app/services/identity_service.py:181-187`

**Issue:** `africastalking.initialize(self._username, self._api_key)` is called inside `send()`, which is invoked for every OTP message. The Africa's Talking SDK is designed to be initialized once as a global side effect; reinitializing on every call can reinitialize internal state, create resource contention under concurrent sends, and in some SDK versions is not thread-safe.

**Fix:** Move initialization to `__init__`:
```python
def __init__(self, api_key: str, username: str, sender_id: str | None = None) -> None:
    import africastalking  # noqa: PLC0415
    africastalking.initialize(username, api_key)
    self._sms = africastalking.SMS
    self._sender_id = sender_id

def send(self, to: str, body: str) -> None:
    kwargs: dict = {"message": body, "recipients": [to]}
    if self._sender_id:
        kwargs["senderId"] = self._sender_id
    self._sms.send(**kwargs)
```

---

### WR-06: No cross-field validation that `external_id` format matches `method`

**File:** `apps/api/app/schemas/widget.py:48-69`

**Issue:** `OtpRequestBody` and `OtpVerifyBody` validate `method` ∈ {`"email"`, `"sms"`} and bound `external_id` by length (1–320 chars), but do not validate that `external_id` conforms to the expected format for the chosen `method`. A client can submit `method="sms"` with `external_id="not-a-phone-number"` or `method="email"` with `external_id="+27821234567"`. This:
- Wastes an SMS API credit (or triggers CR-02's uncaught exception for invalid E.164)
- Stores a malformed Redis challenge key that cannot correspond to a deliverable channel

**Fix:** Add a `model_validator` to each body model:
```python
from pydantic import model_validator

class OtpRequestBody(BaseModel):
    external_id: str = Field(..., min_length=1, max_length=320)
    method: str = Field(..., pattern=r"^(email|sms)$")

    @model_validator(mode="after")
    def external_id_matches_method(self) -> "OtpRequestBody":
        if self.method == "sms" and not re.match(r"^\+\d{7,15}$", self.external_id):
            raise ValueError("external_id must be E.164 format for method='sms'")
        if self.method == "email" and "@" not in self.external_id:
            raise ValueError("external_id must be an email address for method='email'")
        return self
```

---

### WR-07: Unused `agent_id` parameter in `_check_config_rate_limit`

**File:** `apps/api/app/api/v1/widget.py:92-112`

**Issue:** `_check_config_rate_limit(agent_id, client_ip, redis)` accepts `agent_id` as its first argument but does not use it in the rate-limit key (`key = f"rate:config:{client_ip}:{bucket}"`). The rate limit is per-IP only. The unused parameter misleads callers into thinking the limit is scoped per-agent.

**Fix:** Remove the `agent_id` parameter from the function signature and update its single call site at line 269:
```python
async def _check_config_rate_limit(client_ip: str, redis: Redis) -> None: ...
# caller:
await _check_config_rate_limit(request.client.host, redis_client)
```

---

## Info

### IN-01: Hardcoded credentials in integration test

**File:** `apps/api/tests/integration/test_migrations.py:51-52`

**Issue:** `_ADMIN_DB_URL = "postgresql://wchats:wchats@localhost:5432/postgres"` hardcodes a username and password. While this is the local dev DB, the convention of using environment variables for credentials should be followed even in tests to keep `.env`-based credential management consistent.

**Fix:** `_ADMIN_DB_URL = os.environ.get("TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres")`

---

### IN-02: `check_verified_session` accepts `agent_id` but never uses it in SQL

**File:** `apps/api/app/services/identity_service.py:424`

**Issue:** The parameter is intentional per OD-1 ("agent_id accepted for call-site symmetry but NOT used in SQL WHERE clause"). However, the absence of `agent_id` in the query is a security-sensitive design choice. A future maintainer who adds `AND agent_id = %s` to the WHERE clause would silently scope sessions to a single agent instead of the whole tenant, breaking the cross-agent session-sharing design without any test failure (since existing tests supply `agent_id`).

**Fix:** Either remove the unused parameter entirely, or replace it with a clearly named sentinel:
```python
async def check_verified_session(
    raw_token: str,   # agent_id removed — not in WHERE per OD-1
    conn_str: str,
) -> bool: ...
```
Alternatively, keep it and add a prominent inline comment:
```python
# agent_id intentionally excluded from WHERE — sessions are cross-agent per OD-1.
# DO NOT add agent_id to this query without updating the design doc.
```

---

### IN-03: Raw verified_session_token is stored in Celery task args (Redis broker)

**File:** `apps/api/app/worker/tasks/runtime/agent.py:404-405`
**Also:** `apps/api/app/api/v1/widget.py:432-438`

**Issue:** `run_agent_turn.apply_async(args=[job_id, agent_id, message, conversation_id, body.verified_session_token or ""])` stores the raw session token in the Celery task message, which lives in the Redis broker. If the Redis broker is compromised, an attacker can extract raw session tokens and use them to bypass identity verification for the remaining TTL window (up to 1 hour). The analogous exposure exists for the `message` field.

This is an accepted architectural trade-off (the same reasoning applies to the `message` arg which also lives in broker storage), but it should be documented explicitly in the threat model. The token should never appear in logs (correctly enforced by T-04-03-05), and Redis-at-rest encryption (if available on the deployment target) would mitigate this.

---

### IN-04: PII (email address) included in structured log entries for OTP email

**File:** `apps/api/app/services/identity_service.py:264, 267`

**Issue:**
```python
log.info("otp_email.sent", to=to_email)
log.warning("otp_email.send_failed", error=str(exc), to=to_email)
```

The recipient email address is recorded in structured logs. Under POPIA (applicable in South Africa) and GDPR, email addresses are personal data. Log retention periods and access controls must cover these entries. If centralized logging (e.g., Sentry, Langfuse) is in use, the `to` field will be transmitted there as well.

**Fix:** Hash or omit the address, or ensure log-retention policy is documented:
```python
log.info("otp_email.sent", to_domain=to_email.split("@")[-1])  # domain only for ops
```

---

_Reviewed: 2026-07-01_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
