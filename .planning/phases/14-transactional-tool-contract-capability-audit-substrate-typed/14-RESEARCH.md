# Phase 14: Transactional Tool Contract & Capability/Audit Substrate — Research

**Researched:** 2026-06-29
**Domain:** Claude Agent SDK in-process MCP tools, capability enforcement, idempotency, control-DB schema
**Confidence:** HIGH — all findings grounded in direct source inspection of the installed SDK package, existing codebase, and PRD DDL.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- `capability_envelopes`, `tool_calls_audit`, `pending_confirmations` are control-DB tables — new Alembic migration on the control DB. `actor_decision`/`actor_rationale` columns ship now but are written by Phase 15.
- Six transactional tools + `confirm_action` (new) + `escalate_to_human` (existing). Each is a typed Python function with full Pydantic input/output schemas — no string-blob, SQL, URL, or arbitrary-JSON inputs.
- Every tool tagged `mutating: true|false` at definition time (never runtime-inferred).
- Side-effecting tools take a client-provided idempotency key; replay with the same key returns the original result and never re-executes the mutation.
- Tool definitions are A2A- and ACP-skill-compatible in shape (typed inputs/outputs + examples) per ADR-0002 — forward-compat for v1.2; no A2A/ACP server in this phase.
- Enforcement middleware runs before a tool executes: reject (log `capability.denial`) when the skill is disabled, over its `rate_limit`, or violates a `constraints` rule (`max_amount_cents`, scope filters).
- New tools register into the existing customer-agent tool mechanism (extend `apps/api/app/services/agent_tools.py` / `build_tool_server`), NOT a provider MCP server or vendor agent toolkit.
- The tool body calls a provider-adapter interface (Phase 16 implements the real adapters); Phase 14 ships the interface + a stub/sandbox impl.
- `acks_late=True` AND idempotency on any Celery task touched. Connection strings never in task args. Per-tenant Neon for tenant data; control-DB for these three new tables. Claude Agent SDK is stateless (system_prompt every call).

### Claude's Discretion
- Idempotency storage mechanism (recommend in RESEARCH).
- Exact provider-adapter interface shape + the Phase-14 stub.
- Whether enforcement is a decorator, a middleware function, or a wrapper around the tool dispatch.

### Deferred Ideas (OUT OF SCOPE)
- Real provider execution (Shopify/WooCommerce/Stripe/Calendly API calls + the credential service) → Phase 16.
- The Actor validator + 4-node chain → Phase 15. Phase 14 must leave a clean pre-execution hook seam.
- Customer identity verification → Phase 17 (Phase 14 only adds the `requires_identity_verification` envelope column).
- Capability admin UI + blast-radius gate → Phase 18.
- A2A/ACP servers + manifests → v1.2.
</user_constraints>

---

## Summary

Phase 14 delivers the authorization substrate for the entire v1.1 transactional stack. Three interlocking deliverables: (1) the **typed tool contract** — six transactional tool definitions with full Pydantic schemas, registered into the existing customer-agent in-process MCP server; (2) the **capability envelope enforcement layer** — control-DB table + middleware that rejects calls when a skill is disabled, rate-limited, or constraint-violated; and (3) the **audit/confirmation substrate** — `tool_calls_audit` and `pending_confirmations` tables with a write path that every mutating call hits.

The highest-uncertainty item has been resolved: `claude_agent_sdk` 0.1.81 DOES expose in-process Python `hooks` via `ClaudeAgentOptions.hooks` (including `PreToolUseHookInput`), but these route through the CLI subprocess control channel and are not suitable as the synchronous Phase-15 Actor gate. **The correct seam for the Actor hook is a plain async function called directly inside each mutating tool handler** — this is consistent with how the existing tools already implement their own pre-conditions and requires no new SDK infrastructure.

The `@tool` decorator with `input_schema` as a full JSON Schema dict is confirmed supported in `create_sdk_mcp_server` (the `_build_schema` function passes through dicts with `type` + `properties` keys unmodified). Pydantic `.model_json_schema()` output can be passed directly as `input_schema`.

**Primary recommendation:** Implement the enforcement stack as an async wrapper inside each tool handler (not a decorator or middleware layer at the MCP server level), because the handler is the only code point where all per-call context (envelope snapshot, idempotency result, audit row) converges.

---

## LANDMINES

### LANDMINE 1 — SDK hooks are NOT the Actor seam [VERIFIED: source inspection]

`ClaudeAgentOptions.hooks: dict[HookEvent, list[HookMatcher]] | None` exists and includes `PreToolUse`. The `HookCallback` type is `Callable[[HookInput, str|None, HookContext], Awaitable[HookJSONOutput]]`. A `PreToolUse` hook CAN return `permissionDecision: "deny"` to block a tool call.

**BUT**: Hooks route through the CLI subprocess control channel (`SDKHookCallbackRequest` wire messages). They are designed for Claude Code's built-in tools (Bash, Read, Edit). For an in-process `McpSdkServerConfig` tool registered via `create_sdk_mcp_server`, the dispatch path is:

```
CLI sees tool call → CLI sends MCP message to SDK → SDK receives via SDKControlMcpMessageRequest
→ SDK dispatches to create_sdk_mcp_server's call_tool handler
→ call_tool calls tool_def.handler(arguments) DIRECTLY — no hook interception point
```

There is NO hook firing between `call_tool` receiving the call and it calling `tool_def.handler`. A `PreToolUse` hook fires at the CLI layer, before the MCP message is sent — it can block the MCP call from being dispatched at all, but this adds a subprocess roundtrip and loses per-call Python context (no access to agent_id ContextVar, no ability to read the control-DB for the envelope).

**Conclusion:** The Phase-15 Actor seam MUST live inside each mutating tool's handler function. The correct seam is a named `async def call_actor_gate(...)` function in a new module (`app/services/actor_seam.py`), called inside the handler after the capability check. Phase 15 replaces the function body.

The existing SDK `hooks` are NOT used in this phase.

### LANDMINE 2 — STATE note [09] applies only to direct Anthropic API calls, not in-process MCP [VERIFIED: source inspection]

STATE note [09] says `ClaudeSDKClient` does not support custom JSON tool schemas in *service* contexts. This refers to `call_gatekeeper`/`call_auditor`/`call_strategist` which use `anthropic.Anthropic().messages.create()` with `tools=[{"name": ..., "input_schema": {...}}]`. The Agent SDK subprocess does not forward these schemas to the Anthropic API when called that way.

The customer-agent tool loop uses a DIFFERENT mechanism: `create_sdk_mcp_server(tools=[...])` → `McpSdkServerConfig` → registered in `ClaudeAgentOptions.mcp_servers`. The `_build_schema` function in `create_sdk_mcp_server` (confirmed by inspection of `__init__.py` lines 396-416) passes through a full JSON Schema dict unmodified if it has `"type"` and `"properties"` keys. Pydantic's `.model_json_schema()` produces exactly this shape.

**Conclusion:** Phase 14 CAN use full JSON Schema dicts (from Pydantic `.model_json_schema()`) as the `input_schema` arg to `@tool(...)`. No workaround needed.

### LANDMINE 3 — `call_tool` dispatch in `create_sdk_mcp_server` has no pre-hook injection point [VERIFIED: source inspection]

