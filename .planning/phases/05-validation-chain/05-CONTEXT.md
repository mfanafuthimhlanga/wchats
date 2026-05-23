# Phase 5: Validation Chain — Context

**Gathered:** 2026-05-23
**Status:** Ready for planning
**Source:** PRD Express Path (prd.md)

<domain>
## Phase Boundary

Phase 5 wraps every agent response with three async Claude judges — Gatekeeper, Auditor, and Strategist — running after the response is streamed to the user. All three validators use Haiku-tier Claude API calls (direct, not Agent SDK). Outputs are Pydantic-validated and logged to Langfuse v4. Persistent Auditor failures trigger a `strategy_resynthesis_flagged` flag on the agent row. Auditor `grounded` responses with confidence above the per-tenant threshold become verified-knowledge candidates queued in `verified_qa_candidates` for later owner approval (promotion UI ships in M8, but the queueing infra ships here).

**What this phase does NOT build:**
- The owner approval UI for verified-knowledge candidates (M8)
- Neon eval branches (M6)
- The eval harness / Ragas metrics (M6)
- The `verified_qa` promotion to production (M6 + M8)
- The weekly digest (M10)

</domain>

<decisions>
## Implementation Decisions

### Validator Architecture
- **D-01 [LOCKED]** Three validators run sequentially: Gatekeeper → Auditor → Strategist
- **D-02 [LOCKED]** All three use Claude API direct (Haiku) — NOT the Agent SDK
- **D-03 [LOCKED]** Validators run async after the response is streamed to the user — user never waits on validation
- **D-04 [LOCKED]** All outputs are Pydantic-validated structured responses
- **D-05 [LOCKED]** All outputs logged to Langfuse v4 (not pre-v4 API — `start_span()`/`start_generation()` patterns are forbidden)

### Gatekeeper
- **D-06 [LOCKED]** Verdict enum: `pass | fail | needs_clarification`
- **D-07 [LOCKED]** Question: "Does this response address the user's actual question?"

### Auditor
- **D-08 [LOCKED]** Verdict enum: `grounded | ungrounded | partial`
- **D-09 [LOCKED]** Output includes citation spans: which specific claims map to which retrieved context passages
- **D-10 [LOCKED]** Persistent `ungrounded` failures on a retrieval pattern → set `strategy_resynthesis_flagged = True` on the agent row (new boolean column in Alembic migration)
- **D-11 [LOCKED]** Auditor `grounded` with confidence above per-tenant threshold → response becomes verified-knowledge candidate queued in `verified_qa_candidates` staging table (promotion to `verified_qa` happens in M8)

### Strategist
- **D-12 [LOCKED]** Verdict enum: `ship | revise | escalate`
- **D-13 [LOCKED]** Checks: response coherence, on-brand, aligned with agent role

### Infrastructure
- **D-14 [LOCKED]** Validators are Celery tasks on the `runtime` queue — FastAPI never does validation inline
- **D-15 [LOCKED]** Validation triggered as a Celery chord/chain after `run_agent_turn` completes
- **D-16 [LOCKED]** Langfuse SDK v3 API only — use `start_as_current_generation()` context manager pattern; never use the deprecated v2 `StatefulTraceClient` chain (`langfuse.trace()` / `trace.generation()`); always call `langfuse.flush()` at end of each Celery task
- **D-17 [LOCKED]** `strategy_resynthesis_flagged` field requires a new Alembic migration on the control DB agents table

### Verified-Knowledge Candidate Queueing
- **D-18 [LOCKED]** `verified_qa_candidates` staging table lives on each tenant DB (per-tenant Neon project)
- **D-19 [LOCKED]** Rows written when Auditor verdict = `grounded` AND confidence ≥ per-tenant threshold (default 0.90)
- **D-20 [LOCKED]** Columns: `id`, `conversation_id`, `question`, `answer`, `citations`, `auditor_confidence`, `queued_at`, `status` (`pending | approved | rejected`)
- **D-21 [LOCKED]** Alembic migration adds `verified_qa_candidates` to tenant DB schema

### Demo
- **D-22 [LOCKED]** Demo: adversarial query in widget → walk through each validator's score in Langfuse (VAL-07)

