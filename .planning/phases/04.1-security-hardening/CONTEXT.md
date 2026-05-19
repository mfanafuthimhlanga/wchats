# Phase 4.1: Cross-Phase Security Hardening — Context

**Gathered:** 2026-05-18
**Status:** Ready for planning
**Source:** CROSS-PHASE-SECURITY-REVIEW.md — emergent threat analysis across Phases 01–04
**Trigger:** 8 compound threats identified when Phase 04 accepted risks were analysed in combination with earlier-phase accepted risks

---

<domain>
## Phase Boundary

Phase 4.1 closes all 8 emergent threats surfaced by the cross-phase security review. These threats did not exist in any single phase — they arise from the composition of previously-accepted risks across Phases 01–04. Every finding is code-level or configuration-level; no new architecture is introduced.

**What this phase fixes (all 8 findings — all are in scope):**

| Finding | Severity | Title |
|---------|----------|-------|
| F1 | CRITICAL | Column-name f-string SQL injection in `agent_tools.py` via indirect prompt injection |
| F2 | CRITICAL | Public JWT mint endpoint has no per-IP rate limit → unlimited JWT harvest → cost bomb + user lockout |
| F3 | HIGH | Indirect prompt injection → `escalate_to_human` spam → no per-conversation escalation cap |
| F4 | CRITICAL | Budget cap is per-conversation only → no tenant daily ceiling → unbounded Anthropic spend |
| F5 | HIGH | Redis broker now carries customer message text (PII); original "no PII in Redis" premise invalidated |
| F6 | MEDIUM | Soul fields sanitiser not applied at admit-time; Gatekeeper relied on but not yet shipped |
| F7 | MEDIUM | `filters` still no-op in M4 despite AR-03-07 promising "enforced in M4+"; latent regression risk |
| F8 | MEDIUM | Public widget SSE endpoint has no concurrent connection cap → connection exhaustion DoS |

**This phase does NOT include:**
- M5 Gatekeeper (still scheduled for M5)
- Rate limiting on non-widget API endpoints (deferred to M8/M10 per original acceptance)
- Redis mTLS (production hardening deferred to M10; F5 fix is config + documentation)
- Ragas evals, red team — M6/M7
- Any new agent tools or retrieval changes

</domain>

---

<decisions>
## Implementation Decisions

All decisions below are LOCKED. Read before planning.

---

### F1 — Column-Name SQL Injection Fix (CRITICAL)
**File:** `apps/api/app/services/agent_tools.py`
**Location of bug:** Line 225 — `where_clauses.append(f"{col} = %s")` where `col` is LLM-supplied

#### Fix

Add a per-table column allowlist. Check BEFORE any SQL assembly. Use `psycopg2.sql.Identifier` for belt-and-suspenders.

```python
_ALLOWED_FILTER_COLUMNS: dict[str, frozenset[str]] = {
    "chunks":         frozenset({"id", "document_id", "section", "chunk_order"}),
    "documents":      frozenset({"id", "name", "parse_status", "source_uri"}),
    "chunk_metadata": frozenset({"chunk_id", "entity_type", "entity_value"}),
}

def _validate_filter_columns(table: str, filters: dict) -> list[str]:
    """Returns list of rejected column names. Empty = all OK."""
    allowed = _ALLOWED_FILTER_COLUMNS.get(table, frozenset())
    return [col for col in filters if col not in allowed]
```

In the `lookup_structured` tool handler, before building the WHERE clause:

```python
rejected = _validate_filter_columns(table, filters)
if rejected:
    log.warning("lookup_structured.column_rejected", table=table, rejected=rejected)
    return {"content": [{"type": "text", "text": f"Column(s) {rejected!r} are not allowed for table '{table}'."}], "is_error": True}

from psycopg2 import sql as pgsql
for col, val in filters.items():
    where_clauses.append(pgsql.SQL("{} = %s").format(pgsql.Identifier(col)))
    params.append(val)
```

This is now a two-layer defence: Python allowlist check (fast, logged) + `psycopg2.sql.Identifier` quoting (structural).

**Register as T-04-02-06** in `04-SECURITY.md` with disposition `mitigate` and status `closed`.

#### Test
Add to `apps/api/tests/services/test_agent_tools.py`:
- `test_lookup_structured_rejects_unknown_column` — asserts `is_error=True` and no DB call on unknown column
- `test_lookup_structured_allows_known_columns` — asserts query succeeds for each entry in `_ALLOWED_FILTER_COLUMNS`
- `test_lookup_structured_sql_identifier_quoting` — asserts `psycopg2.sql.Identifier` is used in the assembled query (inspect the `mogrify` output)