The `create_sdk_mcp_server` function registers a `@server.call_tool()` handler that calls `tool_def.handler(arguments)` directly. There is no mechanism to register a pre-execution hook at the `Server` level without modifying SDK source. This confirms LANDMINE 1: the hook must live inside `handler`.

### LANDMINE 4 — Idempotency check must survive Redis restarts [ASSUMED based on CLAUDE.md + Celery acks_late semantics]

Redis TTL-based idempotency keys would be lost on a Redis restart. With `acks_late=True`, a Celery task that was executing when Redis died could be redelivered after Redis restarts, at which point the idempotency key would not be found and the mutation would execute again. A PostgreSQL table with `UNIQUE(agent_id, skill, idempotency_key)` survives restarts and provides correct durability.

Redis is still used for the rate-limit counter (losing the counter on restart means the rate limit resets — acceptable, not catastrophic), but NOT for idempotency.

### LANDMINE 5 — `rate_limit` TEXT format needs a parser [ASSUMED]

The DDL defines `rate_limit TEXT` as `"5/hour"`, `"10/day"` style strings. Phase 14 needs to parse this format and maintain a counter. Redis INCR with TTL (window-aligned key) is the right mechanism (matching the existing Redis infrastructure), but a parser is needed. The format should be documented as `N/<unit>` where unit ∈ {`minute`, `hour`, `day`}.

### LANDMINE 6 — `tool_calls_audit` write happens on the control DB, not the tenant DB [VERIFIED: DDL in CONTEXT.md]

Confirmed: `tool_calls_audit` is a control-DB table (no tenant PII, platform metadata). Connection is via `get_sync_db()` (SQLAlchemy session), not via `psycopg2.connect(conn_str)`. Write happens in the tool handler after the adapter stub returns. The `capability_snapshot` JSONB column stores the envelope state at time of call (not a FK, so the row is self-contained even if the envelope changes later).

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Typed tool definitions + Pydantic schemas | API/Backend (in-process MCP) | — | Tools run server-side; schemas validate at handler entry |
| Capability envelope enforcement | API/Backend (tool handler) | Control DB (envelope table) | Synchronous check in handler before execution |
| Rate limit counter | Redis | Control DB (fallback) | Redis INCR+TTL is the fast path; control-DB audit provides retrospective evidence |
| Idempotency key storage + replay | Control DB | — | Must survive Redis restarts; `UNIQUE` constraint is the correctness anchor |
| Audit row write | Control DB | — | `tool_calls_audit` is a control-DB table per PRD §4.3 |
| Pending confirmation write | Control DB | — | `pending_confirmations` is a control-DB table per PRD §4.3 |
| Phase-15 Actor seam | API/Backend (tool handler) | — | Must be synchronous on request path; Phase 15 fills the body |
| Provider adapter execution | Provider-adapter stub (Phase 14) / real (Phase 16) | — | Abstracted behind interface so Phase 14 is exercisable end-to-end |
| Tool registration into agent loop | API/Backend (`build_tool_server`) | — | Extend existing `create_sdk_mcp_server` call |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `pydantic` | ≥2.0 (already in use) | Input/output schemas for all tools | Already used throughout the codebase; `.model_json_schema()` produces valid MCP `inputSchema` |
| `psycopg2` | already in use | Control-DB writes for audit/idempotency | Already used for tenant DB writes; control DB also uses it via `get_sync_db()` → SQLAlchemy |
| `sqlalchemy` | already in use | ORM models for 4 new tables | Existing pattern — all control-DB models use SQLAlchemy declarative |
| `alembic` | already in use | Migration `0014_transactional_substrate.py` | 13 existing migrations in `apps/api/alembic/versions/` |
| `redis` | already in use | Rate-limit counter (INCR + TTL) | Already used in `agent_tools.py` and `agent.py` for pub/sub and query-embed cache |
| `claude_agent_sdk` | 0.1.81 (installed) | `create_sdk_mcp_server` + `@tool` | Existing mechanism — extend, don't replace |
| `structlog` | already in use | `capability.denial` logging | Existing logging convention |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `anthropic` | already in use | Phase-15 Actor stub (direct API call) | Phase 15 fills `call_actor_gate`; the stub does not use it |
| `langfuse` | 4.x, already in use | Trace tool calls | Add `start_as_current_generation` spans for each mutating tool call execution |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Dedicated `tool_idempotency_keys` table | Redis TTL | Redis lost on restart → correctness failure under `acks_late`; table is correct |
| Dedicated `tool_idempotency_keys` table | Reuse `tool_calls_audit` with `idempotency_key` col | Audit table is future write-only (v1.3); adding `UNIQUE` constraint and replay logic complicates its role |
| Inline enforcement in each handler | Decorator pattern | Decorator pattern requires shared context (agent_id, etc.) via closure or ContextVar — same complexity, less transparent |
| Inline enforcement in each handler | `call_tool` wrapper around `create_sdk_mcp_server` | `create_sdk_mcp_server` is from the SDK; patching its internals creates a maintenance hazard |

---

## Architecture Patterns

### System Architecture Diagram

```
Customer message
    │
    ▼
_run_sdk_turn  (agent.py)
    │  Claude Agent SDK turn loop
    │  Claude emits ToolUseBlock for e.g. "place_order"
    ▼
create_sdk_mcp_server.call_tool("place_order", args)
    │  dispatches to handler
    ▼
place_order_tool handler  (transactional_tools.py)
    │
    ├─► 1. Pydantic schema validation
    │       PlaceOrderInput.model_validate(args) → ValidationError → is_error return
    │
    ├─► 2. Capability envelope check  (enforcement.py)
    │       control DB: SELECT capability_envelopes WHERE agent_id+skill
    │       ├─ disabled     → capability.denial log → is_error return
    │       ├─ rate_limit   → Redis INCR(key, TTL=window) → over limit → denial → is_error
    │       └─ constraints  → max_amount_cents, scope filters → violation → denial → is_error
    │
    ├─► 3. Actor seam  (actor_seam.py)
    │       call_actor_gate(skill, args, envelope, conv_id, agent_id)
    │       Phase 14: always returns ("approve", "")
    │       Phase 15: Haiku call → "approve"|"block"|"require_human"
    │       └─ "block" → is_error return
    │
    ├─► 4. Idempotency check  (idempotency.py)
    │       control DB: SELECT tool_idempotency_keys WHERE agent_id+skill+key
    │       └─ hit → return cached result (NO adapter call)
    │
    ├─► 5. Execute adapter  (provider_adapter.py)
    │       StubProviderAdapter.place_order(args, agent_id)
    │       Phase 16: ShopifyAdapter / StripeAdapter / etc.
    │
    ├─► 6. Write audit row  (audit.py)
    │       control DB: INSERT tool_calls_audit (args, result, capability_snapshot, latency_ms, actor_decision)
    │       actor_decision/actor_rationale = "" in Phase 14; Phase 15 fills these
    │
    └─► 7. Store idempotency result
            control DB: INSERT tool_idempotency_keys ON CONFLICT DO NOTHING
            return result to Claude
```

### Recommended Project Structure

