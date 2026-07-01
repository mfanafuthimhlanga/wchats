# Phase 17: Customer Identity Verification — Pattern Map

**Mapped:** 2026-07-01
**Files analyzed:** 10 new/modified files
**Analogs found:** 10 / 10

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `apps/api/alembic_tenant/versions/0008_customer_identities.py` | migration | batch | `apps/api/alembic_tenant/versions/0007_integration_credentials.py` | exact |
| `apps/api/app/services/identity_service.py` | service | request-response | `apps/api/app/core/security.py` + `apps/api/app/services/escalation.py` | role-match (composite) |
| `apps/api/app/api/v1/widget.py` (modified) | route | request-response | `apps/api/app/api/v1/widget.py` (self) | exact — new routes added |
| `apps/api/app/schemas/widget.py` (modified) | schema | — | `apps/api/app/schemas/widget.py` (self) | exact — new field + new models |
| `apps/api/app/services/agent_tools.py` (modified) | service | event-driven | `apps/api/app/services/agent_tools.py` (self) | exact — new ContextVar + param |
| `apps/api/app/worker/tasks/runtime/agent.py` (modified) | worker | event-driven | `apps/api/app/worker/tasks/runtime/agent.py` (self) | exact — signature + forward |
| `apps/api/app/services/transactional/tools.py` (modified) | service | request-response | `apps/api/app/services/transactional/tools.py` (self) | exact — Step 2.5 insertion |
| `apps/api/app/core/config.py` (modified) | config | — | `apps/api/app/core/config.py` (self) | exact — new settings block |
| `apps/api/tests/unit/test_identity_service.py` | test | — | `apps/api/app/core/security.py` (patterns) | role-match |
| `apps/api/tests/unit/test_identity_routes.py` | test | — | `apps/api/app/api/v1/widget.py` (patterns) | role-match |

---

## Pattern Assignments

### `apps/api/alembic_tenant/versions/0008_customer_identities.py` (migration, batch)

**Analog:** `apps/api/alembic_tenant/versions/0007_integration_credentials.py`

**Full file structure** (lines 1–64 of analog):
```python
"""Tenant DB v8 migration — customer_identities table for OTP-verified sessions.

Revision ID: 0008
Revises: 0007
...
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS customer_identities (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            external_id         TEXT NOT NULL,
            verified_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            verification_method TEXT NOT NULL,
            session_token_hash  TEXT NOT NULL,
            session_expires_at  TIMESTAMPTZ NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_customer_identities_external_id UNIQUE (external_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_customer_identities_token_hash
        ON customer_identities (session_token_hash)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_customer_identities_expires_at
        ON customer_identities (session_expires_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_customer_identities_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_customer_identities_token_hash")
    op.execute("DROP TABLE IF EXISTS customer_identities")
```

**Key analog rules from 0007** (lines 44–64):
- Use `op.execute()` raw SQL only — no `op.create_table()`.
- Every DDL statement uses `IF NOT EXISTS` so re-running is safe.
- `downgrade()` must mirror `upgrade()` in reverse order with `IF EXISTS` guards.
- No imports from `app.*` — migrations are standalone.

---

### `apps/api/app/services/identity_service.py` (service, request-response)

**Composite analog:**
- Cryptographic helpers: `apps/api/app/core/security.py` (lines 19–142)
- Email delivery: `apps/api/app/services/escalation.py` (lines 1–81)
- Redis rate pattern: `apps/api/app/api/v1/widget.py` lines 84–104 (`_check_config_rate_limit`)

**Imports pattern** (derived from analogs):
```python
import asyncio
import hashlib
import hmac
import json
import secrets
import smtplib
import time
from email.mime.text import MIMEText

import psycopg2
import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)
```

**Cryptographic core — from `security.py` lines 19–142:**
```python
# security.py already imports: hashlib, hmac, secrets (lines 19–21)
# generate_api_key() pattern — line 134–142:
def generate_api_key() -> str:
    return "vrd_live_" + secrets.token_urlsafe(32)

# hmac_key_prefix() uses hmac.new() — line 111–126 (constant-time comparison convention)
```
Copy these patterns directly for `generate_otp_code()`, `hash_otp_code()`, `verify_otp_code()`,
`generate_session_token()`, `hash_session_token()`. The module already proves stdlib usage of
`secrets`, `hashlib`, `hmac` is the project convention.

