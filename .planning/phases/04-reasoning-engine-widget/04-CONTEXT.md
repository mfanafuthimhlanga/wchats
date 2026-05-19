# Phase 4: M4 Reasoning Engine + Widget v0 — Context

**Gathered:** 2026-05-16
**Status:** Ready for planning
**Source:** PRD Express Path (prd.md §M4 + Layer 6 + Layer 11 + REQUIREMENTS.md AGT-01–AGT-11)

<domain>
## Phase Boundary

M4 wires the M3 retrieval engine to a Claude Agent SDK agent with four tools, delivers a
Preact iframe widget under 20 kb gzipped, and publishes a live public demo. This is the
**first hireable artifact** — M1–M4 alone is a complete portfolio piece.

Deliverables:
- Claude Agent SDK customer-service agent (4 tools: retrieve, lookup_structured, escalate_to_human, clarify)
- System prompt assembled at call time from agent soul fields (voice, do-list, do-not-list, role)
- Session continuity via conversations/messages tables (already exist in tenant DB from M1 migration)
- Celery `run_agent_turn` task on `runtime` queue (async chat, SSE events)
- FastAPI agent-chat routes: `POST /agents/{id}/chat`, `GET /agents/{id}/conversations`
- Widget config endpoint: `GET /widget/{agent_id}/config` → {theming, agent_id, short-lived JWT}
- Preact iframe widget bundle (≤20 kb gzipped) with citation footer + escalation UX
- `GET /widget/{agent_id}/config` CORS + widget CSP headers
- Agent soul fields on `agents` table (control-DB migration 0004)
- Agent soul editor in Next.js admin UI — structured fields only, no blank textarea
- Escalation notification pathway (email or dashboard alert when `escalate_to_human` fires)
- End-to-end public demo: real ingested data → retrieve → agent → widget answers questions

This phase does NOT include:
- Validation chain (Gatekeeper, Auditor, Strategist) — M5
- Ragas evals — M6
- Red team agents — M7
- `verified_qa` lookup (table empty until M6 seeds it)
- Auto-generated retrieval strategies — M9
- Admin UI beyond soul editor (dashboards, eval views) — M5+
- Multi-language / voice channel — non-goal v1
- Admin UI full onboarding wizard — M8

</domain>

<decisions>
## Implementation Decisions

### Schema — Control DB Migration (0004) — LOCKED

New Alembic migration: `apps/api/alembic/versions/0004_agent_soul_fields.py`

Adds to `agents` table:
```sql
ALTER TABLE agents ADD COLUMN soul_voice TEXT;
ALTER TABLE agents ADD COLUMN soul_do_list JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE agents ADD COLUMN soul_donot_list JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE agents ADD COLUMN soul_role TEXT;
```

No tenant DB migration required — `conversations`, `messages`, `tool_calls` tables were
created in `0001_tenant_v1_schema.py` (M1). They already exist on every tenant DB.

### Tenant DB Schema — Existing Tables (from M1) — READ BEFORE IMPLEMENTING

The following tables already exist in every tenant DB (created by `0001_tenant_v1_schema.py`):
- `conversations(id, agent_id, created_at, metadata JSONB)`
- `messages(id, conversation_id, role TEXT, content TEXT, created_at, tool_calls JSONB)`
- `tool_calls(id, message_id, tool_name, input JSONB, output JSONB, created_at)`

**Do not re-create these.** Read the tenant migration before adding any columns.

### Claude Agent SDK — LOCKED

**Version:** `claude-agent-sdk==0.1.81` (pinned in CLAUDE.md, do not upgrade).

**SDK is stateless.** `system_prompt` is passed in `ClaudeAgentOptions` at every call.
Session continuity uses `resume=session_id` where `session_id` is the `conversations.id` UUID.

**Model for customer agent:** `claude-haiku-4-5-20251001` (cost-optimized for customer calls).
Use the latest Claude models from CLAUDE.md for any agent-SDK calls:
- Customer agent: Haiku 4.5 (`claude-haiku-4-5-20251001`)