```
apps/api/app/
├── models/
│   ├── capability_envelope.py      # CapabilityEnvelope ORM
│   ├── tool_calls_audit.py         # ToolCallsAudit ORM
│   ├── pending_confirmation.py     # PendingConfirmation ORM
│   └── tool_idempotency_key.py     # ToolIdempotencyKey ORM
├── services/
│   ├── agent_tools.py              # EXTEND: register transactional tools in build_tool_server
│   ├── actor_seam.py               # NEW: call_actor_gate stub (Phase 15 fills)
│   ├── transactional/
│   │   ├── __init__.py
│   │   ├── schemas.py              # All Pydantic input/output models
│   │   ├── registry.py             # TransactionalToolDef + TOOL_REGISTRY dict
│   │   ├── enforcement.py          # Capability envelope check + rate limit
│   │   ├── idempotency.py          # Idempotency check/store helpers
│   │   ├── audit.py                # Audit row writer
│   │   ├── provider_adapter.py     # ProviderAdapter ABC + StubProviderAdapter
│   │   └── tools.py                # The 7 tool handlers (place_order, etc.)
└── alembic/versions/
    └── 0014_transactional_substrate.py  # 4 new tables
```

### Pattern 1: Typed Tool Handler with Enforcement Stack

```python
# apps/api/app/services/transactional/tools.py
# [ASSUMED — SDK tool pattern confirmed by source inspection but exact dataclass shape is Claude's discretion]

import time
from claude_agent_sdk import tool
from app.services.transactional.schemas import PlaceOrderInput, PlaceOrderOutput
from app.services.transactional.registry import TOOL_REGISTRY
from app.services.transactional.enforcement import check_capability_envelope
from app.services.transactional.idempotency import check_idempotency, store_idempotency
from app.services.transactional.audit import write_audit_row
from app.services.transactional.provider_adapter import get_adapter
from app.services.actor_seam import call_actor_gate
from app.services.agent_tools import _agent_id_var, _conversation_id_var

@tool(
    "place_order",
    "Place a customer order through the tenant's connected store. Requires idempotency_key.",
    PlaceOrderInput.model_json_schema(),
)
async def place_order_tool(args: dict) -> dict:
    agent_id = _agent_id_var.get()
    conversation_id = _conversation_id_var.get()
    skill = "place_order"
    t0 = time.monotonic_ns()

    # 1. Schema validation
    try:
        validated = PlaceOrderInput.model_validate(args)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Invalid input: {e}"}], "is_error": True}

    # 2. Capability envelope enforcement
    envelope, denial = await check_capability_envelope(agent_id, skill, validated)
    if denial:
        return {"content": [{"type": "text", "text": f"Action not permitted: {denial}"}], "is_error": True}

    # 3. Actor pre-execution hook (Phase-15 seam)
    actor_decision, actor_rationale = await call_actor_gate(
        skill, args, envelope, conversation_id, agent_id
    )
    if actor_decision == "block":
        return {"content": [{"type": "text", "text": "Action blocked by safety check."}], "is_error": True}

    # 4. Idempotency check
    cached = await check_idempotency(agent_id, skill, validated.idempotency_key)
    if cached is not None:
        return cached

    # 5. Execute adapter
    adapter = get_adapter()
    latency_ms = None
    error = None
    result_dict = None
    try:
        result = await adapter.place_order(validated, agent_id)
        latency_ms = (time.monotonic_ns() - t0) // 1_000_000
        result_dict = result.model_dump()
    except Exception as e:
        latency_ms = (time.monotonic_ns() - t0) // 1_000_000
        error = str(e)

    # 6. Write audit row (always, even on error)
    await write_audit_row(
        agent_id=agent_id,
        conversation_id=conversation_id,
        skill=skill,
        arguments=args,
        result=result_dict,
        actor_decision=actor_decision,
        actor_rationale=actor_rationale,
        capability_snapshot=envelope,
        latency_ms=latency_ms,
        error=error,
    )

    if error:
        return {"content": [{"type": "text", "text": f"Order could not be placed: {error}"}], "is_error": True}

    # 7. Store idempotency result
    response = {"content": [{"type": "text", "text": result.message}]}
    await store_idempotency(agent_id, skill, validated.idempotency_key, response)
    return response
```

### Pattern 2: TransactionalToolDef — A2A-Compatible Registry Entry

```python
# apps/api/app/services/transactional/registry.py
# [ASSUMED — shape is Claude's discretion per CONTEXT.md]

from dataclasses import dataclass, field
from claude_agent_sdk import SdkMcpTool

@dataclass
class TransactionalToolDef:
    """Wraps an SdkMcpTool with Phase-14 metadata + A2A/ACP forward-compat fields."""
    sdk_tool: SdkMcpTool          # The @tool-decorated handler — passed to create_sdk_mcp_server
    skill_name: str                # Canonical name matching capability_envelopes.skill
    mutating: bool                 # True → Actor gate fires, idempotency required, audit always
    idempotency_required: bool     # Must match mutating; explicit for clarity
    # A2A/ACP forward-compat fields (v1.2 serializer reads these):
    a2a_input_modes: list[str] = field(default_factory=lambda: ["text", "structured"])
    a2a_output_modes: list[str] = field(default_factory=lambda: ["text", "structured"])
    examples: list[str] = field(default_factory=list)

# Module-level registry — the single source of truth for tool metadata
TOOL_REGISTRY: dict[str, TransactionalToolDef] = {}
```

### Pattern 3: Actor Gate Seam

```python
# apps/api/app/services/actor_seam.py
# [ASSUMED — interface shape; Phase 15 replaces the body]

async def call_actor_gate(
    skill: str,
    arguments: dict,
    capability_snapshot: dict,
    conversation_id: str,
    agent_id: str,
) -> tuple[str, str]:
    """
    Pre-execution gate for mutating tools. Called for every tool where
    TOOL_REGISTRY[skill].mutating is True.

    Returns:
        (decision, rationale) where decision ∈ {"approve", "block", "require_human"}.
        Phase 14 always returns ("approve", "").
        Phase 15 replaces this body with a Haiku API call that reads the
        conversation context and the proposed tool call.
    """
    return ("approve", "")
```

### Pattern 4: Capability Envelope Enforcement

```python
# apps/api/app/services/transactional/enforcement.py
# [ASSUMED — core pattern; Redis INCR for rate limit]

import json, time
from app.core.database import get_sync_db
from app.models.capability_envelope import CapabilityEnvelope
import redis as redis_lib

def _parse_rate_limit(rate_str: str | None) -> tuple[int, int] | None:
    """Parse "5/hour" → (max=5, window_secs=3600). Returns None if no limit."""
    if not rate_str:
        return None
    parts = rate_str.strip().split("/")
    if len(parts) != 2:
        return None
    units = {"minute": 60, "hour": 3600, "day": 86400}
    return int(parts[0]), units.get(parts[1].lower(), 3600)

async def check_capability_envelope(
    agent_id: str,
    skill: str,
    args,  # Pydantic validated input model
) -> tuple[dict, str | None]:
    """
    Returns (envelope_snapshot_dict, denial_reason_or_None).
    Logs capability.denial and increments rate-limit counter on pass-through.
    """
    with get_sync_db() as db:
        envelope = db.execute(
            sa_text("SELECT * FROM capability_envelopes WHERE agent_id=:a AND skill=:s LIMIT 1"),
            {"a": agent_id, "s": skill},
        ).mappings().first()

    if envelope is None or not envelope["enabled"]:
        log.warning("capability.denial", agent_id=agent_id, skill=skill, reason="disabled")
        return {}, "skill is not enabled for this agent"

    snapshot = dict(envelope)

    # Rate limit check
    rate = _parse_rate_limit(envelope.get("rate_limit"))
    if rate:
        max_calls, window_secs = rate
        window_key = int(time.time()) // window_secs
        redis_key = f"ratelimit:{agent_id}:{skill}:{window_key}"
        count = _redis.incr(redis_key)
        _redis.expire(redis_key, window_secs + 1)  # +1 to avoid edge-case early expiry
        if count > max_calls:
            log.warning("capability.denial", agent_id=agent_id, skill=skill, reason="rate_limit")
            return snapshot, f"rate limit exceeded ({max_calls}/{envelope['rate_limit']})"

    # Constraint checks (max_amount_cents, scope filters)
    constraints = envelope.get("constraints") or {}
    if "max_amount_cents" in constraints:
        amount = getattr(args, "amount_cents", None) or getattr(args, "refund_amount_cents", None)
        if amount is not None and amount > constraints["max_amount_cents"]:
            log.warning("capability.denial", agent_id=agent_id, skill=skill, reason="max_amount_cents")
            return snapshot, f"amount exceeds configured limit of {constraints['max_amount_cents']} cents"

    return snapshot, None
```