---

### F2 — Per-IP Rate Limit on JWT Mint Endpoint (CRITICAL)
**File:** `apps/api/app/api/v1/widget.py`
**Location of bug:** `GET /widget/{agent_id}/config` handler — no per-IP throttle

#### Fix

Add a second Redis INCR rate check in the `GET /widget/{agent_id}/config` handler, keyed on client IP:

```python
async def _check_config_rate_limit(agent_id: str, client_ip: str, redis: Redis) -> None:
    bucket = int(time.time()) // 60
    key = f"rate:config:{client_ip}:{bucket}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 120)
    if count > 10:
        raise HTTPException(
            status_code=429,
            detail="Too many config requests",
            headers={"Retry-After": "60"},
        )
```

Call this at the top of the config handler before any DB access. Extract client IP via `request.client.host` (FastAPI standard).

**Note on X-Forwarded-For:** In local dev there is no reverse proxy. Log a debug warning if `X-Forwarded-For` is present (trust only in production behind known proxy). For M4.1, trust `request.client.host` directly. Production proxy config is M8 scope.

#### Test
Add to `apps/api/tests/api/v1/test_widget.py`:
- `test_widget_config_rate_limited_by_ip` — send 11 requests from same mock IP; assert 11th returns 429
- `test_widget_config_different_ips_not_affected` — send 10 from IP-A, 1 from IP-B; IP-B gets 200

---

### F3 — One-Escalation-Per-Conversation Enforcement (HIGH)
**File:** `apps/api/app/services/agent_tools.py`
**Location of bug:** `escalate_to_human_tool` — no duplicate escalation guard; `reason`/`context` are unvalidated

#### Fix

**Guard 1: SQL idempotency** — In `_mark_conversation_escalated` (the psycopg2 UPDATE that writes escalation to `conversations.metadata`), add a conditional:

```python
# Only update if not already escalated
cur.execute(
    """
    UPDATE conversations
    SET metadata = jsonb_set(metadata, '{escalated}', 'true')
    WHERE id = %s AND agent_id = %s
      AND (metadata->>'escalated') IS DISTINCT FROM 'true'
    """,
    (conversation_id, agent_id),
)
if cur.rowcount == 0:
    log.info("escalate_to_human.already_escalated", conversation_id=conversation_id)
    return {"already_escalated": True}
```

If already escalated, return early without calling `_notify_fn`.

**Guard 2: Sanitise `reason` and `context`** — Apply length cap and strip characters that would allow newline injection into notification templates:

```python
import re
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

def _sanitise_escalation_field(value: str, max_len: int = 500) -> str:
    return _CONTROL_CHAR_RE.sub(" ", value[:max_len]).strip()

reason = _sanitise_escalation_field(args.get("reason", ""))
context = _sanitise_escalation_field(args.get("context", ""))
```

**Guard 3: Notification prefix** — Prefix escalation notification payloads with `[AGENT-DETECTED — UNVERIFIED]` before sending to `_notify_fn`. This surfaces in email/Slack bodies in M5+.

#### Test
Add to `apps/api/tests/services/test_agent_tools.py`:
- `test_escalate_to_human_idempotent` — second call on already-escalated conversation returns `already_escalated=True`, `_notify_fn` called once only
- `test_escalate_to_human_sanitises_reason` — control chars stripped from reason field
- `test_escalate_to_human_reason_truncated_at_500` — reason longer than 500 chars is truncated

---

### F4 — Tenant Daily Budget Ceiling (CRITICAL)
**File:** `apps/api/app/api/v1/widget.py`, `apps/api/app/core/config.py`, new Alembic migration

#### Fix

**Step 1: Settings field** — Add to `apps/api/app/core/config.py`:
```python
TENANT_DAILY_BUDGET_USD: float = 5.0  # per-tenant daily Anthropic spend ceiling
```

Add to `.env.example`.

**Step 2: Control DB column** — New Alembic migration `0006_tenant_daily_budget.py`:
```sql
ALTER TABLE tenants ADD COLUMN daily_budget_usd FLOAT NOT NULL DEFAULT 5.0;
```

This column is read by the widget dispatch; admin UI management is M5+ scope.

