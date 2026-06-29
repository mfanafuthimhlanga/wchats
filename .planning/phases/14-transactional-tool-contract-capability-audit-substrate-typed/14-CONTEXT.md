# Phase 14: Transactional tool contract & capability/audit substrate — Context

**Gathered:** 2026-06-29
**Status:** Ready for planning
**Source:** Derived from `Post-M10-PRD.md` §4 (authoritative) + `docs/adr/0002-agent-tool-and-provisioning-strategy.md`. No separate discuss round — the decisions are already pinned by the PRD and ADR; this CONTEXT sets the phase boundary and canonical refs.

<domain>
## Phase Boundary

Phase 14 is the **authorization substrate** the rest of v1.1 rides on. It delivers: the typed transactional **tool contract** (L1), the per-skill **capability envelope** table + enforcement middleware (L2), and the **audit / pending-confirmation** tables — so that no action can execute without a typed contract, a capability check, and an audit row.

**IN:** typed tool definitions (the six transactional tools + `confirm_action`) with `mutating:true|false` tagging + idempotency-key handling; `capability_envelopes` table + enforcement middleware; `tool_calls_audit` + `pending_confirmations` tables; registration of the new tools into the existing customer-agent tool loop; a **pluggable execution seam** so the tools' contracts exist now and Phase 16 plugs in real provider logic.

**OUT (explicit — later phases):**
- **Real provider execution** (Shopify/WooCommerce/Stripe/Calendly API calls + the credential service) → **Phase 16**. In Phase 14 the tool execution body is abstracted behind an adapter interface with a stub/no-op (or sandbox) implementation; the *contract, envelope check, idempotency, and audit row* are real.
- **The Actor validator** (the pre-mutation Haiku gate) → **Phase 15**. Phase 14 must leave a clean **pre-execution hook seam** that fires for `mutating:true` tools, which Phase 15 fills.
- **Customer identity verification** → **Phase 17** (Phase 14 only adds the `requires_identity_verification` envelope column).
- **Capability admin UI + blast-radius gate** → **Phase 18** (CAP-03/CAP-04). Phase 14 ships the table + enforcement, not the M8 UI.
</domain>

<decisions>
## Locked Decisions

### Schemas — control DB, per `Post-M10-PRD.md` §4.3 (use that DDL as the contract)
- `capability_envelopes` (agent_id, skill, enabled, rate_limit, constraints JSONB, requires_confirmation, requires_identity_verification, updated_at, UNIQUE(agent_id, skill)).
- `tool_calls_audit` (agent_id, conversation_id, skill, arguments, result, actor_decision, actor_rationale, capability_snapshot JSONB, latency_ms, error, created_at).
- `pending_confirmations` (agent_id, skill, arguments, requested_at, expires_at, resolved_at, resolution).
- These are **control-DB** tables (platform metadata, no tenant PII) — new Alembic migration on the control DB. `actor_decision`/`actor_rationale` columns ship now but are written by Phase 15.

### Tool contract (L1) — per `Post-M10-PRD.md` §4.2 + ADR-0002
- Six transactional tools — `place_order`, `cancel_order`, `issue_refund`, `update_subscription`, `book_slot`, `update_customer_record` — plus `confirm_action` (new) and the existing `escalate_to_human`. Each is a **typed Python function with full Pydantic input/output schemas** — no string-blob, SQL, URL, or arbitrary-JSON inputs.
- Every tool tagged `mutating: true|false` **at definition time** (never runtime-inferred) — the signal the Phase-15 Actor hook keys on.
- Side-effecting tools take a **client-provided idempotency key**; replay with the same key returns the original result and never re-executes the mutation.
- Tool definitions are **A2A- and ACP-skill-compatible in shape** (typed inputs/outputs + examples) per ADR-0002 — forward-compat for v1.2; no A2A/ACP server in this phase.

### Capability envelope (L2)
- Enforcement **middleware** runs before a tool executes: reject (log `capability.denial`) when the skill is disabled, over its `rate_limit`, or violates a `constraints` rule (`max_amount_cents`, scope filters). Owner can only tighten (not loosen) — but the UI for that is Phase 18; Phase 14 ships the enforcement + table.