**Email delivery — from `escalation.py` lines 26–81:**
```python
def send_escalation_email(agent, reason: str, context: str) -> None:
    # Guard: all SMTP fields must be non-None and non-empty.
    if not all([settings.SMTP_HOST, settings.SMTP_FROM, settings.OWNER_EMAIL]):
        log.warning("escalation.email_not_configured", ...)
        return

    msg = MIMEText(body)
    msg["Subject"] = ...
    msg["From"] = settings.SMTP_FROM
    msg["To"] = settings.OWNER_EMAIL

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT or 587, timeout=5) as server:
            server.starttls()
            server.sendmail(settings.SMTP_FROM, [settings.OWNER_EMAIL], msg.as_string())
        log.info("escalation.email_sent", ...)
    except Exception as exc:
        log.warning("escalation.email_failed", error=str(exc), ...)
```
`send_otp_email()` in `identity_service.py` copies this exactly: same guard, same SMTP context
manager, NEVER raises (fire-and-forget). Replace `settings.OWNER_EMAIL` with the `external_id`
(customer email) destination.

**Redis OTP challenge storage — from `widget.py` lines 84–104 (`_check_config_rate_limit`):**
```python
async def _check_config_rate_limit(agent_id: str, client_ip: str, redis: Redis) -> None:
    bucket = int(time.time()) // 60
    key = f"rate:config:{client_ip}:{bucket}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 120)
    if count > 10:
        raise HTTPException(status_code=429, ...)
```
`store_otp_challenge()` uses `redis.set(key, payload, ex=TTL)` instead of INCR, but the
key-naming convention (`otp:{agent_id}:{external_id.lower()}:{method}`) and TTL-based expiry
follow the same Redis pattern as the rate-limit keys.

**Tenant DB write (UPSERT after successful verify) — from `agent.py` lines 92–114:**
```python
def _create_conversation_row(conn, agent_id: str) -> str:
    sql = "INSERT INTO conversations (...) VALUES (%s, %s, NOW(), %s::jsonb)"
    with conn.cursor() as cur:
        cur.execute(sql, (new_id, agent_id, "{}"))
    conn.commit()
```
Copy pattern: `psycopg2.connect(conn_str, connect_timeout=5)`, `with conn.cursor() as cur:`,
`conn.commit()`, `conn.close()` in a `finally`. Use parameterised `%s` only — never f-strings.
The `check_verified_session()` function uses `asyncio.to_thread()` to wrap the blocking
psycopg2 call (same pattern used throughout `agent_tools.py`).

**SMS provider abstraction — from `transactional/credential_service.py` `ProviderNotConfiguredError`:**
Use a `typing.Protocol` class (`SmsProvider`) with a `send(to, body)` method. Concrete
`TwilioSmsProvider` and `AfricasTalkingProvider` implement it. A `NullSmsProvider` logs a
warning and raises at call time (matches `ProviderNotConfiguredError` sentinel pattern).

---

### `apps/api/app/api/v1/widget.py` — new identity routes (route, request-response)

**Analog:** existing routes in `apps/api/app/api/v1/widget.py`

**JWT validation pattern** (lines 173–197):
```python
def validate_widget_jwt(token: str, expected_agent_id: str) -> dict:
    try:
        claims = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if claims.get("agent_id") != expected_agent_id:
        raise HTTPException(status_code=401, detail="Token agent_id mismatch")
    return claims
```
Both new identity routes (`POST /widget/{agent_id}/identity/request` and
`POST /widget/{agent_id}/identity/verify`) call `validate_widget_jwt` first, same as
`post_widget_chat` (line 338).

**Rate limit pattern** (lines 84–104 `_check_config_rate_limit` + lines 344–354 chat rate):
```python
bucket = str(int(time.time()) // 60)
key = f"rate:{agent_id}:{bucket}"
count = await redis_client.incr(key)
if count == 1:
    await redis_client.expire(key, 60)
if count > 60:
    raise HTTPException(status_code=429, detail="Rate limit exceeded",
                        headers={"Retry-After": "60"})
```
The OTP-specific rate limits use the same `redis.incr` + `redis.expire` pattern with different
key names and ceilings:
- Per-external_id OTP send limit: `otp_sendlimit:{agent_id}:{external_id.lower()}` → max 3 / 600s
- Per-IP limit: `otp_sendip:{ip}:{bucket_60s}` → max 10 / 60s