**System prompt assembly at call time** (AGT-02 — LOCKED):
```python
def build_system_prompt(agent: Agent) -> str:
    role = agent.soul_role or "customer service"
    voice = agent.soul_voice or "helpful and professional"
    do_list = "\n".join(f"- {item}" for item in (agent.soul_do_list or []))
    donot_list = "\n".join(f"- {item}" for item in (agent.soul_donot_list or []))
    return f"""You are a {role} agent for {agent.name}.
Voice and tone: {voice}
Do: {do_list or "(none specified)"}
Do not: {donot_list or "(none specified)"}
Always ground answers in retrieved content. Cite sources in your response."""
```

### Four Agent Tools — LOCKED (AGT-01)

All four tools MUST be implemented:

1. **`retrieve(query: str, filters: list[dict] = []) -> list[dict]`**
   — Calls `retrieve_and_rank` retrieval service (M3) with tenant conn_str fetched from
   control DB. Returns top-k chunks with scores + document names for citation.

2. **`lookup_structured(table: str, filters: dict) -> list[dict]`**
   — Direct psycopg2 SELECT against tenant DB. Allowlisted tables only (chunks, documents,
   chunk_metadata). Prevents arbitrary SQL injection via table name allowlist.

3. **`escalate_to_human(reason: str, context: str) -> dict`**
   — Marks conversation as escalated in `conversations.metadata`. Triggers notification
   pathway (email or dashboard alert). Returns confirmation message.

4. **`clarify(question: str) -> str`**
   — Returns the clarification question string to the caller. The SDK presents it as an
   agent message; the widget shows it as a normal chat bubble.

### Session Continuity — LOCKED (AGT-03)

On **first turn** (no session_id in request):
1. Create `conversations` row in tenant DB: `(id=uuid4(), agent_id=agent_id, created_at=now())`
2. Pass `session_id=None` to SDK → SDK creates new session
3. Capture `sdk_response.session_id` → store as `conversations.id` (or cross-reference it)
4. Return `conversation_id` in response so widget can send it on next turn

On **subsequent turns** (conversation_id provided):
1. Load existing `conversations` row to validate ownership
2. Pass `resume=conversation_id` to SDK
3. Append new messages to `messages` table after SDK response

**New browser session → new conversation_id.** No cross-session memory in M4.

### Celery Task Contract — LOCKED

New task: `run_agent_turn` on `runtime` queue.

```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_agent_turn",
)
def run_agent_turn(self, job_id: str, agent_id: str, message: str, conversation_id: str | None) -> dict:
    ...
```

Task args: `job_id`, `agent_id`, `message`, `conversation_id` — NO conn_str, NO API keys.
Connection string fetched+decrypted from control DB by `agent_id` (M1/M2/M3 pattern).
Soul fields fetched from `agents` row at runtime.

SSE events emitted:
- `agent.thinking` — task begins, SDK called
- `agent.tool_call` — each tool invocation (tool_name + input in payload)
- `agent.tool_result` — each tool result (tool_name + output summary)
- `agent.response` — final agent text response (full text + citation footer + conversation_id)
- `agent.escalated` — when escalate_to_human fires (escalation context in payload)

Idempotency: if `agent.response` already emitted for this job_id, return early.

### FastAPI Routes — LOCKED

New router: `apps/api/app/api/v1/agent_chat.py`

```
POST /agents/{agent_id}/chat
  Auth: X-API-Key (tenant auth — existing pattern)
  Body: {"message": "...", "conversation_id": "uuid|null"}
  → 202 {"job_id": "uuid", "events_url": "/jobs/{job_id}/events", "conversation_id": "uuid"}

GET /agents/{agent_id}/conversations
  → 200 list of conversations (id, created_at, escalated, message_count)

GET /widget/{agent_id}/config
  Auth: none (public endpoint, but rate-limited)
  → 200 {"agent_id": "uuid", "name": "...", "theming": {...}, "jwt": "..."}

POST /widget/{agent_id}/chat
  Auth: Bearer JWT (widget JWT)
  Body: {"message": "...", "conversation_id": "uuid|null"}
  → 202 {"job_id": "uuid", "events_url": "/jobs/{job_id}/events", "conversation_id": "uuid"}
```