### Tool consumption pattern — per ADR-0002 §A (do NOT deviate)
- New tools register into the **existing customer-agent tool mechanism** (extend `apps/api/app/services/agent_tools.py` / `build_tool_server`), NOT a provider MCP server or vendor agent toolkit.
- The tool body calls a **provider-adapter interface** (Phase 16 implements the real adapters); Phase 14 ships the interface + a stub/sandbox impl so the path is exercisable end-to-end (envelope → idempotency → execute(stub) → audit).

### CLAUDE.md invariants
- `acks_late=True` AND idempotency on any Celery task touched. Connection strings never in task args. Per-tenant Neon for tenant data; control-DB for these three new tables. Claude Agent SDK is stateless (system_prompt every call).
</decisions>

<canonical_refs>
## Canonical References — downstream agents MUST read

### The contract
- `Post-M10-PRD.md` §4.3 (table DDL — capability_envelopes, tool_calls_audit, pending_confirmations), §4.2 (tool set + idempotency + mutating flag), §4.4 (L1/L2 status), §4.7 (deliverables).
- `docs/adr/0002-agent-tool-and-provisioning-strategy.md` §A (narrow typed tools call provider SDKs behind L1–L3; no provider MCP/toolkit into the agent).

### Code to extend (read before planning)
- `apps/api/app/services/agent_tools.py` — the existing tool server (`build_tool_server`, the retrieve/lookup/escalate/clarify tools, the ContextVar state from Phase 13's 13-07). The transactional tools register here; the Phase-15 Actor hook seam goes here.
- `apps/api/app/worker/tasks/runtime/agent.py` — the `_run_sdk_turn` loop where tools are invoked (Phase 13 made conn handling ContextVar-safe); the pre-execution hook fires here.
- `apps/api/app/services/validators.py` / `validation_service.py` (M5) — the validation chain the Actor extends to 4 nodes in Phase 15; Phase 14 leaves the seam.
- Control-DB Alembic migrations dir (the `alembic/` for the control DB, where M1–M11 control migrations live) — the new migration lands here.
- `apps/api/app/models/` — the Agent ORM + control-DB models the new tables relate to.

### Project rules
- `CLAUDE.md` — acks_late+idempotency, two queues, per-tenant Neon, Langfuse v4, stateless Agent SDK.
</canonical_refs>

<specifics>
## Specific Ideas / Research Landmines
- **claude-agent-sdk 0.1.81 tool mechanism + pre-execution hook.** STATE note [09]: `ClaudeSDKClient` does not support custom JSON tool schemas in *service* contexts (deployment orchestrator, strategy svc switched to direct Anthropic API). BUT the customer agent already exposes custom tools via `build_tool_server`. The researcher MUST pin down exactly how 0.1.81 exposes typed tools to the customer agent AND whether/how a synchronous **pre-execution hook** (for the Phase-15 Actor gate on `mutating:true` tools) can be inserted in the tool loop. This is the highest-uncertainty item.
- **Idempotency storage + replay semantics.** Where the client idempotency key + prior result are stored (a dedicated idempotency table vs `tool_calls_audit` vs Redis) and the replay contract. Must be correct under Celery retries (acks_late).
- **Envelope enforcement point.** Exactly where the capability check runs relative to the tool body and the (future) Actor hook — define the order: capability check → (Phase 15 Actor) → idempotency check → execute → audit.
- **A2A/ACP-compatible tool shape.** Capture name/description/input-output/examples in a form a v1.2 serializer can emit as A2A skill + ACP skill without redefining the tools.

## Claude's Discretion
- Idempotency storage mechanism (recommend in RESEARCH).
- Exact provider-adapter interface shape + the Phase-14 stub.
- Whether enforcement is a decorator, a middleware function, or a wrapper around the tool dispatch.
</specifics>

<deferred>
## Deferred (later phases)
- Real provider adapters + credential service → Phase 16.
- Actor validator + 4-node chain → Phase 15.
- Identity verification → Phase 17.
- Capability admin UI + financial blast-radius gate → Phase 18.
- A2A/ACP servers + manifests → v1.2.

---
*Phase: 14-transactional-tool-contract-capability-audit-substrate-typed*
*Context gathered: 2026-06-29 (derived from Post-M10-PRD §4 + ADR-0002; no discuss round)*
</deferred>
</content>