**Step 3: Redis budget tracker** — In `apps/api/app/services/budget.py` (new file):
```python
import time
from redis.asyncio import Redis

async def check_and_increment_budget(
    tenant_id: str, cost_usd: float, redis: Redis, ceiling_usd: float
) -> bool:
    """Returns True if spend is within ceiling, False if ceiling exceeded."""
    key = f"budget:{tenant_id}:{_today()}"
    # Use a pipeline for atomic check+increment
    async with redis.pipeline(transaction=True) as pipe:
        await pipe.get(key)
        await pipe.execute()
    # Read current then conditionally increment
    current = float(await redis.get(key) or 0.0)
    if current >= ceiling_usd:
        return False
    await redis.incrbyfloat(key, cost_usd)
    await redis.expire(key, 86400)  # 24h TTL
    return True

def _today() -> str:
    return time.strftime("%Y-%m-%d")
```

**Step 4: Widget dispatch guard** — In `POST /widget/{agent_id}/chat` handler, BEFORE `run_agent_turn.apply_async`:

```python
# Estimated cost per turn (conservative upper bound for Haiku 4.5)
ESTIMATED_TURN_COST_USD = 0.01

budget_ok = await check_and_increment_budget(
    str(agent.tenant_id),
    ESTIMATED_TURN_COST_USD,
    redis,
    settings.TENANT_DAILY_BUDGET_USD,
)
if not budget_ok:
    raise HTTPException(
        status_code=429,
        detail="Daily usage limit reached. Please try again tomorrow.",
        headers={"Retry-After": "3600"},
    )
```

**Scope note:** The column `tenants.daily_budget_usd` is not wired to the Settings field — the Settings field is the system-wide default; the column will hold per-tenant overrides once an admin UI for it is built in M5+. For M4.1, the widget uses `settings.TENANT_DAILY_BUDGET_USD` (the global default) for all tenants.

#### Test
Add to `apps/api/tests/services/test_budget.py` (new file):
- `test_check_and_increment_budget_allows_within_ceiling`
- `test_check_and_increment_budget_blocks_at_ceiling`
- `test_check_and_increment_budget_ttl_set_on_first_write`

Add to `apps/api/tests/api/v1/test_widget.py`:
- `test_widget_chat_returns_429_when_budget_exhausted` — mock Redis to return ceiling value; assert 429

---

### F5 — Redis Broker Trust Documentation + Configuration (HIGH)
**Files:** `apps/api/app/worker/celery_app.py`, `.planning/phases/01-control-plane-skeleton/01-SECURITY.md`, `.planning/phases/04-reasoning-engine-widget/04-SECURITY.md`

This finding is **documentation + configuration**, not new code logic.

#### Fix

**Step 1: Celery result expiry** — In `apps/api/app/worker/celery_app.py`, add:
```python
celery_app.conf.result_expires = 300  # task results purged after 5 minutes
```

**Step 2: Disable Redis RDB on the broker** — Add to `apps/api/.env.example`:
```ini
# Redis broker — RDB snapshots disabled because broker carries customer message text
# Set this in redis.conf: save "" or start redis with --save ""
REDIS_SAVE_DISABLED=true  # reminder only — not read by app; set in redis.conf
```

Add a note to the local dev setup in `.planning/phases/04-1-security-hardening/LOCAL-DEV-REDIS.md` (new file, single page) with the `redis-server --save ""` command for local dev and a comment explaining why.

**Step 3: Update AR-06 in `01-SECURITY.md`** — Append a Phase 04-aware rationale addendum to AR-06:

```
AR-06 UPDATE (2026-05-18, Phase 4.1):
Phase 04 changed the content traversing the Redis broker. Task args now include
`body.message` (customer-supplied text, up to 2000 chars) from anonymous widget callers.
The original premise "Redis carries UUID identifiers only, no PII" no longer holds.
Revised acceptance: mTLS deferred to M10 production hardening; mitigating controls
added — (a) Celery result_expires=300 (5 min), (b) Redis RDB disabled on broker,
(c) body.message documented as sensitive in Phase 04 trust boundary table.
```

**Step 4: Update Phase 04 trust boundary table** — Add row to `04-SECURITY.md` trust boundaries:

```
| Widget ↔ Celery (via Redis) | POST /widget/.../chat body.message dispatched via apply_async | Customer message text (sensitive — up to 2000 chars, from anonymous callers) |
```