**204 No Content response pattern** (lines 529–539 `_cors_preflight_response`):
```python
return PlainResponse(
    status_code=204,
    headers={"Access-Control-Allow-Origin": _CORS_ALLOW_ORIGIN, ...},
)
```
`POST /widget/{agent_id}/identity/request` returns 204 on success (code is never echoed).
Copy this `PlainResponse(status_code=204, ...)` pattern with the CORS header.

**CORS header pattern** (lines 63–66, lines 282, 442):
```python
_CORS_ALLOW_ORIGIN = "*"
response.headers["Access-Control-Allow-Origin"] = _CORS_ALLOW_ORIGIN
```
All new identity routes set this header (widget endpoint convention).

**Route registration:** New routes use the existing `router = APIRouter(tags=["widget"])` at line 58.
No new router needed — append to the same file below the OPTIONS handlers.

---

### `apps/api/app/schemas/widget.py` — modified (schema)

**Analog:** `apps/api/app/schemas/widget.py` (lines 1–37)

**Existing field pattern** (lines 23–28):
```python
class WidgetChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: UUID | None = None
```
Add `verified_session_token: str | None = None` using the same `None` default convention.

**New request schemas to add:**
```python
from pydantic import Field, constr

class OtpRequestBody(BaseModel):
    external_id: str = Field(..., min_length=1, max_length=320)
    method: str = Field(..., pattern=r"^(email|sms)$")

class OtpVerifyBody(BaseModel):
    external_id: str = Field(..., min_length=1, max_length=320)
    otp_code: str = Field(..., pattern=r"^\d{6}$")   # ASVS V5 — numeric 6-digit only
    method: str = Field(..., pattern=r"^(email|sms)$")

class OtpVerifyResponse(BaseModel):
    verified_session_token: str
```
`Field(pattern=...)` follows Pydantic v2 convention already used in the codebase.

---

### `apps/api/app/services/agent_tools.py` — modified (service, event-driven)

**Analog:** `apps/api/app/services/agent_tools.py` lines 139–153 (ContextVar block)

**Existing ContextVar declarations** (lines 139–153):
```python
_conn_str_var: ContextVar[str] = ContextVar("conn_str", default="")
_agent_id_var: ContextVar[str] = ContextVar("agent_id", default="")
_agent_name_var: ContextVar[str] = ContextVar("agent_name", default="")
_strategy_var: ContextVar[RetrievalStrategy | None] = ContextVar("strategy", default=None)
_conversation_id_var: ContextVar[str] = ContextVar("conversation_id", default="")
_notify_fn_var: ContextVar = ContextVar("notify_fn", default=None)
_tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="")
_retrieve_call_count_var: ContextVar[int] = ContextVar("retrieve_call_count", default=0)
```
**Insert after line 153** (after `_retrieve_call_count_var`):
```python
# Phase 17: per-task verified session token (IDV-05)
# Empty string default = no verified session (all non-IDV tool calls pass through)
_verified_session_token_var: ContextVar[str] = ContextVar("verified_session_token", default="")
```

**`build_tool_server` signature** (lines 569–576):
```python
def build_tool_server(
    conn_str: str,
    agent_id: str,
    agent_name: str,
    strategy: RetrievalStrategy,
    conversation_id: str,
    notify_fn,
    tenant_id: str = "",
) -> object:
```
Add `verified_session_token: str = ""` as the last keyword argument. In the body (after line 614
`_retrieve_call_count_var.set(0)`), add:
```python
_verified_session_token_var.set(verified_session_token)
```

---

### `apps/api/app/worker/tasks/runtime/agent.py` — modified (worker, event-driven)

**Analog:** `apps/api/app/worker/tasks/runtime/agent.py` lines 1–79 (task signature) + `build_tool_server` call

**Task signature location:** Find `@celery_app.task(...)` decorator. The current `args` list in the
`apply_async` call in `widget.py` (lines 423–431) is:
```python
run_agent_turn.apply_async(
    args=[str(job.id), str(agent.id), body.message,
          str(body.conversation_id) if body.conversation_id else None],
    queue="runtime",
)
```
The task function receives these positionally. Add `verified_session_token: str = ""` as the
5th positional parameter.

**Forward to `build_tool_server`:** Locate the existing call to `build_tool_server(...)` in
`agent.py`. It currently passes `conn_str`, `agent_id`, `agent_name`, `strategy`,
`conversation_id`, `notify_fn`, `tenant_id`. Add `verified_session_token=verified_session_token`.