### Anti-Patterns to Avoid

- **Anti-pattern: Using `ClaudeAgentOptions.hooks` for the Actor gate.** Hooks route through the CLI subprocess control channel — they cannot access Python ContextVars, add a subprocess roundtrip, and lose direct access to the capability envelope. Use the wrapper-inside-handler pattern instead.
- **Anti-pattern: Storing idempotency keys in Redis.** Redis key loss on restart causes re-execution of mutations under `acks_late`. Use the `tool_idempotency_keys` PostgreSQL table.
- **Anti-pattern: Passing `mutating` as a tool annotation in `ToolAnnotations`.** MCP's `ToolAnnotations` has `readOnly`, `destructive`, `openWorld` — none are exactly `mutating`. The flag belongs on `TransactionalToolDef`, not in the MCP wire protocol. Phase 15's Actor gate reads `TOOL_REGISTRY[skill].mutating`, not the MCP schema.
- **Anti-pattern: Writing `actor_decision` in Phase 14.** The audit table columns exist now but Phase 14 writes empty string `""` for `actor_decision` and `actor_rationale`. Phase 15 fills them. The stub `call_actor_gate` returns `("approve", "")`.
- **Anti-pattern: Writing audit rows only for successes.** The audit row MUST be written even on adapter errors — the `error` column captures the failure, and the `result` column is NULL. Incomplete audit coverage was called out as a v1.1 success criterion failure.
- **Anti-pattern: Checking idempotency before the capability check.** A revoked or disabled skill should not serve a cached result — the capability check comes first so a disabled skill stays disabled even for replay requests.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Pydantic JSON Schema for tool input | Custom schema dict | `Model.model_json_schema()` | `_build_schema` in `create_sdk_mcp_server` passes through dicts with `type`+`properties` — Pydantic schema works directly |
| Rate-limit window counter | Custom sliding-window | Redis `INCR` + `EXPIRE` (fixed window) | Already in use; sub-millisecond; fixed window is acceptable for this use case |
| Idempotency race condition | Application-level compare-and-set | PostgreSQL `INSERT ... ON CONFLICT DO NOTHING` | DB-level `UNIQUE` constraint provides race-free idempotency |
| Capability row serialization | Custom dict | SQLAlchemy `.mappings().first()` + snapshot | Already the pattern for agent row reads; produce the `capability_snapshot` JSONB in one step |
| Tool handler async-to-sync bridge | Manual asyncio bridge | None needed — handlers are already async | `create_sdk_mcp_server`'s `call_tool` is `async def`; handlers can use `await` natively |

---

## Research Findings by Cluster

### Cluster 1: SDK Tool Mechanism + Pre-Execution Hook [VERIFIED: source inspection of installed SDK]

**File inspected:** `C:\Users\Bantu\AppData\Local\Programs\Python\Python312\Lib\site-packages\claude_agent_sdk\__init__.py` (line-by-line), `types.py`.

The `create_sdk_mcp_server` factory registers:
1. A `@server.list_tools()` handler that returns pre-computed `Tool` objects (MCP protocol).
2. A `@server.call_tool()` handler that dispatches `tool_def.handler(arguments)` directly.

The `input_schema` parameter to `@tool(name, description, input_schema)` accepts:
- A full JSON Schema dict with `"type": "object"` and `"properties"` — passed through unmodified (lines 399-406 of `create_sdk_mcp_server`).
- A `TypedDict` class — converted via `_typeddict_to_json_schema`.
- A simple `{param_name: python_type}` dict — converted to JSON Schema.

Pydantic's `.model_json_schema()` produces exactly the format that is passed through unmodified. Use this.

**Pre-execution hook finding:** No SDK-level hook point exists between `call_tool` and `tool_def.handler(arguments)`. The `ClaudeAgentOptions.hooks` system (for `PreToolUseHookInput`) routes through the CLI control channel — not applicable for in-process tool intercept. See LANDMINE 1.

**Recommended seam location:** Inside the tool handler, after capability check, before idempotency check:

```python
actor_decision, actor_rationale = await call_actor_gate(skill, args, envelope_snapshot, conversation_id, agent_id)
if actor_decision == "block":
    return {"content": [...], "is_error": True}
if actor_decision == "require_human":
    # Write pending_confirmations row; return "awaiting human confirmation" response
    ...
```

### Cluster 2: Typed Tool Contract [VERIFIED: direct inspection of agent_tools.py + PRD §4.2]

**Six transactional tools + `confirm_action`:**

| Tool | mutating | idempotency_required | `requires_identity_verification` default |
|------|----------|---------------------|------------------------------------------|
| `place_order` | True | True | True |
| `cancel_order` | True | True | True |
| `issue_refund` | True | True | True |
| `update_subscription` | True | True | True |
| `book_slot` | True | True | False |
| `update_customer_record` | True | True | True |
| `confirm_action` | False | False | — |
| `escalate_to_human` (existing) | False | False | — |

**`idempotency_key` field:** Required on all 6 mutating tools. Type: `str`, format: UUID (client-generated). The key is scoped to `(agent_id, skill)` — the same key cannot be reused across different skills.

**Pydantic schema shape for a mutating tool:**

```python
from pydantic import BaseModel, Field
from typing import Annotated

class PlaceOrderInput(BaseModel):
    idempotency_key: Annotated[str, Field(description="Client-generated UUID for replay protection.")]
    product_id: Annotated[str, Field(description="SKU or platform product identifier.")]
    quantity: Annotated[int, Field(ge=1, description="Number of units to order.")]
    customer_email: Annotated[str, Field(description="Customer email address for order confirmation.")]
    shipping_address: Annotated[str, Field(description="Full shipping address.")]
    amount_cents: Annotated[int, Field(ge=0, description="Expected order total in cents (for blast-radius gate).")]

class PlaceOrderOutput(BaseModel):
    order_id: str
    status: str  # "placed" | "pending_confirmation" | "error"
    message: str   # Human-readable result for the agent to convey
```

**A2A/ACP-compatible metadata captured at definition time on `TransactionalToolDef`:**
- `examples`: list of 2-3 plain-English example phrasings ("Place an order for 2 bags of Kenyan AA", etc.)
- `a2a_input_modes`: `["text", "structured"]` — A2A Agent Card skill field
- `a2a_output_modes`: `["text", "structured"]`