Widget JWT is short-lived (15-minute expiry). Signed with `JWT_SECRET` from Settings.
`POST /widget/{agent_id}/chat` validates JWT (agent_id claim must match URL param).

### Widget JWT — LOCKED

JWT payload:
```json
{"sub": "widget", "agent_id": "<uuid>", "exp": <now+900>}
```

Signed with `JWT_SECRET` in Settings (new field, required). Use `python-jose[cryptography]`
(already common in FastAPI stack). Add `JWT_SECRET` to Settings and `.env.example`.

### Preact Widget Bundle — LOCKED (AGT-06, AGT-07, AGT-09)

**Location:** `apps/widget/` (new directory)
**Framework:** Preact (not React — ≤20kb gzipped target)
**Build:** Vite with preact plugin, output to `apps/widget/dist/`

Widget iframe flow:
1. iframe loads `widget.js` from CDN (or `http://localhost:8001` for local dev)
2. Calls `GET /widget/{agent_id}/config` → receives `{theming, agent_id, jwt}`
3. Stores JWT in memory (never localStorage — XSS risk)
4. Sends user messages to `POST /widget/{agent_id}/chat` (Bearer JWT)
5. Opens SSE connection to `/jobs/{job_id}/events`
6. Renders `agent.response` with citation footer
7. On `agent.escalated`: shows escalation UI (human handoff message + optional form)

**Citation footer format (AGT-04 — LOCKED):** "Based on: [Document Name, Section]" — extracted
from the `retrieve` tool's returned chunks. Rendered below every agent response.

**Escalation UX (AGT-05):** When `agent.escalated` event arrives, widget shows:
> "I've flagged this for our team. Reason: {reason}. Expect a reply within [SLA]."
Plus a contact capture form (name + email) whose submission is a no-op in M4 (just shows
a confirmation; real notification pathway handled by escalate_to_human tool).

**CORS/CSP (AGT-09 — LOCKED):**
- Widget config and widget chat endpoints: `Access-Control-Allow-Origin: *` (public widget)
- `Content-Security-Policy` on embed page: `frame-src 'self' <widget-origin>`
- FastAPI CORS middleware: add widget origin to `CORS_ORIGINS` (already in Settings from M1)

**Bundle size gate (AGT-07):** Vite bundle analysis step in Makefile/CI confirms ≤20kb gzip.
If bundle exceeds 20kb, build FAILS (not a warning).

### Agent Soul Editor — Admin UI (AGT-11)

**Location:** `apps/admin/` (Next.js app — check if directory exists before creating)
**Scope:** Single page/route for soul editing. Not a full admin UI buildout.

Structured fields only (NOT a blank textarea):
- **Name**: text input (agent display name)
- **Role**: select/text — predefined options: "Customer Support", "Sales Qualification",
  "Internal Helpdesk", or free-form custom text
- **Voice & Tone**: textarea (soul_voice — e.g., "warm and empathetic, uses plain language")
- **Do list**: add/remove list of strings (soul_do_list array)
- **Do-not list**: add/remove list of strings (soul_donot_list array)

Calls `PATCH /agents/{id}` (existing endpoint from M1) with updated soul fields.
Uses existing `X-API-Key` auth. No separate auth system in M4.

### Escalation Notification Pathway (AGT-05) — SCOPED TO M4

In M4, `escalate_to_human` sends a notification using the simplest available path:
- **Primary**: Email via SMTP (sendmail or smtplib). Add `SMTP_HOST`, `SMTP_PORT`,
  `SMTP_FROM`, `OWNER_EMAIL` to Settings (all optional). If not configured → log only.