**Step 5: Update AR-02 in `01-SECURITY.md`** — Append:

```
AR-02 UPDATE (2026-05-18, Phase 4.1):
Phase 04 SSE now streams agent response text through Redis pub/sub (not just job metadata).
Agent responses may contain customer-query context (PII risk). The original premise
"M1 emit() payloads contain job metadata only — no PII" is no longer universally true.
Revised acceptance: Redis pub/sub carries agent responses in prod; mTLS deferred to M10.
Mitigating control: Redis RDB disabled on broker (Phase 4.1).
```

#### No new tests required
This fix is documentation + configuration only. Validate by checking `celery_app.conf.result_expires` in a unit test assertion (one line in `test_celery_app.py` if it exists).

---

### F6 — Soul Field Sanitisation at Admit-Time (MEDIUM)
**File:** `apps/api/app/schemas/agent.py` (Pydantic validator on `AgentSoulUpdate`)

#### Fix

Re-use `sanitize_chunk_text` from `apps/api/app/services/sanitize.py` (already imports cleanly). Add a Pydantic field validator to `AgentSoulUpdate`:

```python
from pydantic import field_validator
from app.services.sanitize import sanitize_chunk_text

class AgentSoulUpdate(BaseModel):
    soul_voice: str | None = None
    soul_do_list: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = None
    soul_donot_list: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = None
    soul_role: str | None = None

    @field_validator("soul_voice", "soul_role", mode="before")
    @classmethod
    def sanitise_text_field(cls, v: str | None) -> str | None:
        return sanitize_chunk_text(v) if v is not None else None

    @field_validator("soul_do_list", "soul_donot_list", mode="before")
    @classmethod
    def sanitise_list_field(cls, v: list | None) -> list | None:
        if v is None:
            return None
        return [sanitize_chunk_text(item) for item in v]
```

**Note:** `sanitize_chunk_text` strips prompt injection markers (`System:`, `Human:`, `[INST]`, `Ignore previous`, etc.) — the same set that guards chunk text at ingestion time (Phase 02). Applying it to soul fields creates a consistent sanitisation layer at every text ingestion point.

**Update `04-SECURITY.md`** — Amend T-04-06-04 acceptance rationale to add:
```
Mitigating control added in Phase 4.1: soul fields now sanitised at PATCH /agents admit-time
via sanitize_chunk_text — removes known injection markers before storage. Residual risk:
novel injection patterns not in sanitiser regex; M5 Gatekeeper provides secondary detection.
```

#### Test
Add to `apps/api/tests/schemas/test_agent_schemas.py` (or nearest existing test file):
- `test_soul_voice_injection_stripped` — soul_voice containing `System: override` → stripped
- `test_soul_do_list_injection_stripped` — list item containing `[INST]` → stripped from item
- `test_soul_field_valid_values_pass_through` — normal soul content unchanged

---

### F7 — `filters` No-Op Annotation + Runtime Warning (MEDIUM)
**Files:** `apps/api/app/services/agent_tools.py`, `.planning/phases/03-hybrid-retrieval/03-SECURITY.md`

#### Fix

**Step 1: Runtime log warning** — In the `retrieve_tool` handler, after extracting `filters` from args:

```python
filters = args.get("filters", [])
if filters:
    log.warning(
        "retrieve_tool.filters_ignored",
        filter_count=len(filters),
        conversation_id=conversation_id,
        note="filters parameter is not yet enforced; upgrade to M5 allowlist before activation",
    )
# filters intentionally not applied — see AR-03-07 / TODO-RET-01
```

This makes any LLM-supplied filter value loudly visible in observability before it silently becomes load-bearing in a future change.

**Step 2: Update AR-03-07 in `03-SECURITY.md`** — Replace the current rationale with:

```
AR-03-07 UPDATE (2026-05-18, Phase 4.1):
filters field remains no-op as of M4. M4 did not add enforcement (TODO-RET-01).
M5 MUST add an allowlisted-column enforcement layer before activating filters.
Until then, `retrieve_tool` logs a WARNING for any LLM-supplied filter value.
Phase 4.1 added the runtime log warning to prevent silent regression.
```

#### No new tests required beyond log-assertion
Optionally add `test_retrieve_tool_logs_warning_on_unused_filters` to the existing retrieve tool tests.

---

### F8 — SSE Concurrent Connection Cap (MEDIUM)
**File:** `apps/api/app/api/v1/widget.py`

#### Fix