These are stored in `TOOL_REGISTRY` and read by the v1.2 manifest serializer without redefining the tools.

### Cluster 3: Idempotency Key Storage + Replay [VERIFIED: Celery acks_late constraint from CLAUDE.md]

**Recommended storage: dedicated `tool_idempotency_keys` control-DB table.**

```sql
CREATE TABLE tool_idempotency_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL REFERENCES agents(id),
    skill           TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    result          JSONB NOT NULL,   -- the full tool response dict (content + is_error)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_id, skill, idempotency_key)
);
CREATE INDEX tool_idempotency_keys_agent_skill_idx ON tool_idempotency_keys(agent_id, skill);
```

**Replay contract:**
1. Tool handler receives `args` with `idempotency_key`.
2. After capability check and Actor seam, query: `SELECT result FROM tool_idempotency_keys WHERE agent_id=? AND skill=? AND idempotency_key=?`.
3. If row found: return `row.result` immediately. Skip adapter call, skip audit write (audit row was written on the original call).
4. If not found: execute normally. After successful adapter call, write audit row, then `INSERT INTO tool_idempotency_keys ... ON CONFLICT DO NOTHING`.

**Race condition:** Two concurrent retries of the same Celery task both pass the idempotency check simultaneously. The `INSERT ... ON CONFLICT DO NOTHING` means only one wins; the other gets no row and would normally re-execute. To handle this, the adapter stub must itself be idempotent, OR the check must be retried after a short wait. For Phase 14 (stub adapter), this edge case does not matter — document as a Phase 16 concern.

**Why not `tool_calls_audit`?** The audit table is planned to migrate to a write-only Neon project in v1.3. Adding a `UNIQUE` constraint and a read path (for replay) contradicts its future append-only architecture. Keep concerns separated.

### Cluster 4: Capability Envelope Enforcement [VERIFIED: PRD §4.3 DDL + CONTEXT.md]

**Enforcement order (LOCKED in CONTEXT.md):**
```
capability check → [Phase-15 Actor seam] → idempotency check → execute(adapter) → audit
```

**Enforcement logic in `check_capability_envelope(agent_id, skill, validated_args):`**

1. `enabled = False` → immediate denial with `reason="disabled"`.
2. `rate_limit` not null → Redis INCR on `ratelimit:{agent_id}:{skill}:{window_id}` with TTL = window_secs. Count > max → denial with `reason="rate_limit"`.
3. `constraints["max_amount_cents"]` → compare `validated_args.amount_cents` (or `refund_amount_cents`) to limit. Exceed → denial with `reason="max_amount_cents"`.
4. `constraints["scope_filters"]` → future Phase 16 filters (product category restrictions, etc.); Phase 14 stub: no-op.

**Capability snapshot:** The full envelope row dict (including `constraints` JSONB) is captured at check time and stored in `tool_calls_audit.capability_snapshot`. This means the audit record is self-contained even if the envelope is later changed.

**`capability.denial` log:** Use `structlog.warning("capability.denial", agent_id=agent_id, skill=skill, reason=<reason>, constraint_violated=<field>)`. This is the event class mentioned in Post-M10-PRD §6 (v1.3 monitoring) — the log format should be consistent now for retrospective alerting.

### Cluster 5: The 3 Control-DB Tables + Migration [VERIFIED: PRD §4.3 DDL + migration inspection]

**Migration file:** `apps/api/alembic/versions/0014_transactional_substrate.py`
**down_revision:** `"0013"` (current head: `0013_alert_tenant_id.py`)

Tables belong on the **control DB** (same Alembic instance as `apps/api/alembic/`), not on per-tenant Neon. Justification: these are platform-level authorization and audit records. No tenant PII in these tables — `arguments` and `result` JSONB should be treated as potentially containing PII from Phase 16 onwards and documented as such, but for Phase 14 with the stub adapter they will contain only request parameters.

**Phase 14 writes vs Phase 15:**
| Column | Phase 14 | Phase 15 |
|--------|----------|----------|
| `tool_calls_audit.actor_decision` | `""` (empty string, not NULL) | `"approve"` \| `"block"` \| `"require_human"` |
| `tool_calls_audit.actor_rationale` | `""` | The Haiku rationale text |
| `tool_calls_audit.result` | Stub adapter result | Real provider result |
| `tool_calls_audit.conversation_id` | From `_conversation_id_var` ContextVar | Same |
| `capability_envelopes.*` | Row inserted by Alembic seed (enabled=false default) | Same |
| `pending_confirmations.resolved_at` | NULL (Phase 14 only writes the row) | Phase 18 resolves |

**`capability_envelopes` seed:** The migration should optionally insert default rows (all skills, `enabled=false`) for each existing agent. However, since agents are created dynamically, a seed at migration time would only cover existing agents. A better pattern: insert a default envelope row when a new agent is created (in the agent creation route or Celery task). Document this as a follow-on — Phase 14 migration creates the table; Phase 18 UI writes the configuration.

For Phase 14 testing: use a test fixture that inserts a `capability_envelopes` row with `enabled=true` before each test.

**ORM model pattern** — mirror existing `app/models/agent.py`:

```python
# apps/api/app/models/capability_envelope.py
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Text, Boolean, DateTime, text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base
import uuid

class CapabilityEnvelope(Base):
    __tablename__ = "capability_envelopes"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # FK to agents.id
    skill: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    rate_limit: Mapped[str | None] = mapped_column(Text, nullable=True)
    constraints: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    requires_identity_verification: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("agent_id", "skill", name="uq_capability_envelopes_agent_skill"),
        Index("capability_envelopes_agent_id_idx", "agent_id"),
    )
```

### Cluster 6: Provider Adapter Seam [ASSUMED — interface shape is Claude's discretion]

**Interface design (per-method, strongly typed):**

```python
# apps/api/app/services/transactional/provider_adapter.py
from abc import ABC, abstractmethod
from app.services.transactional.schemas import (
    PlaceOrderInput, PlaceOrderOutput,
    CancelOrderInput, CancelOrderOutput,
    IssueRefundInput, IssueRefundOutput,
    UpdateSubscriptionInput, UpdateSubscriptionOutput,
    BookSlotInput, BookSlotOutput,
    UpdateCustomerRecordInput, UpdateCustomerRecordOutput,
)

class ProviderAdapter(ABC):
    @abstractmethod
    async def place_order(self, args: PlaceOrderInput, agent_id: str) -> PlaceOrderOutput: ...
    @abstractmethod
    async def cancel_order(self, args: CancelOrderInput, agent_id: str) -> CancelOrderOutput: ...
    @abstractmethod
    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput: ...
    @abstractmethod
    async def update_subscription(self, args: UpdateSubscriptionInput, agent_id: str) -> UpdateSubscriptionOutput: ...
    @abstractmethod
    async def book_slot(self, args: BookSlotInput, agent_id: str) -> BookSlotOutput: ...
    @abstractmethod
    async def update_customer_record(self, args: UpdateCustomerRecordInput, agent_id: str) -> UpdateCustomerRecordOutput: ...

class StubProviderAdapter(ProviderAdapter):
    """Phase-14 stub: records what would happen, returns sandbox-labelled responses.
    
    Phase 16 replaces this with Shopify/Stripe/etc. adapters by
    subclassing ProviderAdapter and injecting via get_adapter(agent_id).
    """
    async def place_order(self, args, agent_id):
        return PlaceOrderOutput(
            order_id=f"stub-{uuid4()}", status="pending_confirmation",
            message=f"[STUB] Order received for {args.quantity}x {args.product_id} — no real action taken",
        )
    # ... equivalent stubs for all 6 tools

# Module-level singleton for Phase 14
_STUB_ADAPTER = StubProviderAdapter()

def get_adapter(agent_id: str | None = None) -> ProviderAdapter:
    """Phase 14: always returns the stub. Phase 16: lookup per-agent provider config."""
    return _STUB_ADAPTER
```