### Claude's Discretion
- Exact threshold logic for counting "persistent" Auditor failures (e.g., N consecutive ungrounded from same retrieval pattern vs. percentage over rolling window) — recommended: 3 consecutive `ungrounded` on same conversation
- Whether to emit SSE event when validation completes (informational only — user already received response)
- Whether `verified_qa_candidates` promotion threshold is stored as a per-agent config field or a global setting — recommended: per-agent with global default in Settings
- Whether to use Langfuse `score()` API for validator verdicts alongside span logging

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project guidelines
- `CLAUDE.md` — stack, rules, Langfuse v4 constraint, no Docker constraint, Celery queue names (`pipeline` / `runtime`), connection string rules

### Phase 4 patterns (what this phase extends)
- `.planning/phases/04-reasoning-engine-widget/04-CONTEXT.md` — agent run architecture, Celery task patterns, SSE patterns, tool definitions
- `.planning/phases/04-reasoning-engine-widget/04-03-PLAN.md` — `run_agent_turn` task (the Celery task this phase attaches validators to)
- `.planning/phases/04-reasoning-engine-widget/04-07-PLAN.md` — existing eval harness (judge.py patterns, Haiku call patterns)
- `.planning/phases/04.1-security-hardening/CONTEXT.md` — security hardening decisions (injection sanitization, soul validator patterns)

### Existing code patterns
- `apps/api/app/worker/celery_app.py` — Celery app config, queue definitions
- `apps/api/app/worker/tasks/` — existing task patterns (acks_late, idempotency guards)
- `apps/api/app/services/` — existing service patterns

### Requirements
- `.planning/REQUIREMENTS.md` — VAL-01 through VAL-07 (Phase 5 requirements)

### Architecture
- `.planning/PROJECT.md` — Layer 7 (Validation chain) and Layer 4 (Verified Knowledge Layer) descriptions
- `prd.md` §6 Layer 7 — full validation chain specification
- `prd.md` §6 Layer 4 — verified knowledge layer, `verified_qa_candidates` queueing specification

</canonical_refs>

<specifics>
## Specific Ideas

### From prd.md Layer 7 (Validation Chain)
> "Three sequential Claude calls wrapping every agent response. All Haiku-tier for cost."
> "Gatekeeper — 'Does this response address the user's actual question?' → pass | fail | needs_clarification"
> "Auditor — 'Is every factual claim in this response supported by the retrieved context?' → grounded | ungrounded | partial with citation spans"
> "Strategist — 'Is this response coherent, on-brand, and aligned with the agent's role?' → ship | revise | escalate"
> "Outputs are structured (Pydantic-validated), logged to Langfuse, and feed back into the eval system as production telemetry."
> "Persistent Auditor failures on a given retrieval pattern trigger a strategy re-synthesis flag."

### From prd.md Layer 4 (Verified Knowledge Layer) — M5 scope
> "When the Auditor classifies a production response as grounded with high confidence, the response becomes a candidate for promotion, surfaced in the owner's weekly digest."
> "M5 ships the candidate-marking and queueing; the approval UI lands in M8"

### Langfuse v4 constraint (from CLAUDE.md)
> "Langfuse v4 API only. Do not use pre-v4 Langfuse patterns — start_span()/start_generation() are gone."

### Celery patterns (from CLAUDE.md)
> "acks_late=True AND idempotency" on every task
> "Connection strings never in Celery task args. Tasks receive tenant_id; they fetch and decrypt from the control DB at runtime."
> "FastAPI never does work inline. All long-running operations go to Celery."

</specifics>

<deferred>
## Deferred Ideas

- Owner approval UI for `verified_qa_candidates` — M8
- Automatic promotion from `verified_qa_candidates` to `verified_qa` — M6 (sandbox) + M8 (production)
- Weekly digest surfacing promotion candidates — M10
- Strategy re-synthesis execution when flagged — M9
- Sampling rate configuration (100% vs lower rate for mature agents) — noted in PRD as configurable but not in M5 scope
- GraphRAG conversation insights — M10

</deferred>

---

*Phase: 05-validation-chain*
*Context gathered: 2026-05-23 via PRD Express Path (prd.md)*