- **Fallback**: log to structlog at WARNING level with full escalation context.

Full email/dashboard notification system is M5+. M4 ships the minimal viable path.

### Public Demo Site (AGT-10) — SCOPED TO M4

A minimal static HTML page (`scripts/demo_m4.html` or `apps/demo/index.html`) that:
1. Embeds the Preact widget iframe pointed at a real Veridian agent
2. Is deployable to any static host (Netlify, Vercel, GitHub Pages)
3. Serves as the "live public demo" for the hireable artifact

The page has no backend — just the iframe embed snippet. The agent it points to must
have been provisioned (M1), data ingested (M2), and retrieval tested (M3).

A `scripts/demo_m4.sh` script demonstrates the full flow:
1. POST /agents → provision agent
2. Upload demo_business.pdf → ingest data (M2 chain)
3. POST /agents/{id}/chat → confirm agent responds
4. Print the iframe embed snippet for the demo page

### Testing Strategy

- **Unit:** Mock SDK calls, mock psycopg2 (tenant DB), test system prompt assembly,
  test JWT generation/validation, test tool definitions in isolation.
- **Integration:** Real local Postgres (conversations/messages tables), mocked SDK,
  `CELERY_TASK_ALWAYS_EAGER=True`. Test full chat route → task → SSE events pattern.
- **Widget bundle test:** `npm run build` exits 0 AND gzip output ≤20480 bytes.
- **E2E (AGENT_E2E_ENABLED=1):** Real Claude API + real M3 retrieval against local tenant.
  Guarded — not run in CI by default.

### New Settings Fields — LOCKED

Add to `apps/api/app/core/config.py`:
```python
JWT_SECRET: str = "dev-secret-change-in-production"
SMTP_HOST: str | None = None
SMTP_PORT: int = 587
SMTP_FROM: str | None = None
OWNER_EMAIL: str | None = None
```

Add all to `.env.example`.

### Celery App Update

Add `"app.worker.tasks.runtime.agent"` to `celery_app.conf.include` (same pattern as M3's
`retrieve` task addition in M3).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### PRDs
- `prd.md` — Full PRD; §M4 for milestone scope; Layer 6 "Reasoning engine" for tool contract;
  Layer 11 "Widget delivery" for widget architecture
- `.planning/REQUIREMENTS.md` — AGT-01 through AGT-11 (all 11 must appear in plan requirements fields)
- `.planning/ROADMAP.md` — M4 milestone, success criteria, "first hireable artifact" context

### Prior Phase Context (read for patterns, do not duplicate)
- `.planning/phases/03-hybrid-retrieval/03-CONTEXT.md` — M3 retrieval service patterns;
  retrieve_and_rank task; psycopg2 tenant DB pattern; SSE event payload structure
- `.planning/phases/02-ingestion-pipeline/02-CONTEXT.md` — tenant DB schema after M2;
  entity extraction; voyageai embedding patterns
- `.planning/phases/01-control-plane-skeleton/01-CONTEXT.md` — M1 SSE pattern; emit() helper;
  Celery chain patterns; argon2 API key auth; Fernet encryption

### M1/M2/M3 Codebase (pattern source — read before implementing)
- `apps/api/app/worker/tasks/pipeline/provision.py` — acks_late=True, idempotency guard pattern
- `apps/api/app/worker/tasks/runtime/retrieve.py` — M3 runtime task pattern (CLOSEST ANALOG)
- `apps/api/app/services/retrieval_service.py` — retrieve_and_rank entry point to call from tool
- `apps/api/app/services/events.py` — emit() helper (reuse for agent.* events)
- `apps/api/app/core/config.py` — Settings pattern (add JWT_SECRET, SMTP_* fields)
- `apps/api/app/core/security.py` — Fernet/argon2 patterns (reference for JWT signing approach)
- `apps/api/alembic/versions/0003_agent_retrieval_strategy.py` — control-DB migration pattern
- `apps/api/alembic_tenant/versions/0001_tenant_v1_schema.py` — conversations/messages/tool_calls DDL (READ BEFORE touching tenant schema)
- `apps/api/app/api/v1/query.py` — closest route analog for agent_chat.py
- `apps/api/app/api/v1/documents.py` — agent ownership validation pattern
- `apps/api/app/worker/celery_app.py` — queue definitions, include list update pattern
- `.planning/phases/03-hybrid-retrieval/.continue-here.md` — M3 implementation decisions, exact psycopg2 patterns