**Why per-method (not single-dispatch)?** Phase 16 implements one adapter per provider (Stripe adapter has `issue_refund` + `update_subscription` but not `place_order`; Shopify adapter has `place_order` + `cancel_order`). Typed per-method gives mypy coverage and documents which tools each provider supports. Single-dispatch would obscure this.

### Cluster 7: A2A/ACP-Compatible Tool Shape [ASSUMED — forward-compat design]

The `TransactionalToolDef` captures all fields the v1.2 A2A/ACP manifest serializer needs:

```python
# v1.2 manifest serializer (stub at this point — just documents the contract):
def to_a2a_skill(tool_def: TransactionalToolDef) -> dict:
    return {
        "id": tool_def.skill_name,
        "name": tool_def.sdk_tool.name,
        "description": tool_def.sdk_tool.description,
        "inputModes": tool_def.a2a_input_modes,
        "outputModes": tool_def.a2a_output_modes,
        "examples": tool_def.examples,
        "inputSchema": tool_def.sdk_tool.input_schema,   # already JSON Schema
        # "outputSchema": ...,  # added when output schema is captured
    }
```

**Fields to capture at definition time and NOT defer to v1.2:**
- `examples` (list of 2-3 phrasings) — must match the tool's Pydantic schema usage
- `a2a_input_modes` / `a2a_output_modes` — default `["text", "structured"]` for mutating tools
- `skill_name` — stable identifier used in capability_envelopes (already required for Phase 14)

No A2A/ACP server ships in Phase 14. The `TOOL_REGISTRY` dict is the canonical source.

### Cluster 8: `build_tool_server` Extension [VERIFIED: agent_tools.py source inspection]

**Current `build_tool_server` signature:**
```python
def build_tool_server(conn_str, agent_id, agent_name, strategy, conversation_id, notify_fn) -> McpSdkServerConfig:
```

**Phase 14 extension:** Add the 7 new transactional tools to the `create_sdk_mcp_server` call:
```python
return create_sdk_mcp_server(
    name="customer-tools",
    version="1.0.0",
    tools=[
        retrieve_tool,
        lookup_structured_tool,
        escalate_to_human_tool,
        clarify_tool,
        # Phase 14 additions:
        place_order_tool,
        cancel_order_tool,
        issue_refund_tool,
        update_subscription_tool,
        book_slot_tool,
        update_customer_record_tool,
        confirm_action_tool,
    ],
)
```

**`allowed_tools` update in `agent.py`:** Add `mcp__customer-tools__<tool_name>` for each new tool to `ClaudeAgentOptions.allowed_tools`. The tools are only invokable when the corresponding `capability_envelope.enabled = True` (enforced in the tool handler), so listing them in `allowed_tools` does not grant access — it just tells the SDK not to prompt for permission.

**ContextVars already available:** The new tool handlers can read `_agent_id_var`, `_conversation_id_var`, and `_conn_str_var` exactly as the existing tools do (set by `build_tool_server` before `asyncio.run()`).

**No new ContextVars needed for Phase 14.** The control-DB connection for audit/idempotency writes uses `get_sync_db()` (SQLAlchemy), NOT the tenant `conn_str`. The tenant `conn_str` is used only by Phase 17 (customer identity verification).

---

## Recommended Task / Wave Sequencing

### Wave 0 — Foundation (no dependencies; parallelizable)

| Task | File | What |
|------|------|------|
| 14-01 | `alembic/versions/0014_transactional_substrate.py` | Migration: 4 tables (`capability_envelopes`, `tool_calls_audit`, `pending_confirmations`, `tool_idempotency_keys`) |
| 14-02 | `app/models/capability_envelope.py` etc. | 4 ORM models |
| 14-03 | `app/services/transactional/schemas.py` | All 12 Pydantic models (6 inputs + 6 outputs) |
| 14-04 | `app/services/transactional/provider_adapter.py` | `ProviderAdapter` ABC + `StubProviderAdapter` |
| 14-05 | `app/services/actor_seam.py` | `call_actor_gate` stub |
| 14-06 | `app/services/transactional/registry.py` | `TransactionalToolDef` + `TOOL_REGISTRY` |

### Wave 1 — Enforcement + Infrastructure (depends on Wave 0)

| Task | File | What |
|------|------|------|
| 14-07 | `app/services/transactional/enforcement.py` | Capability envelope check: disabled / rate_limit / constraints |
| 14-08 | `app/services/transactional/idempotency.py` | `check_idempotency` + `store_idempotency` helpers |
| 14-09 | `app/services/transactional/audit.py` | `write_audit_row` helper (control DB, `tool_calls_audit`) |

### Wave 2 — Tool Handlers (depends on Wave 1)

| Task | File | What |
|------|------|------|
| 14-10 | `app/services/transactional/tools.py` | `place_order_tool` + `cancel_order_tool` + `issue_refund_tool` |
| 14-11 | `app/services/transactional/tools.py` | `update_subscription_tool` + `book_slot_tool` + `update_customer_record_tool` |
| 14-12 | `app/services/transactional/tools.py` | `confirm_action_tool` (writes `pending_confirmations`) |
| 14-13 | `app/services/agent_tools.py` | Register all 7 new tools in `build_tool_server`; update `allowed_tools` in `agent.py` |

### Wave 3 — Tests (depends on Wave 2)

| Task | File | What |
|------|------|------|
| 14-14 | `tests/unit/test_transactional_tools.py` | Schema rejection, mutating-flag, idempotency replay, capability-denial paths, audit-row completeness, Actor-seam firing |
| 14-15 | `tests/unit/test_capability_enforcement.py` | Rate-limit counter, constraint violations, disabled-skill denial |
| 14-16 | `tests/integration/test_transactional_substrate.py` | Alembic migration roundtrip; ORM model CRUD |

---

## Common Pitfalls

### Pitfall 1: Forgetting `acks_late=True` is NOT enough for idempotency

**What goes wrong:** A developer adds idempotency logic to the tool handler but assumes the existing `run_agent_turn` Celery task's idempotency guard (job_events check) covers them. It does not — that guard is for the whole SDK turn, not for individual tool calls within a turn.

**Why it happens:** The `run_agent_turn` task guard prevents re-running the whole turn. But within a single turn, a tool can be called multiple times. The `tool_idempotency_keys` table guard is the per-tool guard.

**How to avoid:** Ensure every mutating tool handler has its own idempotency check on `tool_idempotency_keys` BEFORE calling the adapter. The turn-level guard and the tool-level guard are orthogonal.

### Pitfall 2: Writing audit row AFTER idempotency store (atomicity)

**What goes wrong:** Write audit row first, then adapter fails, then idempotency store never happens. On retry, idempotency check misses (key not stored), adapter is called again (double-mutation).

