# Phase 17: Customer Identity Verification (Email/SMS OTP, Per-Skill, Server-Enforced) — Research

**Researched:** 2026-07-01
**Domain:** OTP generation + delivery, verified session tokens, server-side enforcement in the transactional dispatcher
**Confidence:** MEDIUM

---

## Summary

Phase 17 adds a customer identity verification layer (IDV) on top of the existing transactional tool infrastructure from Phases 14–16. When a capability envelope has `requires_identity_verification = true`, the tool dispatcher blocks the mutating call until the customer holds a valid verified session token — never inferred from agent prose. The OTP flows (email and SMS) are deterministic server-side operations, fully outside the agent's judgment.

The core architecture is an extension of the Phase 14 enforcement model: a new Step 2.5 inserted into `_execute_transactional_tool` in `apps/api/app/services/transactional/tools.py`. The enforcement reads a new `_verified_session_token_var` ContextVar (parallel to the existing `_conn_str_var`, `_agent_id_var`, etc.) and validates it against the `customer_identities` table in the tenant DB.

Two new FastAPI route groups on the widget path handle the OTP request/verify flow. Email OTP reuses the existing SMTP stack (`escalation.py` pattern, `SMTP_*` settings). SMS OTP uses a provider-abstracted notifier (recommended default: Twilio, configurable to Africa's Talking for ZA production cost optimization).

**Primary recommendation:** Add IDV enforcement as Step 2.5 in `_execute_transactional_tool` (before `reserve_idempotency`). Store OTP challenge state in Redis (TTL-based). Add a `customer_identities` table as tenant-DB migration 0008. Thread `verified_session_token` through `WidgetChatRequest` → `run_agent_turn` task args → `build_tool_server` → `_verified_session_token_var` ContextVar.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| IDV-01 | `customer_identities` tenant-DB table — `external_id, verified_at, verification_method, session_token_hash, session_expires_at` | Migration 0008 in `alembic_tenant/versions/`; follows 0007 pattern with IF NOT EXISTS guards |
| IDV-02 | Email-OTP verification flow (request code → verify → short-lived verified session) | POST /widget/{agent_id}/identity/request + POST /widget/{agent_id}/identity/verify; reuses SMTP stack |
| IDV-03 | SMS-OTP verification flow | Same endpoints, `method: "sms"`; requires SMS provider (Twilio recommended); abstraction pattern defined |
| IDV-04 | Per-skill verification config driven by envelope `requires_identity_verification` | Already a column in `capability_envelopes` (Phase 14); `check_capability_access` snapshot already includes it |
| IDV-05 | Mutating tool blocked server-side until valid verified session exists — never from agent prose | New Step 2.5 in `_execute_transactional_tool`; reads `_verified_session_token_var`; DB lookup against tenant `customer_identities` |
</phase_requirements>

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| OTP code generation | API / Backend | — | Pure server-side: `secrets.randbelow` + hash; no agent involvement |
| OTP delivery (email) | API / Backend | SMTP relay | Deterministic code path; reuses `escalation.py` SMTP pattern |
| OTP delivery (SMS) | API / Backend | SMS provider (Twilio/Africa's Talking) | HTTP call to external provider; adapter behind protocol |
| Verified-session issuance | API / Backend | Tenant DB | `secrets.token_urlsafe(32)` → SHA-256 hash stored in `customer_identities` |
| Enforcement gate (IDV-05) | API / Backend (Celery) | Tenant DB | Step 2.5 in `_execute_transactional_tool`; reads ContextVar, queries tenant DB |
| Per-skill IDV config | Control DB | Capability envelope | `requires_identity_verification` column already exists in `capability_envelopes` |
| OTP challenge temp state | Redis | — | TTL-based; automatic expiry; no durable record needed for OTP codes |
| `customer_identities` durable record | Tenant DB (Neon) | — | Per-tenant isolation; follows `integration_credentials` (0007) pattern |
| Widget session token transport | Browser / Client | FastAPI route | Client holds raw token; passes in `WidgetChatRequest.verified_session_token` |

---

## Standard Stack

### Core (no new packages required)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `secrets` (stdlib) | Python 3.11+ | OTP code generation + session token generation | Cryptographically secure random; already used in `security.py` for API key generation |
| `hashlib` (stdlib) | Python 3.11+ | SHA-256 hash of OTP code and session token | Already imported in `security.py` |
| `hmac` (stdlib) | Python 3.11+ | Constant-time comparison (`hmac.compare_digest`) | Already imported in `security.py` |
| `smtplib` (stdlib) | Python 3.11+ | Email OTP delivery | Already used in `escalation.py`; zero new dependency |
| `redis==6.4.0` (already pinned) | 6.4.0 | OTP challenge state storage (TTL keys) | Already in `pyproject.toml`; rate-limit Redis pattern in `enforcement.py` |

**No new packages are needed for the email OTP path.** All required cryptographic and email primitives are already present in the codebase.

### Supporting (SMS path only — planner/user decision required)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `twilio` | 9.10.9 (latest) | SMS delivery (and optionally Twilio Verify API) | Recommended default; already installed on dev machine (9.10.0 present in pip) |
| `africastalking` | 2.0.2 (latest) | SMS delivery via Africa's Talking (18+ African countries, direct ZA carrier) | Recommended for ZA production due to lower latency and cost on SA routes |

Both SMS packages are discovered from official provider documentation and confirmed on PyPI. The `twilio` package is confirmed installed on this machine at 9.10.0. `africastalking` is the correct PyPI name (NOT `africa-talking`, which does not exist).

**Installation (SMS path, one provider only):**
```bash
# Recommended default (Twilio):
pip install "twilio==9.10.9"

# Alternative (Africa's Talking for ZA production):
pip install "africastalking==2.0.2"
```

**Version verification (confirmed 2026-07-01):**
```bash
pip index versions twilio       # → 9.10.9 (confirmed via pip index)
pip index versions africastalking  # → 2.0.2 (confirmed via pip index)
```

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Self-managed OTP hash in Redis | Twilio Verify API (fully managed) | Twilio Verify handles code gen, delivery, rate limits, fraud detection — but costs $0.05/verification and ties SMS OTP tightly to Twilio; self-managed is provider-agnostic |
| SMTP for email OTP | SendGrid / Mailgun transactional API | SMTP is already wired; no new dependency. Transactional APIs are better for production deliverability but are out of scope for this phase |
| `africastalking` for SMS | Local ZA aggregators (Arkesel, Termii, Infobip) | Africa's Talking has 18+ country direct carrier connections; Termii is stronger in West Africa (Nigeria); Infobip has ZA presence but is enterprise-focused |

---

## Package Legitimacy Audit

| Package | Registry | Source Repo | Verdict | Disposition |
|---------|----------|-------------|---------|-------------|
| `secrets` (stdlib) | stdlib | python.org | OK | Approved — no install needed |
| `hashlib` (stdlib) | stdlib | python.org | OK | Approved — no install needed |
| `hmac` (stdlib) | stdlib | python.org | OK | Approved — no install needed |
| `twilio` | PyPI (9.10.9) | github.com/twilio/twilio-python | SUS (unknown-downloads via tool, but official Twilio SDK) [ASSUMED: not verified via Context7/official docs in this session] | Conditionally approved — planner must add `checkpoint:human-verify` before pinning |
| `africastalking` | PyPI (2.0.2) | github.com/AfricasTalkingLtd/africastalking-python | SUS (unknown-downloads via tool) [ASSUMED: not verified via official docs in this session] | Conditionally approved — planner must add `checkpoint:human-verify` before pinning |

**Packages removed due to SLOP verdict:** `africa-talking` (does not exist on PyPI — the correct name is `africastalking`)

**Packages flagged as suspicious [SUS]:** `twilio`, `africastalking` — both have legitimate source repos on GitHub and long release histories, but the legitimacy tool returned SUS due to unknown download counts. The planner must gate each install behind a `checkpoint:human-verify` task. **Context note:** `twilio 9.10.0` is already installed on the developer's machine and already used in sibling projects (`one-for-all`, `salga-trust-engine`) — this strongly suggests it is legitimate.

*The email OTP path requires ZERO new packages — all primitives are already in the codebase.*

---

## Architecture Patterns

### System Architecture Diagram

```
Widget (Preact browser)
  │
  │  POST /widget/{agent_id}/identity/request
  │  { external_id: "alice@email.com", method: "email" }
  ▼
FastAPI widget.py route
  │  rate-limit check (Redis)
  │  JWT validation
  │  → identity_service.request_otp(agent_id, external_id, method)
  │      generate 6-digit code (secrets.randbelow)
  │      SHA-256 hash
  │      store in Redis: otp:{agent_id}:{external_id}:{method}
  │              value: {hash, attempts:0, expires_at}  TTL=600s
  │      send delivery (email → SMTP, sms → SMS provider)
  │
  └──► 204 No Content
  
Widget
  │
  │  POST /widget/{agent_id}/identity/verify
  │  { external_id: "alice@email.com", otp_code: "123456", method: "email" }
  ▼
FastAPI widget.py route
  │  → identity_service.verify_otp(agent_id, external_id, otp_code, method)
  │      fetch Redis key
  │      check attempt count (max 5) → 429 if exceeded
  │      hmac.compare_digest(stored_hash, sha256(code))
  │      if mismatch → increment attempts, 400
  │      if match → delete Redis key (single-use consumption)
  │                  generate session token (secrets.token_urlsafe(32))
  │                  SHA-256 hash token
  │                  UPSERT customer_identities (tenant DB):
  │                    external_id, verified_at, verification_method,
  │                    session_token_hash, session_expires_at
  │
  └──► 200 { verified_session_token: "<raw 43-char token>" }

Widget
  │
  │  POST /widget/{agent_id}/chat
  │  { message: "...", conversation_id: "...", verified_session_token: "<token>" }
  ▼
FastAPI widget.py → run_agent_turn.apply_async(
    [job_id, agent_id, message, conversation_id, verified_session_token]
)

Celery run_agent_turn task
  │  build_tool_server(..., verified_session_token=vst)
  │    → _verified_session_token_var.set(vst)
  ▼
Agent SDK turn → tool call (e.g. issue_refund)
  │
  ▼
_execute_transactional_tool dispatcher
  Step 1: IN-03 agent_id guard
  Step 2: check_capability_access → snapshot  [includes requires_identity_verification]
  Step 2.5: IDV gate ←── NEW (IDV-05)
  │   if snapshot["requires_identity_verification"]:
  │     vst = _verified_session_token_var.get()
  │     if not vst: return is_error("identity_verification_required")
  │     valid = check_verified_session(agent_id, vst, conn_str)  ← tenant DB query
  │     if not valid: audit row + return is_error("identity_verification_expired")
  Step 3: reserve_idempotency
  ... etc (existing steps 4-7 unchanged)
```

### Recommended Project Structure (new files only)

```
apps/api/
├── app/
│   ├── services/
│   │   └── identity_service.py          # OTP generation, delivery, verify, session check
│   └── api/v1/
│       └── widget.py                    # + 2 new identity routes added here (or new identity.py)
├── alembic_tenant/versions/
│   └── 0008_customer_identities.py      # tenant-DB migration
└── tests/
    └── unit/
        └── test_identity_service.py     # OTP + session tests
```

### Pattern 1: OTP Code Generation and Hashing

**What:** Generate a 6-digit numeric OTP using `secrets.randbelow` and store only its SHA-256 hash.

**When to use:** Both email and SMS OTP channels. Always hash before storing; never store the plaintext code.

```python
# Source: Python stdlib docs + security.py pattern in this codebase
import hashlib
import hmac
import secrets

def generate_otp_code() -> str:
    """Return a 6-digit zero-padded OTP code. Cryptographically random."""
    return f"{secrets.randbelow(1_000_000):06d}"

def hash_otp_code(code: str) -> str:
    """SHA-256 hash of the plaintext code for storage."""
    return hashlib.sha256(code.encode()).hexdigest()

def verify_otp_code(stored_hash: str, submitted_code: str) -> bool:
    """Constant-time comparison to prevent timing side-channel attacks."""
    submitted_hash = hashlib.sha256(submitted_code.encode()).hexdigest()
    return hmac.compare_digest(stored_hash, submitted_hash)
```

### Pattern 2: Session Token Generation and Hashing

**What:** Generate an opaque session token with 256 bits of entropy; store only its SHA-256 hash.

**When to use:** After successful OTP verification. The raw token is returned to the client once and never stored.

```python
# Source: security.py generate_api_key() pattern + stdlib secrets docs
import hashlib
import secrets

def generate_session_token() -> str:
    """256-bit URL-safe base64 opaque token (43 chars)."""
    return secrets.token_urlsafe(32)

def hash_session_token(token: str) -> str:
    """SHA-256 hash for storage in session_token_hash column."""
    return hashlib.sha256(token.encode()).hexdigest()
```

### Pattern 3: OTP Challenge State in Redis

**What:** Store OTP hash + attempt count + expiry in Redis with a TTL. Automatic cleanup; no manual purge needed.

**When to use:** Between `request_otp` and `verify_otp` calls. NOT for verified sessions (those go in the DB).

```python
# Source: enforcement.py Redis pattern in this codebase
import json
import time

OTP_TTL_SECONDS = 600  # 10 minutes for email; 300 for SMS [ASSUMED — configurable]
OTP_MAX_ATTEMPTS = 5

def _otp_redis_key(agent_id: str, external_id: str, method: str) -> str:
    """Redis key for a pending OTP challenge."""
    # Normalize: lowercase external_id to prevent case-sensitivity bypass
    return f"otp:{agent_id}:{external_id.lower()}:{method}"

def store_otp_challenge(redis_client, agent_id: str, external_id: str,
                         method: str, code_hash: str) -> None:
    key = _otp_redis_key(agent_id, external_id, method)
    payload = json.dumps({
        "hash": code_hash,
        "attempts": 0,
        "expires_at": time.time() + OTP_TTL_SECONDS,
    })
    redis_client.set(key, payload, ex=OTP_TTL_SECONDS)
```

### Pattern 4: Verified-Session DB Check (IDV-05 enforcement)

**What:** Hash the presented token and look up `customer_identities` in the tenant DB. The lookup uses `conn_str` from ContextVar (tenant DB, not control DB).

**When to use:** Step 2.5 in `_execute_transactional_tool` when `snapshot["requires_identity_verification"]` is True.

```python
# Source: tools.py lazy import pattern + idempotency.py psycopg2 pattern [ASSUMED pattern]
import asyncio
import hashlib

import psycopg2

async def check_verified_session(agent_id: str, raw_token: str, conn_str: str) -> bool:
    """Return True if raw_token corresponds to a valid, non-expired session in tenant DB."""
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    def _query() -> bool:
        conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM customer_identities "
                    "WHERE session_token_hash = %s AND session_expires_at > NOW() "
                    "LIMIT 1",
                    (token_hash,),
                )
                return cur.fetchone() is not None
        finally:
            conn.close()

    return await asyncio.to_thread(_query)
```

### Pattern 5: SMS Provider Abstraction

**What:** A typed Protocol class that both `TwilioSmsProvider` and `AfricasTalkingProvider` implement. Resolved at startup from `settings.SMS_PROVIDER`.

**When to use:** `identity_service.py` calls `sms_provider.send(to, body)` without knowing the backend.

```python
# Source: credential_service.py ProviderNotConfiguredError pattern [ASSUMED pattern]
from typing import Protocol

class SmsProvider(Protocol):
    def send(self, to: str, body: str) -> None: ...

class TwilioSmsProvider:
    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None: ...
    def send(self, to: str, body: str) -> None:
        from twilio.rest import Client  # [ASSUMED: twilio package import]
        Client(self._sid, self._token).messages.create(
            body=body, from_=self._from_number, to=to
        )

class AfricasTalkingProvider:
    def __init__(self, api_key: str, username: str, sender_id: str = "") -> None: ...
    def send(self, to: str, body: str) -> None:
        import africastalking  # [ASSUMED: africastalking package import]
        africastalking.initialize(self._username, self._api_key)
        africastalking.SMS.send(body, [to], self._sender_id or None)
```

### Anti-Patterns to Avoid

- **Trusting agent prose for verification:** The agent must never be in a position to "tell" the server that the customer is verified. The gate is in deterministic Python, not in an LLM output.
- **Storing plaintext OTP codes:** Always SHA-256 hash the code before Redis storage. The raw code goes only to the customer's email/phone.
- **Using `==` for code comparison:** Use `hmac.compare_digest` to prevent timing attacks.
- **Putting the verified-session check in the capability check or rate-limit step:** It must be between capability check (Step 2) and idempotency reservation (Step 3) so the rate counter is not consumed on a rejected unverified call.
- **Querying `customer_identities` from the control DB:** It is in the TENANT DB. Use `conn_str` from `_conn_str_var` ContextVar, same as the idempotency helpers.
- **SMS provider credentials in task args:** Use `settings.TWILIO_ACCOUNT_SID` etc. fetched at service construction time; these are platform-level secrets, not tenant-level.
- **Using `random.randint` for OTP generation:** `secrets.randbelow` is the stdlib-approved cryptographic random source.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Constant-time token comparison | Custom `==` comparison | `hmac.compare_digest` | Timing side-channel: naive `==` short-circuits on first byte mismatch, leaking hash prefix information |
| Cryptographic randomness | `random.randint` or `uuid4()` | `secrets.randbelow` / `secrets.token_urlsafe` | `random` is not cryptographically secure; `uuid4` entropy is fine but `secrets` is explicit about security intent |
| OTP code hash storage | Encrypted field | SHA-256 hex string | OTP codes are single-use + short-lived; SHA-256 (not Argon2) is appropriate here because the code space (1M combinations) is too small for password-strength hashing but the TTL + attempt limit is the primary defense |
| SMS delivery | Direct HTTP to carrier APIs | Twilio or Africa's Talking SDK | Carrier APIs vary by country, change routing, have retry semantics; use a battle-tested SDK |
| Redis OTP state serialization | Custom binary format | `json.dumps` / `json.loads` | Matches the existing `enforcement.py` Redis pattern; human-readable for debugging |

**Key insight:** The security model for OTP codes does NOT rely on hash unbreakability — the code space is only 1M values (weak for password hashing). The security comes from: (1) 5-attempt lockout, (2) 10-minute TTL, (3) single-use consumption, (4) delivery to a controlled channel (email/phone). Hash storage prevents the `redis-cli` operator from reading undelivered codes.

---

## Existing Integration Points (Codebase Audit)

### 1. Capability Envelope — `requires_identity_verification` Field

**File:** `apps/api/app/models/capability_envelope.py`
**Finding:** `requires_identity_verification: Mapped[bool]` column with `server_default=false` already exists on the `CapabilityEnvelope` ORM model. [VERIFIED: read from codebase]

**File:** `apps/api/alembic/versions/0014_transactional_substrate.py`
**Finding:** The column `requires_identity_verification BOOLEAN NOT NULL DEFAULT false` is already in the DDL. [VERIFIED: read from codebase]

**File:** `apps/api/app/services/transactional/enforcement.py` — `check_capability_access`
**Finding:** The `SELECT` query in `check_capability_access` already reads `requires_identity_verification` and includes it in the `snapshot` dict returned to the dispatcher. No schema or enforcement changes needed — just need to read `snapshot["requires_identity_verification"]` in the new Step 2.5. [VERIFIED: read from codebase]

### 2. Tool-Execution Enforcement Point (IDV-05)

**File:** `apps/api/app/services/transactional/tools.py` — `_execute_transactional_tool`
**Finding:** The 7-step dispatcher is documented in the module docstring. Step 2.5 must be inserted between Step 2 (`check_capability_access`) and Step 3 (`reserve_idempotency`). This is consistent with the existing pattern for the actor gate (Step 5). [VERIFIED: read from codebase]

**Critical constraint:** The IDV check must run BEFORE `reserve_idempotency` (Step 3) to avoid consuming the idempotency slot for a rejected unverified call. If the session is invalid, the tool returns `is_error` WITHOUT writing an idempotency row, so the customer can present a valid token and retry with the same `idempotency_key`.

**LANDMINE — audit path:** When IDV blocks a call, an audit row SHOULD be written (AUD-01 symmetry: every non-replay, non-in_progress tool entry gets one audit row). The error value: `"identity_verification.required"` (no session) or `"identity_verification.expired"` (session found but expired / not found after hash lookup). Pattern follows `capability.denial` in Steps 2 and 4.

### 3. ContextVar Infrastructure

**File:** `apps/api/app/services/agent_tools.py`
**Finding:** ContextVars defined: `_conn_str_var`, `_agent_id_var`, `_agent_name_var`, `_strategy_var`, `_conversation_id_var`, `_notify_fn_var`, `_tenant_id_var`, `_retrieve_call_count_var`. A new `_verified_session_token_var: ContextVar[str] = ContextVar("verified_session_token", default="")` must be added here. [VERIFIED: read from codebase]

`build_tool_server` must accept a `verified_session_token: str = ""` parameter and call `_verified_session_token_var.set(verified_session_token)`. [VERIFIED: function signature read from codebase]

### 4. Widget Chat Request → Task Arg Path

**File:** `apps/api/app/schemas/widget.py` — `WidgetChatRequest`
**Finding:** Current fields: `message: str`, `conversation_id: UUID | None`. A new field `verified_session_token: str | None = None` must be added. [VERIFIED: read from codebase]

**File:** `apps/api/app/api/v1/widget.py` — `post_widget_chat`
**Finding:** `run_agent_turn.apply_async(args=[job_id, agent_id, body.message, conversation_id])`. Must add `body.verified_session_token or ""` as 5th arg. **The token must NEVER be logged** (same constraint as `message` per T-04-03-05). [VERIFIED: read from codebase]

**File:** `apps/api/app/worker/tasks/runtime/agent.py` — `run_agent_turn`
**Finding:** Task signature: `(self, job_id, agent_id, message, conversation_id)`. Must add `verified_session_token: str = ""`. Must pass `verified_session_token=verified_session_token` to `build_tool_server`. [VERIFIED: read from codebase]

### 5. Audit / Pending-Confirmations Tables

**Files:** `apps/api/app/services/transactional/audit.py`, `apps/api/alembic/versions/0014_transactional_substrate.py`
**Finding:** `tool_calls_audit` (control DB) already exists with `error TEXT NULL`. IDV denial writes an audit row via the existing `write_audit_row` function. No schema changes needed to the audit table. [VERIFIED: read from codebase]

### 6. Tenant-DB Migration Pattern

**File:** `apps/api/alembic_tenant/versions/0007_integration_credentials.py`
**Finding:** Uses `op.execute()` raw SQL with `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` guards. `customer_identities` must follow the same pattern as migration 0008. [VERIFIED: read from codebase]

**Tenant DB current head:** 0007. Next migration: `0008_customer_identities.py`.
**Control DB current head:** 0016. **No control DB changes are needed for Phase 17.**

### 7. Existing Email Infrastructure

**File:** `apps/api/app/services/escalation.py`
**Finding:** `send_escalation_email` uses `smtplib.SMTP`, reads `settings.SMTP_HOST`, `settings.SMTP_PORT`, `settings.SMTP_FROM`, `settings.OWNER_EMAIL`, `settings.SMTP_USER`, `settings.SMTP_PASSWORD`. This exact pattern can be reused for email OTP delivery. [VERIFIED: read from codebase]

**File:** `apps/api/app/core/config.py`
**Finding:** `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD` all present as optional settings. New SMS settings needed: `SMS_PROVIDER`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` (for Twilio), or `AT_API_KEY`, `AT_USERNAME`, `AT_SENDER_ID` (for Africa's Talking). [VERIFIED: read from codebase]

### 8. Existing Session / Conversation Concepts

**Finding:** The widget session is currently identified by:
- `conversation_id` (UUID, persisted in `conversations` table in tenant DB)
- SDK session continuity via `sdk_session_id` in `conversations.metadata`
- Widget JWT (15-min HS256, identifies agent only, not the customer)

**There is no existing concept of a customer-facing identity in the widget session.** The OTP flow is a new customer identity layer, parallel to (not replacing) the conversation session. A customer might complete the OTP flow once per `session_expires_at` window and then continue multiple conversation turns with their verified session token. [VERIFIED: read from codebase]

---

## IDV-01: customer_identities Table Design

**Location:** Tenant DB (alembic_tenant migration 0008)

```sql
CREATE TABLE IF NOT EXISTS customer_identities (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id          TEXT NOT NULL,
    verified_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    verification_method  TEXT NOT NULL,          -- 'email' | 'sms'
    session_token_hash   TEXT NOT NULL,           -- SHA-256 hex of raw session token
    session_expires_at   TIMESTAMPTZ NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_customer_identities_external_id UNIQUE (external_id)
);

CREATE INDEX IF NOT EXISTS ix_customer_identities_token_hash
ON customer_identities (session_token_hash);

CREATE INDEX IF NOT EXISTS ix_customer_identities_expires_at
ON customer_identities (session_expires_at);
```

**Design decisions baked in:**

1. **UNIQUE on `external_id`** — one row per customer per tenant. Re-verification UPSERTs the token + expiry in place. Avoids unbounded row growth.
2. **Index on `session_token_hash`** — the enforcement query looks up by hash; must be indexed for O(1) lookup.
3. **No `agent_id` column** — a verified session for `alice@email.com` is valid across all agents in that tenant. The capability envelope's `requires_identity_verification` is per-skill/agent — the enforcement still gates per-agent, but the verified session itself is tenant-scoped. [ASSUMED — open decision, see Open Questions]
4. **`external_id` = delivery address** — for email OTP it is the email; for SMS OTP it is the phone number (E.164 format recommended). [ASSUMED — open decision]

---

## OTP Flow Design

### Request-Code Step

```
Input:   { external_id: str, method: "email"|"sms" }
Guards:
  1. JWT authentication (existing widget Bearer JWT)
  2. Per-external_id send rate limit (Redis): max 3 sends / 10 minutes
     key: otp_sendlimit:{agent_id}:{external_id.lower()} — TTL 600s, max 3 INCR
  3. Per-IP send rate limit (Redis): max 10 sends / minute [ASSUMED limit]
     key: otp_sendip:{ip}:{bucket_60s} — TTL 120s, max 10 INCR

Processing:
  1. code = f"{secrets.randbelow(1_000_000):06d}"      # 6 digits, 1M space
  2. code_hash = sha256(code.encode()).hexdigest()
  3. Redis SET otp:{agent_id}:{external_id.lower()}:{method}
             = { "hash": code_hash, "attempts": 0 }
             TTL = 600 (email) or 300 (SMS) seconds
  4. Deliver: email via SMTP | SMS via provider

Output: 204 No Content (code is never returned to caller)
```

### Verify-Code Step

```
Input:   { external_id: str, otp_code: str, method: "email"|"sms" }
Guards:
  1. JWT authentication
  2. Fetch Redis key — if absent: 400 "Code expired or not issued"
  3. Check attempts < OTP_MAX_ATTEMPTS (5) — if exceeded: 429 "Too many attempts"

Processing:
  1. Increment attempts in Redis value (atomic with GET then SET or use a
     Lua script / pipeline to avoid TOCTOU)
  2. submitted_hash = sha256(otp_code.encode()).hexdigest()
  3. if NOT hmac.compare_digest(stored_hash, submitted_hash):
       → 400 "Invalid code"
       (do NOT delete key — let them retry up to max attempts)
  4. If match:
       DELETE Redis key  (single-use consumption)
       raw_token = secrets.token_urlsafe(32)
       token_hash = sha256(raw_token.encode()).hexdigest()
       UPSERT customer_identities:
         ON CONFLICT (external_id) DO UPDATE SET
           verified_at = now(),
           verification_method = :method,
           session_token_hash = :token_hash,
           session_expires_at = now() + INTERVAL '1 hour',   [ASSUMED — configurable]
           updated_at = now()

Output: 200 { "verified_session_token": "<raw_token>" }
        — raw token returned ONCE; client must store it
```

**Concrete defaults (all configurable via settings):**

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| Code length | 6 digits | NIST 800-63B minimum; UX standard |
| Code space | 1,000,000 | 1M combinations; brute force blocked by attempt limit |
| Email code TTL | 600s (10 min) | Industry standard for email OTP |
| SMS code TTL | 300s (5 min) | SMS delivery is near-instant; shorter window reduces exposure |
| Max attempts | 5 | ASVS V2.7.6 L2 recommendation; balanced with UX |
| Resend limit | 3 / 10 min | Prevents SMS cost abuse |
| Verified session TTL | 3600s (1 hr) | High-risk action (refund); configurable per-envelope in Phase 18 |
| Session token length | 43 chars (secrets.token_urlsafe(32)) | 256 bits of entropy |

---

## IDV-04 and IDV-05: Enforcement Wiring

### Step 2.5 in `_execute_transactional_tool`

The new step must be inserted in `apps/api/app/services/transactional/tools.py` in `_execute_transactional_tool`, between Step 2 (capability check) and Step 3 (idempotency reservation):

```python
# -------------------------------------------------------- 2.5 IDV gate (IDV-05)
# Reads requires_identity_verification from the capability snapshot (already
# fetched in Step 2 — no additional DB call for the envelope).
# Checks the verified session token from ContextVar against the tenant DB.
# This runs BEFORE reserve_idempotency so the idempotency slot is NOT consumed
# on a rejected unverified call (customer can retry with same idempotency_key
# after completing verification).
if snapshot.get("requires_identity_verification", False):
    vst = _verified_session_token_var.get()
    if not vst:
        # No session token presented — prompt customer to verify
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
    # Hash the token and look up in the tenant DB
    from app.services.identity_service import check_verified_session  # lazy import — avoids circular
    session_valid = await check_verified_session(agent_id, vst, conn_str)
    if not session_valid:
        await write_audit_row(..., error="identity_verification.invalid_or_expired")
        return {
            "content": [{"type": "text", "text": (
                "Identity verification required or session expired. "
                "Please verify your identity again to proceed."
            )}],
            "is_error": True,
        }
# -------------------------------------------------------- 3. Reserve idempotency
```

### ContextVar addition in `agent_tools.py`

```python
# Phase 17: per-task verified session token (IDV-05)
# Empty string default = no verified session (all non-IDV tool calls pass through)
_verified_session_token_var: ContextVar[str] = ContextVar("verified_session_token", default="")
```

`build_tool_server` signature addition:
```python
def build_tool_server(
    ...,
    verified_session_token: str = "",  # NEW: Phase 17
) -> object:
    ...
    _verified_session_token_var.set(verified_session_token)  # NEW: Phase 17
```

### What the agent receives (IDV-05 contract)

When the IDV gate blocks a tool call, the agent SDK receives a standard `is_error: True` tool result. The agent will include this in its next turn and prompt the customer to verify their identity. The agent CANNOT bypass this check — it is enforced in deterministic Python before the tool executes.

The agent must never attempt to "tell" the server that the customer is verified. The verification token is a client credential that the customer themselves provides (not something the agent can fabricate).

---

## SMS Provider Design

### Platform-Level Secrets (NOT Tenant-Level)

SMS credentials are **platform-level** secrets (one account for all tenants), NOT per-tenant credentials managed via `integration_credentials`. Rationale:
- SMS sending is a platform service (like email escalation)
- Per-tenant SMS accounts are Phase 18+ scope
- Follows the same pattern as `SMTP_*` settings

New settings to add to `config.py`:

```python
# Phase 17: SMS OTP provider
# "twilio" (default) or "africastalking"
SMS_PROVIDER: str = "twilio"

# Twilio credentials (used when SMS_PROVIDER="twilio")
TWILIO_ACCOUNT_SID: str | None = None
TWILIO_AUTH_TOKEN: str | None = None
TWILIO_FROM_NUMBER: str | None = None    # E.164, e.g. "+15017122661"

# Africa's Talking credentials (used when SMS_PROVIDER="africastalking")
AT_API_KEY: str | None = None
AT_USERNAME: str | None = None
AT_SENDER_ID: str | None = None         # optional branded sender ID
```

**Provider selection at startup** (module-level in `identity_service.py`):

```python
def _get_sms_provider() -> SmsProvider:
    if settings.SMS_PROVIDER == "africastalking":
        if not settings.AT_API_KEY or not settings.AT_USERNAME:
            raise ConfigError("AT_API_KEY and AT_USERNAME required for africastalking")
        return AfricasTalkingProvider(settings.AT_API_KEY, settings.AT_USERNAME, ...)
    # default: twilio
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        # SMS OTP not configured — log warning and allow graceful degradation
        log.warning("sms_provider.not_configured", provider="twilio")
        return NullSmsProvider()  # logs warning + raises at call time if sms method used
    return TwilioSmsProvider(...)
```

---

## Common Pitfalls

### Pitfall 1: Querying `customer_identities` Against the Control DB

**What goes wrong:** `get_sync_db()` connects to the control DB. `customer_identities` is in the TENANT DB. Using `get_sync_db()` will produce "relation does not exist" errors.

**Why it happens:** The pattern difference between control-DB tables (capability_envelopes, tool_calls_audit, etc.) and tenant-DB tables (customer_identities, integration_credentials) is subtle. Both use psycopg2 but against different connection strings.

**How to avoid:** Use `conn_str` from `_conn_str_var` (the decrypted tenant connection string) for all `customer_identities` queries. Follow the `integration_credentials` lookup pattern in `credential_service.py` which uses psycopg2 + `conn_str` directly.

**Warning signs:** `psycopg2.errors.UndefinedTable: relation "customer_identities" does not exist`

### Pitfall 2: Consuming the Idempotency Slot on IDV Rejection

**What goes wrong:** If the IDV check is placed AFTER `reserve_idempotency` (Step 3), a rejected unverified call will reserve the idempotency slot. When the customer later presents a valid token and retries with the same `idempotency_key`, they get "args_mismatch" or the slot is already consumed, making it impossible to proceed.

**Why it happens:** The order of enforcement steps matters. IDV is a precondition for the action, not a post-reservation check.

**How to avoid:** IDV check MUST be Step 2.5 (before Step 3). The idempotency key is conceptually "this action, once identity is verified." The key should be reusable after successful IDV.

### Pitfall 3: SMS Flooding via OTP Send Endpoint

**What goes wrong:** Without per-external_id rate limiting, an attacker submits thousands of phone numbers, each receiving an OTP SMS. This incurs real-money SMS costs and potentially DDoS's third-party carriers.

**Why it happens:** OTP send endpoints are unauthenticated (or lightly authenticated) by design — the customer is "pre-auth" at this point.

**How to avoid:** Three-layer rate limiting: (1) per-IP rate limit on OTP send endpoint (max 10/min per IP), (2) per-external_id limit (max 3 OTPs per 10 min), (3) widget JWT requirement (only active widget sessions can trigger OTPs). Layer (3) is already enforced by requiring the widget Bearer JWT on the identity endpoints.

### Pitfall 4: Replay Attack Via Undeleted OTP Code

**What goes wrong:** If the OTP Redis key is NOT deleted immediately on first successful verify, a second verification attempt with the same code succeeds. An attacker who intercepts the code (e.g., SIM-swap) can use it again.

**Why it happens:** Developers sometimes delete the key after returning the response, but the response + deletion are not atomic. Prefer: delete key FIRST, then issue session token.

**How to avoid:** In the verify handler: after code validation, DELETE the Redis key BEFORE generating the session token. If the session token generation fails, the customer must request a new OTP code (acceptable — codes are short-lived and cheap to regenerate).

### Pitfall 5: Using `==` Instead of `hmac.compare_digest` for Code Comparison

**What goes wrong:** Naive `==` comparison on hex strings short-circuits on the first differing byte, leaking information about how many prefix bytes match. An attacker can use this to narrow the search space.

**Why it happens:** Developers familiar with string comparison don't think about timing side-channels for hex-encoded hashes.

**How to avoid:** Always use `hmac.compare_digest(stored_hash, submitted_hash)` for both OTP code comparison and session token hash comparison.

### Pitfall 6: `external_id` Case Sensitivity

**What goes wrong:** `alice@email.com` and `Alice@Email.Com` are the same email but produce different lookup results if stored with mixed case.

**Why it happens:** No normalization before storage/lookup.

**How to avoid:** Lowercase `external_id` before storing in Redis (OTP challenge) and before querying `customer_identities`. For phone numbers, normalize to E.164 format (`+27821234567` not `0821234567`).

### Pitfall 7: Circular Import Between `tools.py` and `identity_service.py`

**What goes wrong:** If `identity_service.py` imports from `tools.py` at module level AND `tools.py` imports from `identity_service.py` at module level, a circular import error occurs at startup.

**Why it happens:** The transactional module already has a documented circular import issue between `tools.py` and `agent_tools.py` (resolved with lazy import in function body — noted in `tools.py` docstring).

**How to avoid:** Import `check_verified_session` from `identity_service.py` inside the function body of `_execute_transactional_tool` (lazy import pattern), not at module level. This mirrors the existing `from app.services.agent_tools import _agent_id_var, _conn_str_var` lazy import in the same function.

### Pitfall 8: Africa's Talking PyPI Package Name

**What goes wrong:** Installing `africa-talking` (with hyphen) from PyPI — this package does NOT exist on PyPI. It is `africastalking` (no hyphen, no space).

**Why it happens:** The company name contains a space; the PyPI package name uses no separator.

**How to avoid:** Always `pip install africastalking` (confirmed on PyPI as of 2026-07-01).

---

## Threat Model Surface (IDV Attack Vectors)

| Threat | STRIDE | Surface | Mitigation |
|--------|--------|---------|-----------|
| OTP brute force | Elevation of Privilege | Verify endpoint | 5-attempt lockout; 6-digit code (1M space); short TTL (5-10 min) |
| SMS flooding / cost abuse | Denial of Service | Request endpoint | 3 OTPs/10min per external_id; 10 OTPs/min per IP; widget JWT required |
| SIM-swap (SMS OTP intercept) | Spoofing | SMS channel | Inherit: accept as risk for this phase; note in deploy docs. Email OTP is safer for high-value actions |
| OTP replay | Repudiation | Verify endpoint | Single-use: Redis key deleted on first successful verify |
| Session token replay (theft) | Elevation of Privilege | Tool dispatcher | `session_expires_at` checked server-side; token hash stored (never raw token in DB) |
| Session fixation | Elevation of Privilege | Verify endpoint | Token generated server-side after successful verify; client cannot inject its own token |
| IDOR — cross-tenant session reuse | Elevation of Privilege | Tool dispatcher | `customer_identities` is in the tenant DB (isolated per tenant); sessions cannot cross tenant boundaries |
| Timing attack on OTP comparison | Information Disclosure | Verify endpoint | `hmac.compare_digest` prevents timing side-channel |
| Timing attack on session lookup | Information Disclosure | Tool dispatcher | DB lookup returns hit/miss only; no early-exit path leaks token prefix |
| Code enumeration (error oracles) | Information Disclosure | Verify endpoint | Same 400 response for "expired" and "wrong code" after key deletion — do not distinguish |
| Agent prose bypass (IDV-05) | Elevation of Privilege | Agent turn | Enforcement is in `_execute_transactional_tool` (deterministic Python); agent prose is irrelevant |

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Yes | OTP 6-digit, 5-attempt limit, TTL, single-use |
| V2.7 OTP Authenticators | Yes | ASVS V2.7.4 (expiry), V2.7.5 (single-use), V2.7.6 (rate limit) |
| V3 Session Management | Yes | Opaque token, server-side expiry, hash-stored |
| V4 Access Control | Yes | Per-skill IDV enforcement; fail-closed on missing session |
| V5 Input Validation | Yes | `external_id` normalized; OTP code is numeric only (Pydantic `constr(pattern=r'^\d{6}$')`) |
| V6 Cryptography | Yes | `secrets` module for generation; SHA-256 for hashing; `hmac.compare_digest` |
| V11 Business Logic | Yes | ASVS V11.1 (transaction authorization time window) |

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing in `pyproject.toml` dev dependencies) |
| Config file | `apps/api/pytest.ini` (existing) |
| Quick run command | `python -m pytest tests/unit/test_identity_service.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| IDV-01 | `customer_identities` table created by migration 0008 | Integration (migration roundtrip) | `python -m pytest tests/integration/test_migrations.py -k "0008" -x` | ❌ Wave 0 |
| IDV-02a | OTP code generation is 6 digits, from `secrets` | Unit | `python -m pytest tests/unit/test_identity_service.py::test_otp_code_format -x` | ❌ Wave 0 |
| IDV-02b | OTP code stored as SHA-256 hash, not plaintext | Unit | `python -m pytest tests/unit/test_identity_service.py::test_otp_hash_not_plaintext -x` | ❌ Wave 0 |
| IDV-02c | OTP verify: wrong code → 400, attempt count incremented | Unit | `python -m pytest tests/unit/test_identity_service.py::test_otp_wrong_code -x` | ❌ Wave 0 |
| IDV-02d | OTP verify: expired code (TTL elapsed) → 400 | Unit | `python -m pytest tests/unit/test_identity_service.py::test_otp_expired -x` | ❌ Wave 0 |
| IDV-02e | OTP verify: correct code → session token issued + Redis key deleted | Unit | `python -m pytest tests/unit/test_identity_service.py::test_otp_verify_success -x` | ❌ Wave 0 |
| IDV-02f | OTP verify: 5th wrong attempt → lockout (429) | Unit | `python -m pytest tests/unit/test_identity_service.py::test_otp_lockout -x` | ❌ Wave 0 |
| IDV-02g | Session token hash stored in `customer_identities`, raw token not stored | Unit | `python -m pytest tests/unit/test_identity_service.py::test_session_token_hashed -x` | ❌ Wave 0 |
| IDV-02h | Session expires after configured TTL | Unit | `python -m pytest tests/unit/test_identity_service.py::test_session_expiry -x` | ❌ Wave 0 |
| IDV-03 | SMS OTP request calls provider.send (email path covered by IDV-02) | Unit (mock provider) | `python -m pytest tests/unit/test_identity_service.py::test_sms_provider_called -x` | ❌ Wave 0 |
| IDV-04 | `requires_identity_verification` read from capability snapshot | Unit | Covered by existing `test_capability_enforcement.py` (snapshot already includes field) | ✅ Existing |
| IDV-05a | Tool dispatcher blocks when `requires_identity_verification=True` and no session token | Unit | `python -m pytest tests/unit/test_transactional_tools.py::test_idv_blocks_without_session -x` | ❌ Wave 0 |
| IDV-05b | Tool dispatcher blocks when session token expired/invalid | Unit | `python -m pytest tests/unit/test_transactional_tools.py::test_idv_blocks_expired_session -x` | ❌ Wave 0 |
| IDV-05c | Tool dispatcher proceeds when valid session token present | Unit | `python -m pytest tests/unit/test_transactional_tools.py::test_idv_passes_with_valid_session -x` | ❌ Wave 0 |
| IDV-05d | IDV block writes audit row (AUD-01 symmetry) | Unit | `python -m pytest tests/unit/test_transactional_tools.py::test_idv_audit_row_written -x` | ❌ Wave 0 |
| IDV-05e | IDV check runs BEFORE `reserve_idempotency` (idempotency key reusable after IDV failure) | Unit | `python -m pytest tests/unit/test_transactional_tools.py::test_idv_before_idempotency -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `python -m pytest tests/unit/test_identity_service.py tests/unit/test_transactional_tools.py -x -q`
- **Per wave merge:** `python -m pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `apps/api/tests/unit/test_identity_service.py` — covers IDV-01 through IDV-03 + IDV-05 service-layer
- [ ] `apps/api/tests/unit/test_identity_routes.py` — covers HTTP endpoint behavior (rate limiting, JWT validation, 204/200/400/429 responses)
- [ ] Test fixtures: mock Redis for OTP challenge state; mock SMTP for email delivery; mock SMS provider

---

## Open Design Decisions (Planner Must Resolve)

These are design choices with no locked answer in CONTEXT.md. A recommended default is given for each.

### OD-1: Scope of `customer_identities` — Per-Tenant vs Per-Agent

**Question:** Does a verified session for `external_id='alice@email.com'` apply across ALL agents in the tenant, or only for the specific agent?

**Options:**
- **Per-tenant (recommended):** No `agent_id` column. Alice verifies once; all agents in the tenant accept her session. Simpler UX, fewer re-verifications.
- **Per-agent:** Add `agent_id UUID NOT NULL` column + change UNIQUE to `(agent_id, external_id)`. Each agent requires separate verification.

**Recommendation:** Per-tenant (no `agent_id`). The enforcement is still per-skill via `capability_envelopes.requires_identity_verification`. The session is scoped to the tenant DB (Neon project) so cross-tenant leakage is impossible.

### OD-2: SMS Provider Choice — Twilio vs Africa's Talking

**Question:** Which SMS provider to use for Phase 17?

**Options:**
- **Twilio 9.10.9 (recommended default):** Already installed on dev machine (9.10.0). Best documentation, widest global reach, battle-tested Python SDK. Twilio Verify adds managed code generation + fraud protection (alternative to self-managed OTP hash).
- **Africa's Talking 2.0.2:** Better for ZA production (direct carrier connections, lower per-SMS cost for SA numbers, ~$0.015/SMS vs Twilio's routing via international aggregators).

**Recommendation:** Twilio as default for Phase 17 (already in the Python environment). Add Africa's Talking as the configured alternative via `SMS_PROVIDER=africastalking`. Both are supported by the provider abstraction layer.

**Important:** If using Twilio Verify API (not raw SMS), the architecture changes: Twilio handles code generation, delivery, and verification. Self-managed OTP (recommended here) is simpler and provider-agnostic.

### OD-3: OTP Challenge State Storage — Redis vs DB Table

**Question:** Where to store the temporary OTP hash + attempt count + expiry?

**Options:**
- **Redis (recommended):** TTL-based automatic expiry. Zero cleanup task needed. Matches the existing rate-limit Redis pattern.
- **Separate `otp_pending` table in tenant DB:** Durable, survives Redis restarts (but OTPs should expire anyway). Requires a cleanup Celery beat task.

**Recommendation:** Redis. OTPs are intentionally ephemeral; Redis loss resets pending challenges (customer must request a new code, which is acceptable). This follows the codebase's principle: durable state → DB, ephemeral/rate state → Redis.

### OD-4: Verified Session TTL — Per-Envelope vs Global

**Question:** Should the verified session TTL be per-skill (envelope-configured) or a global default?

**Options:**
- **Global settings default (recommended for Phase 17):** `VERIFIED_SESSION_TTL_SECONDS = 3600` in config.py. Simple. Phase 18 can extend to per-envelope TTL.
- **Per-envelope:** Add `identity_verification_ttl_seconds INTEGER` to `capability_envelopes`. Full flexibility but more complex.

**Recommendation:** Global setting for Phase 17. Note in RESEARCH as a Phase 18 extension point.

### OD-5: `external_id` Definition

**Question:** Is `external_id` always the delivery address (email / E.164 phone number), or a separate customer identifier?

**Options:**
- **Delivery address as external_id (recommended):** Email OTP → external_id is the email. SMS OTP → external_id is the phone number. Simple: one field serves both identity and delivery.
- **Separate CRM customer ID + delivery address:** More complex; requires the widget to pass both. Out of scope for this phase.

**Recommendation:** Delivery address = external_id for Phase 17. For email OTP: validate as email format. For SMS OTP: validate as E.164 phone number format.

---

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| MD5/SHA-1 for token hashing | SHA-256 (current) | SHA-256 is collision-resistant and computationally appropriate for token hashing (not password hashing) |
| TOTP (time-based, authenticator app) | Short-lived OTP via email/SMS | TOTP requires pre-registration; email/SMS OTP has lower friction for customer-facing flows |
| JWT for verified sessions | Opaque tokens + server-side DB | JWTs cannot be revoked before expiry; opaque tokens allow immediate session invalidation |
| SMS-only OTP | Multi-channel (email + SMS) | Email is phishing-resistant compared to SIM-swap-vulnerable SMS; offering both is standard |

**Deprecated/outdated:**
- `pyotp` is for TOTP/HOTP (authenticator apps, RFC 4226/6238). It is NOT the right library for custom short-lived email/SMS OTP codes. Use `secrets.randbelow` + `hmac.compare_digest` instead.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11+ | `secrets.randbelow` | ✓ | 3.12.x (dev machine) | — |
| Redis (local) | OTP challenge state | ✓ | Already required by Celery | — |
| SMTP (local) | Email OTP delivery | ✗ (optional — local dev) | SMTP_HOST=None → log warning | Structlog warning only; tests mock SMTP |
| `twilio==9.10.0` | SMS OTP (Twilio path) | ✓ | 9.10.0 installed on dev machine | Skip SMS tests if TWILIO_ACCOUNT_SID not set |
| `africastalking==2.0.2` | SMS OTP (AT path) | ✗ | Not installed (optional) | Use Twilio provider |
| Twilio account + ACCOUNT_SID | SMS delivery | ✗ | Not configured (env var not set) | SMS OTP unavailable until configured; email OTP unaffected |
| Neon tenant DB (0007 migrated) | `customer_identities` table | ✓ | Head at 0007 (Phase 16 shipped) | — |

**Missing dependencies with no fallback:**
- None blocking — email OTP path works with zero new dependencies; SMS OTP requires provider credentials (not blocking for development)

---

## Project Constraints (from CLAUDE.md)

The following project-level constraints apply to this phase:

1. **No Docker.** All demo scripts and verification steps must target local processes: Redis (`redis-server`), PostgreSQL, FastAPI (`uvicorn`), Celery worker. No `docker-compose`.
2. **Connection strings never in Celery task args.** The `verified_session_token` is NOT a connection string — it may be passed as a task arg. However, it must never be logged (treat same as `message` per T-04-03-05).
3. **`acks_late=True` AND idempotency on every Celery task.** No new Celery tasks are introduced in Phase 17 (the existing `run_agent_turn` task is extended); existing guarantees are preserved.
4. **FastAPI never does long-running work inline.** The OTP request/verify routes are short (Redis + one DB write) and run synchronously in the FastAPI event loop — acceptable. No Celery dispatch needed for these.
5. **Langfuse v4 API only.** No Langfuse calls are needed in the identity flow (it is a deterministic gate, not an LLM judgment). No Langfuse dependency introduced.
6. **No pg_search / pgbm25.** Not relevant to this phase.
7. **Per-tenant Neon projects.** `customer_identities` goes in the tenant DB (per-tenant isolation). The migration runs via the existing `apply_migrations` Celery task pattern.
8. **Two Celery queues always present:** `pipeline` and `runtime`. No new queues introduced.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `customer_identities` is scoped per-tenant (no `agent_id` column) | IDV-01 table design, OD-1 | If per-agent is required, migration schema and UNIQUE constraint change |
| A2 | `external_id` = delivery address (email for email OTP, E.164 phone for SMS OTP) | OTP Flow Design, OD-5 | If external_id is a separate CRM ID, the request schema and tenant-DB lookup pattern change |
| A3 | Verified session TTL = 3600 seconds (1 hour) global default | OTP Flow Design | If per-envelope TTL is required from day 1, the `customer_identities` UPSERT query changes |
| A4 | Email OTP TTL = 600 seconds; SMS OTP TTL = 300 seconds | OTP Flow Design | Reasonable defaults; the planner should expose as settings |
| A5 | Twilio is recommended SMS default (already installed on dev machine) | SMS Provider Design, OD-2 | If Africa's Talking is preferred, settings defaults change but the abstraction layer remains unchanged |
| A6 | `verify_otp` atomically increments attempt count and checks in a single Redis pipeline | OTP Flow Design, Pitfall 4 | If Redis pipeline is not available, TOCTOU race exists; mitigated by short TTL |
| A7 | The lazy import pattern for `check_verified_session` (function-body import) avoids the circular import | IDV-05 enforcement code example | If `identity_service.py` has no imports from `tools.py`, a module-level import is simpler |
| A8 | `africastalking` PyPI package is the official Africa's Talking SDK | Package Legitimacy Audit | Confirmed via PyPI (github.com/AfricasTalkingLtd/africastalking-python source repo) but not verified via Context7 |

---

## Sources

### Primary (VERIFIED from codebase)

All code examples tagged `[VERIFIED: read from codebase]` were directly extracted from the live codebase in this research session:
- `apps/api/app/models/capability_envelope.py` — `requires_identity_verification` field confirmed
- `apps/api/app/services/transactional/tools.py` — 7-step dispatcher documented; Step 2.5 insertion point identified
- `apps/api/app/services/transactional/enforcement.py` — `check_capability_access` snapshot includes IDV field
- `apps/api/app/services/agent_tools.py` — ContextVar pattern + `build_tool_server` signature confirmed
- `apps/api/app/api/v1/widget.py` — `WidgetChatRequest` schema + `run_agent_turn.apply_async` args confirmed
- `apps/api/app/worker/tasks/runtime/agent.py` — task signature + `build_tool_server` call confirmed
- `apps/api/app/services/escalation.py` — SMTP pattern for reuse in email OTP delivery
- `apps/api/app/core/config.py` — existing `SMTP_*` settings confirmed
- `apps/api/alembic_tenant/versions/0007_integration_credentials.py` — tenant migration pattern (IF NOT EXISTS guards)
- `apps/api/alembic/versions/0014_transactional_substrate.py` — control DB schema; `capability_envelopes` DDL confirmed

### Secondary (MEDIUM confidence — confirmed via pip + WebSearch)

- `twilio 9.10.9` on PyPI — confirmed `pip index versions twilio`; source repo `github.com/twilio/twilio-python/`
- `africastalking 2.0.2` on PyPI — confirmed `pip index versions africastalking`; source repo `github.com/AfricasTalkingLtd/africastalking-python`
- ASVS 4.0 V2.7 OTP requirements (attempt limits, TTL, single-use) — referenced from OWASP ASVS GitHub
- OTP flow best practices (6-digit, `secrets.randbelow`, `hmac.compare_digest`, single-use deletion) — WebSearch corroborated

### Tertiary (LOW confidence — WebSearch + training knowledge)

- Africa's Talking ZA deliverability and pricing claims
- Twilio Verify API pricing ($0.05/verification)
- OTP fraud statistics (SMS pumping cost estimates)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages needed for email path; twilio confirmed installed
- Architecture: HIGH — enforcement point precisely identified from codebase read
- OTP security parameters: MEDIUM — NIST/ASVS referenced but ASVS doc not read line-by-line
- SMS provider details: LOW — provider pricing/deliverability data is from web search

**Research date:** 2026-07-01
**Valid until:** 2026-07-31 (stable domain; OTP security standards change slowly)