Add a per-`agent_id` concurrent SSE connection cap using Redis SETNX + expiry:

```python
_MAX_CONCURRENT_SSE_PER_AGENT = 50  # configurable via settings if needed

async def _acquire_sse_slot(agent_id: str, job_id: str, redis: Redis) -> bool:
    """Returns True if slot acquired, False if at capacity."""
    count_key = f"sse:count:{agent_id}"
    slot_key  = f"sse:slot:{agent_id}:{job_id}"
    # Atomic slot claim with 150s TTL (> agent.py asyncio.wait_for 30s + buffer)
    acquired = await redis.set(slot_key, "1", nx=True, ex=150)
    if not acquired:
        return False  # duplicate job_id
    count = await redis.incr(count_key)
    if count == 1:
        await redis.expire(count_key, 3600)
    if count > _MAX_CONCURRENT_SSE_PER_AGENT:
        # Over limit — release slot immediately
        await redis.delete(slot_key)
        await redis.decr(count_key)
        return False
    return True

async def _release_sse_slot(agent_id: str, job_id: str, redis: Redis) -> None:
    slot_key = f"sse:slot:{agent_id}:{job_id}"
    deleted = await redis.delete(slot_key)
    if deleted:
        await redis.decr(f"sse:count:{agent_id}")
```

Call `_acquire_sse_slot` at the top of the widget SSE event generator. Call `_release_sse_slot` in a `finally` block.

**Hard timeout** — The widget SSE generator should wrap the event loop in `asyncio.wait_for`:

```python
async def _widget_event_generator(job_id, agent_id, redis):
    try:
        async with asyncio.timeout(120):  # 120s hard cap (well above 30s agent task timeout)
            async for event in _poll_job_events(job_id, redis):
                yield event
    except asyncio.TimeoutError:
        yield "event: timeout\ndata: {}\n\n"
    finally:
        await _release_sse_slot(agent_id, job_id, redis)
```

**Note:** `asyncio.timeout` requires Python 3.11+. If Python 3.10, use `asyncio.wait_for` around the generator instead.

#### Test
Add to `apps/api/tests/api/v1/test_widget.py`:
- `test_sse_slot_acquired_and_released` — assert slot key set/deleted around stream
- `test_sse_returns_503_when_agent_at_capacity` — mock Redis count at `_MAX_CONCURRENT_SSE_PER_AGENT`; assert 503
- `test_sse_hard_timeout_fires` — mock Redis pub/sub that never sends TERMINAL_EVENT; assert generator terminates within timeout

---

### Security File Updates Summary

After all code fixes, update the security files:

| File | Action |
|------|--------|
| `04-SECURITY.md` | Register T-04-02-06 (column injection, mitigate, closed); amend T-04-06-04 with sanitiser control; add trust boundary row for widget message text |
| `01-SECURITY.md` | Append Phase 4.1 addenda to AR-02 and AR-06 |
| `03-SECURITY.md` | Update AR-03-07 with TODO-RET-01 and runtime warning control |
| `CROSS-PHASE-SECURITY-REVIEW.md` | Add "Status" column to Recommendations Table — mark each R-XX as CLOSED after fix |

</decisions>

---

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Security Context
- `.planning/phases/04-reasoning-engine-widget/CROSS-PHASE-SECURITY-REVIEW.md` — full emergent threat analysis (primary input for this phase)
- `.planning/phases/04-reasoning-engine-widget/04-SECURITY.md` — Phase 04 threat register (update after fixes)
- `.planning/phases/01-control-plane-skeleton/01-SECURITY.md` — AR-02, AR-06 to be annotated
- `.planning/phases/03-hybrid-retrieval/03-SECURITY.md` — AR-03-07 to be updated

### Code Under Modification
- `apps/api/app/services/agent_tools.py` — F1 (column allowlist), F3 (escalation idempotency + sanitisation)
- `apps/api/app/api/v1/widget.py` — F2 (config rate limit), F4 (budget guard at dispatch), F8 (SSE slot cap)
- `apps/api/app/core/config.py` — F4 (TENANT_DAILY_BUDGET_USD setting)
- `apps/api/app/schemas/agent.py` — F6 (soul field sanitiser validator)
- `apps/api/app/worker/celery_app.py` — F5 (result_expires=300)

### New Files
- `apps/api/app/services/budget.py` — F4 budget tracking service
- `apps/api/alembic/versions/0006_tenant_daily_budget.py` — F4 migration
- `.planning/phases/04-1-security-hardening/LOCAL-DEV-REDIS.md` — F5 Redis config note