**Logging constraint (T-04-03-05):** The token must NEVER appear in a `log.*` call.
The existing pattern for `message` is: omit it from all structlog lines. Apply the same omission
to `verified_session_token`.

---

### `apps/api/app/services/transactional/tools.py` — modified (service, request-response)

**Analog:** `apps/api/app/services/transactional/tools.py` lines 147–201 (Step 1 + Step 2 enforcement)

**Lazy import pattern** (line 150):
```python
from app.services.agent_tools import _agent_id_var, _conn_str_var, _conversation_id_var  # noqa: PLC0415
```
Step 2.5 adds `_verified_session_token_var` to this same lazy import line:
```python
from app.services.agent_tools import (  # noqa: PLC0415
    _agent_id_var, _conn_str_var, _conversation_id_var, _verified_session_token_var
)
```

**Insertion point** (between lines 201 and 203 — after capability denial return, before `reserve_idempotency`):

The exact anchor text in the file is:
```python
    # -------------------------------------------------------- 3. Reserve idempotency (atomic)
    # compute_args_hash excludes idempotency_key internally — used to detect WR-02 key reuse.
    args_hash = compute_args_hash(raw_args)
```
Insert **before** this block:
```python
    # -------------------------------------------------------- 2.5 IDV gate (IDV-05)
    if snapshot.get("requires_identity_verification", False):
        vst = _verified_session_token_var.get()
        if not vst:
            await write_audit_row(
                agent_id=agent_id, conversation_id=conversation_id, skill=skill,
                arguments=raw_args, result=None, actor_decision="", actor_rationale="",
                capability_snapshot=snapshot, latency_ms=None,
                error="identity_verification.required",
            )
            return {
                "content": [{"type": "text", "text": (
                    "This action requires identity verification. "
                    "Please verify your identity with a one-time code before proceeding."
                )}],
                "is_error": True,
            }
        from app.services.identity_service import check_verified_session  # noqa: PLC0415
        session_valid = await check_verified_session(agent_id, vst, conn_str)
        if not session_valid:
            await write_audit_row(
                agent_id=agent_id, conversation_id=conversation_id, skill=skill,
                arguments=raw_args, result=None, actor_decision="", actor_rationale="",
                capability_snapshot=snapshot, latency_ms=None,
                error="identity_verification.invalid_or_expired",
            )
            return {
                "content": [{"type": "text", "text": (
                    "Identity verification required or session expired. "
                    "Please verify your identity again to proceed."
                )}],
                "is_error": True,
            }
```

**AUD-01 symmetry note:** The `write_audit_row(...)` call signature is copied verbatim from the
capability denial block at lines 178–189. Same keyword arguments, same position.

---

### `apps/api/app/core/config.py` — modified (config)

**Analog:** `apps/api/app/core/config.py` lines 82–89 (existing SMTP block)

**Existing SMTP block pattern** (lines 82–89):
```python
# M4: Escalation email (all optional — fallback to structlog WARNING when unset)
SMTP_HOST: str | None = None
SMTP_PORT: int = 587
SMTP_FROM: str | None = None
OWNER_EMAIL: str | None = None
SMTP_USER: str | None = None
SMTP_PASSWORD: str | None = None
```
Insert a new `# M17: SMS OTP` block immediately after the SMTP block, using the exact same
naming convention (`str | None = None` for all credentials, `str = "..."` for enum settings,
`int = N` for TTL integers):
```python
# M17: OTP identity verification
VERIFIED_SESSION_TTL_SECONDS: int = 3600     # 1 hour
OTP_EMAIL_TTL_SECONDS: int = 600             # 10 minutes
OTP_SMS_TTL_SECONDS: int = 300              # 5 minutes
OTP_MAX_ATTEMPTS: int = 5

# M17: SMS OTP provider ("twilio" | "africastalking")
SMS_PROVIDER: str = "twilio"
TWILIO_ACCOUNT_SID: str | None = None
TWILIO_AUTH_TOKEN: str | None = None
TWILIO_FROM_NUMBER: str | None = None
AT_API_KEY: str | None = None
AT_USERNAME: str | None = None
AT_SENDER_ID: str | None = None
```

---

### `apps/api/tests/unit/test_identity_service.py` (test)

**Analog:** Conventions from `apps/api/app/core/security.py` (the module being tested follows
the same `secrets`/`hashlib`/`hmac` pattern). Test file structure follows existing unit tests
in `apps/api/tests/unit/`.