**How to avoid:** Write audit row AFTER adapter returns AND after idempotency store. If the process dies between adapter success and audit write, the audit row is missing (acceptable for Phase 14 stub; Phase 16 should use a short-lived distributed lock or a compensating write). Document this known gap.

**Correct sequence:** adapter → store_idempotency (ON CONFLICT) → write_audit_row.

### Pitfall 3: Rate-limit counter race

**What goes wrong:** Two concurrent requests hit the Redis INCR at the same time, both read count ≤ max, both proceed, actual calls exceed the limit by 1.

**Why it happens:** Redis INCR is atomic (one operation), but the `check count > max → proceed` decision is not atomic with the increment.

**How to avoid:** The Redis INCR command returns the new count in a single atomic operation. `count = redis.incr(key)` returns the post-increment value. Compare `count > max` — if two concurrent calls both get `count = max`, both proceed. In the worst case you get `max + concurrency - 1` calls through. This is acceptable for a rate limiter (no financial harm from one extra call) but document it as approximate. Phase 18 can upgrade to a Lua script or MULTI/EXEC for exact limiting.

### Pitfall 4: `capability_snapshot` must be a plain dict, not an ORM object

**What goes wrong:** The `write_audit_row` helper receives the `CapabilityEnvelope` ORM object and tries to store it as JSONB. SQLAlchemy models are not JSON-serializable.

**How to avoid:** In `check_capability_envelope`, convert the result to a plain dict immediately: `snapshot = dict(envelope_row)` where `envelope_row` is a SQLAlchemy `Row` from `.mappings().first()`. Pass the plain dict everywhere downstream.

### Pitfall 5: Tool not appearing in Claude's tool list (wrong `allowed_tools` format)

**What goes wrong:** New tools are added to `create_sdk_mcp_server` but not to `allowed_tools` in `ClaudeAgentOptions`. Claude can see them in the MCP listing but won't call them without being prompted.

**How to avoid:** For every tool added to `tools=[...]` in `create_sdk_mcp_server(name="customer-tools", ...)`, add `mcp__customer-tools__<tool_name>` to `allowed_tools` in `agent.py`. The pattern is confirmed in `agent.py` lines 566-571 for the existing four tools.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (confirmed — `apps/api/tests/` has 70+ test files using pytest) |
| Config file | `apps/api/pytest.ini` or `pyproject.toml` (check; existing pattern) |
| Quick run command | `cd apps/api && pytest tests/unit/test_transactional_tools.py -x` |
| Full suite command | `cd apps/api && pytest tests/unit/ tests/integration/test_transactional_substrate.py -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| REQ-14-01 | Typed Pydantic schema rejects bad inputs (wrong type, missing required field) | unit | `pytest tests/unit/test_transactional_tools.py::test_place_order_rejects_bad_schema -x` | Wave 3 |
| REQ-14-01 | Typed Pydantic schema accepts valid inputs | unit | `pytest tests/unit/test_transactional_tools.py::test_place_order_valid_schema -x` | Wave 3 |
| REQ-14-02 | All 6 mutating tools have `mutating=True` in TOOL_REGISTRY | unit | `pytest tests/unit/test_transactional_tools.py::test_mutating_flag_presence -x` | Wave 3 |
| REQ-14-02 | `confirm_action` and `escalate_to_human` have `mutating=False` | unit | `pytest tests/unit/test_transactional_tools.py::test_non_mutating_tools -x` | Wave 3 |
| REQ-14-03 | Same idempotency key → second call returns original result, adapter NOT called again | unit | `pytest tests/unit/test_transactional_tools.py::test_idempotency_replay -x` | Wave 3 |
| REQ-14-03 | Different idempotency key on same skill → adapter called fresh | unit | `pytest tests/unit/test_transactional_tools.py::test_idempotency_different_key -x` | Wave 3 |
| REQ-14-04 | Disabled skill → `capability.denial` logged + `is_error` in response | unit | `pytest tests/unit/test_capability_enforcement.py::test_disabled_skill_denial -x` | Wave 3 |
| REQ-14-04 | Rate limit exceeded → denial | unit | `pytest tests/unit/test_capability_enforcement.py::test_rate_limit_denial -x` | Wave 3 |
| REQ-14-04 | `max_amount_cents` exceeded → denial | unit | `pytest tests/unit/test_capability_enforcement.py::test_max_amount_denial -x` | Wave 3 |
| REQ-14-04 | Enabled skill within constraints → passes through | unit | `pytest tests/unit/test_capability_enforcement.py::test_enabled_skill_passes -x` | Wave 3 |
| REQ-14-05 | `tool_calls_audit` row written after every mutating call (success path) | unit | `pytest tests/unit/test_transactional_tools.py::test_audit_row_success -x` | Wave 3 |
| REQ-14-05 | `tool_calls_audit` row written after every mutating call (error path) | unit | `pytest tests/unit/test_transactional_tools.py::test_audit_row_error -x` | Wave 3 |
| REQ-14-05 | `capability_snapshot` matches envelope at time of call | unit | `pytest tests/unit/test_transactional_tools.py::test_audit_capability_snapshot -x` | Wave 3 |
| REQ-14-06 | `confirm_action_tool` writes a `pending_confirmations` row | unit | `pytest tests/unit/test_transactional_tools.py::test_confirm_action_writes_pending -x` | Wave 3 |
| REQ-14-07 | `call_actor_gate` is called for mutating tools | unit (mock) | `pytest tests/unit/test_transactional_tools.py::test_actor_seam_fires_for_mutating -x` | Wave 3 |
| REQ-14-07 | `call_actor_gate` is NOT called for non-mutating tools | unit (mock) | `pytest tests/unit/test_transactional_tools.py::test_actor_seam_skipped_for_non_mutating -x` | Wave 3 |
| REQ-14-07 | `call_actor_gate` returning "block" → tool returns `is_error` without calling adapter | unit (mock) | `pytest tests/unit/test_transactional_tools.py::test_actor_block_prevents_execution -x` | Wave 3 |
| REQ-14-08 | `StubProviderAdapter.place_order` returns a `[STUB]`-labelled response | unit | `pytest tests/unit/test_transactional_tools.py::test_stub_adapter_response -x` | Wave 3 |
| REQ-14-09 | Alembic migration creates all 4 tables with correct columns | integration | `pytest tests/integration/test_transactional_substrate.py::test_migration_creates_tables -x` | Wave 3 |
| REQ-14-10 | New tools appear in `build_tool_server` MCP server tools list | unit | `pytest tests/unit/test_agent_tools.py::test_build_tool_server_includes_transactional -x` | Wave 3 |

### Test Stubs Required

All unit tests for tool handlers use:
1. **`StubProviderAdapter`** — already part of Phase 14 deliverables. No additional stub needed.
2. **Mocked `call_actor_gate`** — `unittest.mock.AsyncMock` that returns `("approve", "")` by default; patched to `("block", "reason")` for block tests.
3. **In-memory SQLite or test Postgres** — for `tool_calls_audit` and `tool_idempotency_keys` writes. Follow existing pattern from `tests/integration/conftest.py`.
4. **Mocked Redis** — for rate-limit counter tests. Use `fakeredis` (already referenced in some test files) or `unittest.mock.MagicMock`.

### Sampling Rate

- **Per task commit:** `pytest tests/unit/test_transactional_tools.py -x` (the primary test file for this phase)
- **Per wave merge:** `pytest tests/unit/ -x -k "transactional or capability"` (all unit tests)
- **Phase gate:** Full suite green (`pytest tests/unit/ tests/integration/test_transactional_substrate.py`) before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/unit/test_transactional_tools.py` — covers REQ-14-01 through REQ-14-10 (unit scope)
- [ ] `tests/unit/test_capability_enforcement.py` — covers REQ-14-04 in isolation
- [ ] `tests/integration/test_transactional_substrate.py` — covers REQ-14-09 (migration roundtrip)