### Existing Patterns to Reuse
- `apps/api/app/services/sanitize.py` — `sanitize_chunk_text` (F6 reuses this directly)
- `apps/api/app/api/v1/widget.py:252-262` — existing Redis INCR rate limit pattern (F2, F8 follow same shape)
- `apps/api/app/worker/tasks/pipeline/parse.py` — idempotency guard pattern (F3 follows `WHERE ... IS DISTINCT FROM` pattern)
- `apps/api/app/services/events.py` — emit() helper (no changes, reference only)

### CLAUDE.md Constraints
- `acks_late=True` AND idempotency on every Celery task (no new tasks in this phase — constraint applies to existing task modifications only)
- Connection strings never in Celery task args (no change — audit confirms no regression)
- No Docker — all services run locally (Redis config change is runtime flag, not Docker)
- FastAPI never does work inline — budget and rate checks are fast Redis ops, not long-running work; acceptable in route handlers

</canonical_refs>

---

<specifics>
## Specific Implementation Notes

### F1 — Why Two Layers (Allowlist + Identifier)

The Python allowlist is the primary control — it logs, returns early, and never touches psycopg2. `psycopg2.sql.Identifier` is a belt-and-suspenders layer that provides structural SQL quoting even if the allowlist is accidentally bypassed in a future code change. Both must be present.

### F2 — Why Per-IP, Not Per-JWT

A per-JWT rate limit provides zero protection because JWT mint is the endpoint being rate-limited. An attacker who can mint unlimited JWTs can also bypass a per-JWT limit. The check must be on the inbound IP before the JWT is issued.

### F4 — Estimated Cost Is Conservative

`ESTIMATED_TURN_COST_USD = 0.01` is a safe upper bound for a Haiku 4.5 turn (typically $0.001–$0.003 at current pricing). Overestimating cost means the ceiling is conservative (safe). Do not use a lower estimate without also implementing actual cost tracking via Anthropic usage headers.

### F4 — Budget Migration Is Control DB, Not Tenant DB

`0006_tenant_daily_budget.py` modifies the `tenants` table in the control DB (`apps/api/alembic/`). It does NOT modify any tenant DB. Confirm migration target before running.

### F5 — Redis RDB Cannot Be Disabled From App Code

`result_expires=300` is configurable from app code. RDB snapshots (`save ""`) must be set in `redis.conf` or via the `redis-server` command line. The fix is a documented operational requirement, not a code change. The app-side fix is `result_expires=300`.

### F7 — TODO-RET-01 Tracking

Create `TODO-RET-01` as an entry in `.planning/STATE.md` notes section: "F7 / TODO-RET-01: filters field in retrieve_tool must have allowlisted-column enforcement before being wired to LLM. Gate: M5 phase planning must resolve this before activating filters in retrieve_tool description."

### F8 — Python Version Check

Before implementing `asyncio.timeout(120)`, check `apps/api/pyproject.toml` for the Python version constraint. If `python >= "3.11"`, use `asyncio.timeout`. If `python >= "3.10"`, use `asyncio.wait_for(gen, timeout=120)` wrapped around the generator. Do not mix the two.

</specifics>

---

<deferred>
## Deferred (Out of Scope for Phase 4.1)

- Redis mTLS / TLS transport — production hardening, M10
- Rate limiting on non-widget endpoints (`POST /agents/{id}/query`, `POST /documents`) — M8/M10 per original acceptance
- `azp` claim validation for Clerk tokens (T-04-10-06) — unchanged; RS256 signature + exp treated as sufficient
- Red-team PDF fixture for F1 validation — M6 red-team phase
- `tenants.daily_budget_usd` admin UI management — M5+
- Full Gatekeeper deployment — M5
- TODO-RET-01 resolution (filters enforcement) — M5 planning
- Per-IP SSE cap at reverse-proxy layer — M8 production hardening

</deferred>

---

*Phase: 04-1-security-hardening*
*Context gathered: 2026-05-18 via CROSS-PHASE-SECURITY-REVIEW.md emergent threat analysis*
*Findings covered: F1 (CRITICAL), F2 (CRITICAL), F3 (HIGH), F4 (CRITICAL), F5 (HIGH), F6 (MEDIUM), F7 (MEDIUM), F8 (MEDIUM)*