**Test structure pattern:**
```python
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest

# Test OTP code format
def test_otp_code_format():
    from app.services.identity_service import generate_otp_code
    code = generate_otp_code()
    assert len(code) == 6
    assert code.isdigit()

# Test hash is not plaintext
def test_otp_hash_not_plaintext():
    from app.services.identity_service import generate_otp_code, hash_otp_code
    code = generate_otp_code()
    stored = hash_otp_code(code)
    assert stored != code
    assert len(stored) == 64  # SHA-256 hex

# Mock Redis for challenge state tests
@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.get.return_value = None
    r.set.return_value = True
    r.delete.return_value = 1
    return r
```

---

### `apps/api/tests/unit/test_identity_routes.py` (test)

**Analog:** `apps/api/app/api/v1/widget.py` route structure

**Test pattern:** Use `fastapi.testclient.TestClient` or `httpx.AsyncClient` with mock dependencies
(`app.dependency_overrides`). Mock `redis_client` and `db` deps. Assert HTTP status codes
(204, 200, 400, 429, 401) and response body shapes for each endpoint.

---

## Shared Patterns

### JWT Validation (all new identity routes)
**Source:** `apps/api/app/api/v1/widget.py` lines 173–197 (`validate_widget_jwt`)
**Apply to:** `POST /widget/{agent_id}/identity/request`, `POST /widget/{agent_id}/identity/verify`
```python
validate_widget_jwt(credentials.credentials, str(agent_id))
```

### Redis Async Client Injection (all new identity routes)
**Source:** `apps/api/app/api/v1/widget.py` line 238, 317
**Apply to:** Both identity route handlers
```python
redis_client=Depends(get_async_redis),
```

### CORS Header on Every Widget Response
**Source:** `apps/api/app/api/v1/widget.py` line 282, 442
**Apply to:** Both identity route responses
```python
response.headers["Access-Control-Allow-Origin"] = _CORS_ALLOW_ORIGIN
```

### Fire-and-Forget with NEVER-raises Contract
**Source:** `apps/api/app/services/escalation.py` lines 65–80
**Apply to:** `send_otp_email()` in `identity_service.py`
```python
try:
    with smtplib.SMTP(...) as server:
        server.starttls()
        server.sendmail(...)
except Exception as exc:
    log.warning("otp_email.send_failed", error=str(exc))
```

### psycopg2 Tenant DB Pattern (blocking IO wrapped in asyncio.to_thread)
**Source:** `apps/api/app/api/v1/widget.py` lines 205–220 (`_validate_conv_owner`) +
`apps/api/app/worker/tasks/runtime/agent.py` lines 92–114
**Apply to:** `check_verified_session()` in `identity_service.py`
```python
async def check_verified_session(agent_id: str, raw_token: str, conn_str: str) -> bool:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    def _query() -> bool:
        conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM customer_identities "
                    "WHERE session_token_hash = %s AND session_expires_at > NOW() LIMIT 1",
                    (token_hash,),
                )
                return cur.fetchone() is not None
        finally:
            conn.close()
    return await asyncio.to_thread(_query)
```

### Lazy Import to Break Circular Dependency
**Source:** `apps/api/app/services/transactional/tools.py` line 150
**Apply to:** Step 2.5 import of `check_verified_session` from `identity_service.py`
```python
from app.services.identity_service import check_verified_session  # noqa: PLC0415
```
Place inside the function body of `_execute_transactional_tool`, NOT at module level.

### Structlog Logger Instantiation
**Source:** Every service/route file in the codebase (e.g., `escalation.py` line 23)
**Apply to:** `identity_service.py`
```python
import structlog
log = structlog.get_logger(__name__)
```

---

## No Analog Found

All files have analogs in the codebase. No items require falling back to RESEARCH.md patterns alone.

---

## Metadata

**Analog search scope:**
- `apps/api/alembic_tenant/versions/` — migration pattern
- `apps/api/app/core/security.py` — crypto helpers
- `apps/api/app/services/escalation.py` — SMTP pattern
- `apps/api/app/api/v1/widget.py` — route + JWT + Redis + CORS patterns
- `apps/api/app/services/agent_tools.py` — ContextVar + `build_tool_server` pattern
- `apps/api/app/services/transactional/tools.py` — dispatcher enforcement pattern
- `apps/api/app/core/config.py` — settings block pattern
- `apps/api/app/worker/tasks/runtime/agent.py` — task signature + psycopg2 pattern

**Files scanned:** 12 source files read directly
**Pattern extraction date:** 2026-07-01