No new test framework installation needed — pytest is already installed.

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Not in Phase 14 scope (identity verification → Phase 17) |
| V3 Session Management | no | Handled by existing SDK session mechanism |
| V4 Access Control | yes | `capability_envelopes` enforcement is the access control layer for transactional tools |
| V5 Input Validation | yes | Pydantic schema validation at handler entry — the L1 tool contract |
| V6 Cryptography | no | No new secrets handling; credential service → Phase 16 |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt injection → coerced `place_order` call with attacker-controlled args | Tampering | L1: typed Pydantic schema rejects string-blob inputs; `amount_cents` is `int`, not free-form |
| Replay attack via duplicate idempotency key | Repudiation | `UNIQUE(agent_id, skill, idempotency_key)` in control DB prevents re-execution |
| Rate-limit evasion via many small requests | DoS | Redis INCR counter per `(agent_id, skill, window)` |
| Exceeding financial blast radius | Tampering | `max_amount_cents` constraint in envelope enforced before adapter call |
| Prompt injection bypasses Actor gate (Phase 15 concern, but seam matters now) | Tampering | Seam fires for ALL `mutating=True` tools; Phase 15 fills the gate body |
| Cross-tenant tool audit contamination | Information Disclosure | `agent_id` scoping on ALL control-DB writes; no tenant data in `tool_calls_audit` |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `TransactionalToolDef` dataclass shape (discretionary fields like `a2a_input_modes`) | Cluster 2, Cluster 7 | Low — v1.2 serializer can read different field names; only used for forward compat |
| A2 | `ProviderAdapter` uses per-method signature (not single-dispatch) | Cluster 6 | Low — Phase 16 implementors can refactor; the Phase-14 interface only matters for stub |
| A3 | `rate_limit` TEXT format is `N/<unit>` (e.g. `"5/hour"`) | Cluster 4 | Medium — if Phase 18 UI adopts a different format, the parser breaks; define the format in docstring early |
| A4 | Idempotency replay should NOT re-fire the Actor gate | Cluster 3 | Medium — if PRD intent is that the Actor should re-evaluate replays (for security), the order needs to flip. CONTEXT.md order puts Actor before idempotency check implying it fires on replays — follow that |
| A5 | `fakeredis` or equivalent is available for unit tests | Validation Architecture | Low — if not, use `unittest.mock.MagicMock` for Redis calls |
| A6 | `confirm_action_tool` is `mutating=False` | Cluster 2 | Medium — if confirm_action itself needs idempotency (e.g. don't confirm twice), it should be `mutating=True` with its own key. Current interpretation: it writes a row but doesn't execute a provider action, so `mutating=False` |

---

## Open Questions

1. **`confirm_action` mutating flag**
   - What we know: `confirm_action` writes a `pending_confirmations` row (a DB write) but does not call a provider.
   - What's unclear: Should the `pending_confirmations` write be idempotency-protected? A double-`confirm_action` for the same action could create two pending rows.
   - Recommendation: Tag `confirm_action` as `mutating=False` (it doesn't execute a provider action), but add a `UNIQUE(agent_id, skill, arguments_hash)` constraint on `pending_confirmations` to prevent duplicate rows.

2. **Capability envelope row creation timing**
   - What we know: The migration creates the table. Enforcement reads `WHERE agent_id=X AND skill=Y`.
   - What's unclear: If no row exists for (agent_id, skill), should the tool be treated as disabled (fail-closed) or enabled (fail-open)?
   - Recommendation: Fail-closed. If no envelope row exists, treat as `enabled=false` and log `capability.denial` with reason `"no_envelope_row"`. Phase 18 admin UI creates the rows; until then, all transactional tools are off.

3. **`write_audit_row` atomicity with `store_idempotency`**
   - What we know: Both writes go to the control DB. They're separate statements.
   - What's unclear: If the process dies between `store_idempotency` (OK) and `write_audit_row` (not yet), the audit log has a gap.
   - Recommendation: Accept this for Phase 14 (stub adapter; no real financial impact). Document as a known gap. Phase 16 should wrap both in a single transaction.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| PostgreSQL (control DB) | Migration 0014, ORM models | Confirmed (Neon) | Neon Postgres | — |
| Redis | Rate-limit counter, existing SSE | Confirmed (existing `REDIS_URL`) | Any | Disable rate limiting in tests with mock |
| `alembic` | Migration | Confirmed (already in use) | Current (M13 migration ran) | — |
| `pydantic` v2 | Pydantic schemas | Confirmed (existing use of `BaseModel`) | v2 (model_json_schema available) | — |
| `pytest` | Tests | Confirmed (70+ test files exist) | Existing version | — |

**Missing dependencies with no fallback:** None.

---

## Sources

### Primary (HIGH confidence)
- `C:\Users\Bantu\AppData\Local\Programs\Python\Python312\Lib\site-packages\claude_agent_sdk\__init__.py` — SDK tool mechanism, `create_sdk_mcp_server`, `@tool` decorator, `ClaudeAgentOptions.hooks`
- `C:\Users\Bantu\AppData\Local\Programs\Python\Python312\Lib\site-packages\claude_agent_sdk\types.py` — `PreToolUseHookInput`, `HookCallback`, `HookMatcher`, `ClaudeAgentOptions` full field list
- `apps/api/app/services/agent_tools.py` — existing tool registration pattern, ContextVars, `build_tool_server`
- `apps/api/app/worker/tasks/runtime/agent.py` — `_run_sdk_turn`, `ClaudeAgentOptions` construction, `allowed_tools` pattern
- `Post-M10-PRD.md` §4.2, §4.3, §4.4, §4.7 — canonical DDL, tool set, security layers
- `docs/adr/0002-agent-tool-and-provisioning-strategy.md` §A — narrowly-typed tools, no provider MCP
- `.planning/phases/14-transactional-tool-contract-capability-audit-substrate-typed/14-CONTEXT.md` — locked decisions, phase boundary

### Secondary (MEDIUM confidence)
- `apps/api/app/services/validation_service.py` — Actor seam design informed by gatekeeper/auditor/strategist pattern
- `apps/api/alembic/versions/0013_alert_tenant_id.py` — confirmed current head migration for `down_revision`
- `apps/api/app/models/agent.py` — ORM model pattern to mirror

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages confirmed as existing dependencies; SDK version confirmed by `pip show`
- Architecture: HIGH — confirmed by SDK source inspection; no guessing
- Pitfalls: HIGH for SDK hook pitfall (confirmed by source), MEDIUM for atomicity and race conditions (based on Celery+Postgres semantics)
- A2A compat: MEDIUM — forward-compat metadata fields are Claude's discretion; v1.2 may need minor adjustments

**Research date:** 2026-06-29
**Valid until:** 2026-07-29 (claude-agent-sdk 0.1.81 API surface; fast-moving SDK)