### CLAUDE.md Constraints (enforced in all plans)
- `acks_late=True` AND idempotency on every Celery task
- Connection strings never in Celery task args
- FastAPI never does work inline
- claude-agent-sdk 0.1.81 (pinned version)
- Langfuse v4 API if observability added (not required in M4 core plans)
- No Docker — 4GB RAM machine; all services run locally

</canonical_refs>

<specifics>
## Specific Ideas

### System Prompt Assembly vs Soul Stored as Blob

The system prompt is ASSEMBLED AT CALL TIME from structured soul fields, NOT stored as a
blob in the DB. This is a deliberate architecture choice: it allows the admin UI to
present structured fields (AGT-11), makes the system prompt auditable and testable,
and keeps the soul fields machine-readable for future M9 strategy synthesis.

### retrieve Tool → M3 retrieval_service

The `retrieve` tool should call `retrieval_service.rrf_fuse()` + `retrieval_service.rerank()`
directly (not via `retrieve_and_rank.apply_async`) because tool calls happen inside an
already-running Celery task. Using apply_async inside a task creates nested chain issues.
Call the retrieval service functions directly from the tool implementation.

### Widget JWT Security

JWT is stored in memory (JS variable), NOT localStorage or sessionStorage. This protects
against XSS stealing the token. The JWT expires in 15 minutes; widget fetches a fresh one
on page reload by calling `/widget/{agent_id}/config` again. This is intentional — the
widget config endpoint is public and cheap.

### Citation Footer Format

The `retrieve` tool returns chunks with `document_name` (from `documents.name` column in
tenant DB) and `section` (from `chunk_metadata.section` if available, or chunk ordinal).
The agent is instructed in the system prompt to list its sources; the widget extracts
the citation from the `agent.response` payload (add a `citations` field to the event payload)
and renders it as a styled footer below the message bubble.

### Preact Bundle ≤20kb — Build Approach

To stay under 20kb gzipped:
- Use Preact (not React) — preact/core is ~4kb min+gzip
- No UI component library — custom minimal CSS
- No date library, no lodash, no heavy deps
- Fetch API for HTTP (no axios)
- EventSource for SSE (native browser API)
- Vite production build with `minify: 'terser'`
- Check bundle size in `package.json` postbuild script: `gzip -c dist/widget.js | wc -c`

</specifics>

<deferred>
## Deferred Ideas

- Validation chain (Gatekeeper, Auditor, Strategist) — M5
- verified_qa lookup before retrieval — M6 (table empty until M6)
- Ragas evals — M6
- Red team agents — M7
- Full admin UI (eval dashboard, retrieval trace viewer, conversation history) — M5+
- Owner notification via Slack/webhook — M5 (M4 ships email-only fallback)
- Token streaming (streaming agent response to widget character-by-character) — post-M4
- Multi-tenant widget on single domain — M4 ships single-agent-per-widget only
- Structured data ingestion path (CSV, order exports) — M4's lookup_structured uses existing chunks only
- Conversation history in widget UI (scrollable past messages) — future UX; M4 shows current session only
- verified_qa candidate queue (production_promotion path) — M5 ships the candidate-marking
- Full onboarding wizard in admin UI — M8

</deferred>

---

*Phase: 04-reasoning-engine-widget*
*Context gathered: 2026-05-16 via PRD Express Path (prd.md §M4 + Layer 6 + Layer 11 + REQUIREMENTS.md AGT-01–AGT-11)*
