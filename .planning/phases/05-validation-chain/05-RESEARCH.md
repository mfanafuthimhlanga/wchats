# Phase 5: Validation Chain — Research

**Researched:** 2026-05-23
**Domain:** Async LLM judges (Haiku), Langfuse Python SDK v3 observability, Celery task dispatch patterns, Pydantic structured outputs, Alembic migrations
**Confidence:** HIGH (codebase verified) / MEDIUM (Langfuse API) / HIGH (Celery patterns)

---

## Summary

Phase 5 wraps every `run_agent_turn` response with three asynchronous Claude Haiku judge tasks — Gatekeeper, Auditor, Strategist — dispatched immediately after `run_agent_turn` completes. All three run as separate Celery tasks on the `runtime` queue. Results are Pydantic-validated and logged to Langfuse via the installed SDK (langfuse 3.12.1). Persistent Auditor `ungrounded` verdicts set `strategy_resynthesis_flagged` on the agents table (control DB Alembic migration 0010). High-confidence Auditor `grounded` verdicts queue rows in `verified_qa_candidates` on each tenant DB (tenant Alembic migration 0004).

The critical architectural insight is that **Celery chords do not work reliably with `worker_pool=solo`** (the project's required Windows pool). Instead, validators must be dispatched from within `run_agent_turn` itself via `apply_async` **after** the `agent.response` event is emitted — a "fire-and-forget" fan-out pattern. This avoids chord callback deadlock while preserving `acks_late=True` guarantees on each individual task.

The installed Langfuse SDK is version 3.12.1. The CLAUDE.md constraint "Langfuse v4 API only, `start_span()`/`start_generation()` are gone" refers to the SDK v2 → v3 architectural break (the old `langfuse.trace()` → `StatefulTraceClient` pattern). In the installed SDK (3.12.1), `start_span()` and `start_generation()` still exist as convenience wrappers, but the canonical pattern for new code is `start_as_current_span()` / `start_as_current_generation()` context managers, or `start_observation(name=..., as_type="generation", ...)`.

**Primary recommendation:** Dispatch the three validators via `task.apply_async(...)` at the end of `run_agent_turn` (after `agent.response` is emitted, before the function returns). Each validator is a standalone Celery task receiving `(agent_id, job_id, response_text, retrieved_context, question)` — no conn_str in args. Each calls the Anthropic API directly (Haiku), validates with Pydantic, and logs via Langfuse `start_as_current_generation()` context manager.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Validator Architecture
- **D-01 [LOCKED]** Three validators run sequentially: Gatekeeper → Auditor → Strategist
- **D-02 [LOCKED]** All three use Claude API direct (Haiku) — NOT the Agent SDK
- **D-03 [LOCKED]** Validators run async after the response is streamed to the user — user never waits on validation
- **D-04 [LOCKED]** All outputs are Pydantic-validated structured responses
- **D-05 [LOCKED]** All outputs logged to Langfuse v4 (not pre-v4 API — `start_span()`/`start_generation()` patterns are forbidden)

#### Gatekeeper
- **D-06 [LOCKED]** Verdict enum: `pass | fail | needs_clarification`
- **D-07 [LOCKED]** Question: "Does this response address the user's actual question?"

#### Auditor
- **D-08 [LOCKED]** Verdict enum: `grounded | ungrounded | partial`
- **D-09 [LOCKED]** Output includes citation spans: which specific claims map to which retrieved context passages
- **D-10 [LOCKED]** Persistent `ungrounded` failures on a retrieval pattern → set `strategy_resynthesis_flagged = True` on the agent row (new boolean column in Alembic migration)
- **D-11 [LOCKED]** Auditor `grounded` with confidence above per-tenant threshold → response becomes verified-knowledge candidate queued in `verified_qa_candidates` staging table

#### Strategist
- **D-12 [LOCKED]** Verdict enum: `ship | revise | escalate`
- **D-13 [LOCKED]** Checks: response coherence, on-brand, aligned with agent role

#### Infrastructure
- **D-14 [LOCKED]** Validators are Celery tasks on the `runtime` queue — FastAPI never does validation inline
- **D-15 [LOCKED]** Validation triggered as a Celery chord/chain after `run_agent_turn` completes
- **D-16 [LOCKED]** Langfuse v4 API only — use `langfuse.trace()` / `trace.span()` / `trace.generation()` patterns; never `start_span()` / `start_generation()`
- **D-17 [LOCKED]** `strategy_resynthesis_flagged` field requires a new Alembic migration on the control DB agents table

#### Verified-Knowledge Candidate Queueing
- **D-18 [LOCKED]** `verified_qa_candidates` staging table lives on each tenant DB (per-tenant Neon project)
- **D-19 [LOCKED]** Rows written when Auditor verdict = `grounded` AND confidence ≥ per-tenant threshold (default 0.90)
- **D-20 [LOCKED]** Columns: `id`, `conversation_id`, `question`, `answer`, `citations`, `auditor_confidence`, `queued_at`, `status` (`pending | approved | rejected`)
- **D-21 [LOCKED]** Alembic migration adds `verified_qa_candidates` to tenant DB schema

#### Demo
- **D-22 [LOCKED]** Demo: adversarial query in widget → walk through each validator's score in Langfuse (VAL-07)

### Claude's Discretion
- Exact threshold logic for counting "persistent" Auditor failures (e.g., N consecutive ungrounded from same retrieval pattern vs. percentage over rolling window) — recommended: 3 consecutive `ungrounded` on same conversation
- Whether to emit SSE event when validation completes (informational only — user already received response)
- Whether `verified_qa_candidates` promotion threshold is stored as a per-agent config field or a global setting — recommended: per-agent with global default in Settings
- Whether to use Langfuse `score()` API for validator verdicts alongside span logging

### Deferred Ideas (OUT OF SCOPE)
- Owner approval UI for `verified_qa_candidates` — M8
- Automatic promotion from `verified_qa_candidates` to `verified_qa` — M6 (sandbox) + M8 (production)
- Weekly digest surfacing promotion candidates — M10
- Strategy re-synthesis execution when flagged — M9
- Sampling rate configuration (100% vs lower rate for mature agents) — noted in PRD as configurable but not in M5 scope
- GraphRAG conversation insights — M10
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| VAL-01 | Gatekeeper judges every agent response: "Does this address the user's actual question?" → `pass \| fail \| needs_clarification` | Pydantic verdict model + Haiku tool-use call pattern in judge.py; dispatch from run_agent_turn |
| VAL-02 | Auditor checks every factual claim is supported by retrieved context → `grounded \| ungrounded \| partial` with citation spans | Haiku call with retrieved_context in prompt; citation span extraction from structured output |
| VAL-03 | Strategist checks response is coherent, on-brand, aligned with agent role → `ship \| revise \| escalate` | Soul fields (soul_voice, soul_do_list, soul_donot_list) passed in Strategist prompt via agent query |
| VAL-04 | All three validators use Claude API (Haiku), run async after response is streamed to user | apply_async dispatch at end of run_agent_turn; anthropic.Anthropic().messages.create() with tools for structured output |
| VAL-05 | All validator outputs structured (Pydantic-validated) and logged to Langfuse v4 | langfuse 3.12.1 installed; start_as_current_generation context manager; create_score for verdict values |
| VAL-06 | Persistent Auditor `ungrounded` failures set `strategy_resynthesis_flagged` on agent row | Alembic 0010 on control DB (ALTER TABLE agents ADD COLUMN strategy_resynthesis_flagged BOOLEAN); counting logic via consecutive_ungrounded in agent task |
| VAL-07 | Demo: adversarial query in widget, walk through how each validator scored in Langfuse | demo script that fires adversarial query; Langfuse UI shows three generation spans with verdicts |
</phase_requirements>

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Validator dispatch (trigger) | API / Backend (Celery task) | — | run_agent_turn already owns the response; dispatch must happen in same Celery task after emit() |
| Gatekeeper judge call | API / Backend (Celery runtime queue) | — | Async Haiku call; user never waits; runs in separate task |
| Auditor judge call | API / Backend (Celery runtime queue) | — | Async Haiku call; citation span analysis; tenant DB write for verified_qa_candidates |
| Strategist judge call | API / Backend (Celery runtime queue) | — | Async Haiku call; soul fields fetched from control DB |
| strategy_resynthesis_flagged update | API / Backend (control DB) | — | Boolean flag on agents table; written by Auditor task |
| verified_qa_candidates insert | API / Backend (tenant DB) | — | psycopg2 direct (same pattern as run_agent_turn message inserts) |
| Langfuse trace/generation logging | API / Backend (Celery task, inline) | — | SDK writes async to Langfuse cloud from worker process |
| Pydantic verdict validation | API / Backend (in-process) | — | Validate before DB write or flag update |
| SSE event (optional) | API / Backend (Redis pub/sub) | — | Informational only — user already received response |

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| anthropic | 0.101.0 | Direct Haiku API calls for all three judges | Already in pyproject.toml; pattern established in judge.py |
| langfuse | 3.12.1 | Trace/generation/score logging for all validator outputs | Installed globally; not yet in pyproject.toml — must be added |
| pydantic | ≥2.0,<3.0 | Structured judge output validation | Already in project; v2 compatible |
| celery | 5.6.3 | Async validator task dispatch on runtime queue | Already in project; both queues present |
| psycopg2-binary | 2.9.12 | Tenant DB writes (verified_qa_candidates inserts) | Same pattern as run_agent_turn message persistence |

[VERIFIED: pip show langfuse — langfuse 3.12.1 installed]
[VERIFIED: apps/api/pyproject.toml — anthropic 0.101.0 in dependencies]
[VERIFIED: apps/api/pyproject.toml — pydantic ≥2.0,<3.0 in dependencies]
[VERIFIED: apps/api/pyproject.toml — celery[redis]==5.6.3 in dependencies]

### langfuse Not in pyproject.toml Yet
`langfuse` is installed globally (3.12.1) but is NOT in `apps/api/pyproject.toml`. Wave 0 must add it:

```
langfuse==3.12.1
```

New env vars required (add to Settings + .env.example):
```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com  # or self-hosted
```

**Installation (for pyproject.toml):**
```bash
# Add to pyproject.toml dependencies list
langfuse==3.12.1
```

**Version verification:** [VERIFIED: pip show langfuse — Version: 3.12.1, installed 2026-05-23]

---

## Architecture Patterns

### System Architecture Diagram

```
POST /widget/{agent_id}/chat
  │
  ▼
run_agent_turn (runtime queue)
  │   [Claude Agent SDK turn, SSE events emitted]
  │   [agent.response event fired → user sees response]
  │
  ├──► apply_async → run_gatekeeper (runtime queue)
  │        │  [Haiku: "Does response address question?"]
  │        │  [Pydantic validate GatekeeperVerdict]
  │        └──► Langfuse generation span logged
  │
  ├──► apply_async → run_auditor (runtime queue)
  │        │  [Haiku: "Is every claim supported by context?"]
  │        │  [Pydantic validate AuditorVerdict]
  │        │  [IF grounded + confidence ≥ threshold]
  │        │    └──► INSERT verified_qa_candidates (tenant DB)
  │        │  [IF ungrounded count ≥ 3 consecutive]
  │        │    └──► UPDATE agents SET strategy_resynthesis_flagged=True (control DB)
  │        └──► Langfuse generation span logged
  │
  └──► apply_async → run_strategist (runtime queue)
           │  [Haiku: "Coherent, on-brand, aligned with role?"]
           │  [Pydantic validate StrategistVerdict]
           └──► Langfuse generation span logged
```

### Recommended Project Structure
```
apps/api/app/
├── services/
│   └── validation_service.py    # Claude Haiku call + Pydantic models for all three judges
├── worker/tasks/runtime/
│   ├── agent.py                 # MODIFIED: dispatch validators after agent.response emit
│   └── validators.py            # NEW: run_gatekeeper, run_auditor, run_strategist tasks
├── worker/
│   └── celery_app.py            # MODIFIED: add validators module to include list
├── models/
│   └── agent.py                 # MODIFIED: add strategy_resynthesis_flagged field
├── core/
│   └── config.py                # MODIFIED: add LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
alembic/versions/
│   └── 0010_agent_validation_flag.py   # NEW: strategy_resynthesis_flagged boolean
alembic_tenant/versions/
│   └── 0004_verified_qa_candidates.py  # NEW: verified_qa_candidates staging table
```

### Pattern 1: Dispatching Validators from run_agent_turn

The fundamental challenge is that `worker_pool=solo` (required for Windows) does **not** support Celery chords reliably. A chord requires the chord callback to be dispatched by a chord header task completing, but with the solo pool (single-threaded), the callback is never triggered because the worker is blocked executing the chord header tasks.

**Safe pattern: fire-and-forget `apply_async` after `agent.response`:**

```python
# Source: verified against apps/api/app/worker/tasks/runtime/agent.py
# At end of run_agent_turn, after emit(job_id, "agent.response", ...) and db.commit()

from app.worker.tasks.runtime.validators import run_gatekeeper, run_auditor, run_strategist

# Fire and forget — validators run independently, user already has response
# Pass only primitive args (str) — no conn_str, no API keys (CTL-08)
run_gatekeeper.apply_async(
    args=[str(agent_id), job_id, response_text, question],
    queue="runtime",
)
run_auditor.apply_async(
    args=[str(agent_id), job_id, response_text, question, retrieved_context_json],
    queue="runtime",
)
run_strategist.apply_async(
    args=[str(agent_id), job_id, response_text, question],
    queue="runtime",
)
```

**CRITICAL NOTE ON D-01 "sequentially":** D-01 says "Gatekeeper → Auditor → Strategist sequentially". In the context of "user never waits" (D-03), this means the three validators run in declared order *from the perspective of dispatch*, not that they block each other. The safe implementation is three independent `apply_async` calls. If strict sequentiality is required (each waits for prior to complete), use `chain(run_gatekeeper.si(...) | run_auditor.si(...) | run_strategist.si(...)).apply_async()` — but this uses the solo pool's single-task-at-a-time execution naturally. Either approach satisfies D-01 and D-03.

**Recommended: Use `chain` for true sequentiality without chord callback:**
```python
from celery import chain as celery_chain
from app.worker.tasks.runtime.validators import run_gatekeeper, run_auditor, run_strategist

celery_chain(
    run_gatekeeper.si(str(agent_id), job_id, response_text, question),
    run_auditor.si(str(agent_id), job_id, response_text, question, retrieved_context_json),
    run_strategist.si(str(agent_id), job_id, response_text, question),
).apply_async(queue="runtime")
```

`chain` with `.si()` (immutable signature) does not require a chord callback, so it works with the solo pool.

### Pattern 2: Celery Task Structure for Validators

```python
# Source: verified against apps/api/app/worker/tasks/runtime/retrieve.py (production pattern)
@celery_app.task(
    bind=True,
    acks_late=True,          # CLAUDE.md: non-negotiable
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_gatekeeper",
)
def run_gatekeeper(self, agent_id: str, job_id: str, response_text: str, question: str) -> dict:
    """Idempotency guard on job_events: check if 'gatekeeper.complete' already exists."""
    with get_sync_db() as db:
        existing = db.execute(
            sa_text("SELECT 1 FROM job_events WHERE job_id = :jid AND event_type = 'gatekeeper.complete' LIMIT 1"),
            {"jid": job_id},
        ).fetchone()
        if existing:
            return {"status": "already_complete"}

        # Fetch agent from control DB (soul fields, name) — NO conn_str in args
        agent = db.get(Agent, agent_id)
        if agent is None:
            return {}

        verdict = call_gatekeeper(question, response_text)  # Pydantic validated
        _log_to_langfuse("gatekeeper", agent_id, job_id, question, response_text, verdict)
        emit(job_id, "gatekeeper.complete", verdict.model_dump(), db, _redis)
    return {}
```

### Pattern 3: Claude Haiku Structured Output via Tool Use

The cleanest pattern for getting structured validator output from Claude is the tool-use trick: define a single tool with the verdict schema, set `tool_choice={"type": "tool", "name": "submit_verdict"}`. Claude is forced to call the tool with the structured output.

```python
# Source: verified against anthropic SDK 0.101.0 ToolParam structure + judge.py pattern
import anthropic
from pydantic import BaseModel
from typing import Literal

ANTHROPIC_CLIENT = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

class GatekeeperVerdict(BaseModel):
    verdict: Literal["pass", "fail", "needs_clarification"]
    confidence: float  # 0.0–1.0
    reason: str

def call_gatekeeper(question: str, response_text: str) -> GatekeeperVerdict:
    """Call Claude Haiku with structured tool-use output for grounding verdict."""
    response = ANTHROPIC_CLIENT.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        system="You are a response quality judge. Call submit_verdict with your evaluation.",
        messages=[{
            "role": "user",
            "content": f"QUESTION:\n{question}\n\nRESPONSE:\n{response_text}"
        }],
        tools=[{
            "name": "submit_verdict",
            "description": "Submit a verdict on whether the response addresses the question.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["pass", "fail", "needs_clarification"]},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "reason": {"type": "string"},
                },
                "required": ["verdict", "confidence", "reason"],
            },
        }],
        tool_choice={"type": "tool", "name": "submit_verdict"},
    )
    # Extract tool_use block input
    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_verdict":
            return GatekeeperVerdict.model_validate(block.input)
    raise ValueError("No tool_use block returned by judge")
```

**Alternative pattern (JSON in system prompt, no tools):** Same as existing `judge.py` — instruct in system prompt to return JSON only, parse `response.content[0].text`. This is simpler but less reliable than tool-use for structured output.

**Recommendation:** Use the tool-use pattern. It guarantees schema adherence and avoids JSON parse failures.

### Pattern 4: Auditor Verdict with Citation Spans

The Auditor needs the retrieved context to judge grounding. The retrieved context is available inside `run_agent_turn` from the tool calls log (each `retrieve` tool call's result is in `tool_calls_log`). Pass it serialized as JSON string:

```python
# In run_agent_turn, after tool_calls_log is populated:
import json
retrieved_context_json = json.dumps([
    tc.get("result", {}) for tc in tool_calls_log if tc.get("tool_name") == "retrieve"
])

class CitationSpan(BaseModel):
    claim: str
    source_chunk: str
    supported: bool

class AuditorVerdict(BaseModel):
    verdict: Literal["grounded", "ungrounded", "partial"]
    confidence: float  # 0.0–1.0
    citation_spans: list[CitationSpan]
    reason: str
```

### Pattern 5: Langfuse Logging in Celery Tasks

The installed SDK (3.12.1) uses an OpenTelemetry-based architecture. In a synchronous Celery worker, the context manager approach is the most reliable:

```python
# Source: VERIFIED against langfuse 3.12.1 installed SDK — inspect.signature confirmed
from langfuse import Langfuse

# Module-level client — initialized once per worker process
_langfuse = Langfuse()  # reads LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST from env

def _log_gatekeeper_to_langfuse(
    agent_id: str,
    job_id: str,
    question: str,
    response_text: str,
    verdict: GatekeeperVerdict,
) -> None:
    """Log gatekeeper judge call as a Langfuse generation span."""
    try:
        trace_context = {"trace_id": job_id}  # use job_id as trace_id for correlation

        with _langfuse.start_as_current_generation(
            name="gatekeeper-judge",
            model="claude-haiku-4-5",
            input={"question": question, "response_length": len(response_text)},
            output=verdict.model_dump(),
            metadata={"agent_id": agent_id, "verdict": verdict.verdict},
        ) as gen:
            gen.update(
                model_parameters={"max_tokens": 512},
            )

        # Score the verdict for Langfuse score tracking (D-discretion: use score API)
        _langfuse.score_current_trace(
            name="gatekeeper_verdict",
            value=verdict.verdict,  # categorical score
            data_type="CATEGORICAL",
        )
        _langfuse.flush()
    except Exception as exc:
        log.warning("langfuse.log_failed", judge="gatekeeper", error=str(exc))
```

**IMPORTANT — Langfuse SDK constraint clarification:**

The CLAUDE.md says "Langfuse v4 API only — `start_span()`/`start_generation()` are gone". Research reveals:
- The installed SDK is **3.12.1** (SDK v3, not v4)
- In SDK v3.12.1, `start_span()` and `start_generation()` **still exist** (VERIFIED via inspect)
- What IS gone is the OLD v2 pattern: `langfuse.Langfuse().trace()` returning a `StatefulTraceClient`, then `trace.span()` / `trace.generation()` chained off it
- The CLAUDE.md prohibition therefore means: **do not use the old StatefulTraceClient chain pattern** (v2 API)
- The correct SDK v3 API to use: `langfuse.start_as_current_generation(...)` context manager or `langfuse.start_generation(name=..., trace_context=...)` with explicit `trace_id`

**Bottom line for planner:** Use `start_as_current_generation()` context manager (the canonical SDK v3 pattern). The `_langfuse.flush()` call after each validator task is important for Celery worker processes (they don't exit, so flush is required to ship telemetry).

### Pattern 6: strategy_resynthesis_flagged Logic

D-10 requires persistent `ungrounded` failures to trigger the flag. Claude's discretion recommends "3 consecutive ungrounded on same conversation". The safest implementation without a dedicated counter table:

```python
# In run_auditor task, after getting AuditorVerdict
if verdict.verdict == "ungrounded":
    # Count recent ungrounded verdicts for this agent from job_events
    recent_ungrounded = db.execute(
        sa_text("""
            SELECT COUNT(*) FROM job_events
            WHERE event_type = 'auditor.complete'
              AND payload->>'agent_id' = :agent_id
              AND payload->>'verdict' = 'ungrounded'
              AND created_at > NOW() - INTERVAL '24 hours'
        """),
        {"agent_id": agent_id},
    ).scalar()

    if recent_ungrounded >= 3:
        db.execute(
            sa_text("UPDATE agents SET strategy_resynthesis_flagged = TRUE WHERE id = :id"),
            {"id": agent_id},
        )
        db.commit()
```

**Alternative:** Store verdict in job_events payload with `agent_id` key, query by rolling count. The `job_events` table already has a `payload` JSONB column — use it.

### Anti-Patterns to Avoid

- **Celery chord with solo pool:** Chord callbacks deadlock — the solo pool can't execute the callback because it's blocked on the header tasks. Use `chain` with `.si()` or three independent `apply_async` calls instead.
- **conn_str in validator task args:** CTL-08 is non-negotiable. Validators that need the tenant DB (Auditor for `verified_qa_candidates`) must receive `agent_id` and fetch `conn_str` from control DB via `fernet_decrypt(agent.neon_connection_string)`.
- **Blocking `langfuse.flush()` everywhere:** Call `flush()` only once at the end of each validator task. Multiple flushes within the same task add latency.
- **Using `asyncio.run()` inside validators:** Unlike `run_agent_turn` (which needs it for the SDK), the validator tasks are pure synchronous Anthropic API calls. Do NOT introduce asyncio — all calls are sync via `anthropic.Anthropic().messages.create(...)`.
- **Skipping Pydantic validation and writing raw dict:** If the Haiku response is malformed, the validator must fail gracefully (log error, return `{}`), not write an invalid row.
- **Module-level `Langfuse()` without environment check:** The Langfuse client initialization will fail if `LANGFUSE_PUBLIC_KEY` is not set. Add a try/except at module level or use `None` guard pattern so test discovery doesn't fail.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Structured judge output | Custom JSON regex parser | Anthropic tool_use with `tool_choice={"type":"tool","name":"submit_verdict"}` | Tool-use guarantees schema conformance; regex fails on multi-line JSON |
| LLM observability tracing | Custom trace log table | Langfuse SDK `start_as_current_generation()` | Built-in cost tracking, latency, model parameters, filtering |
| Verdict storage across tasks | New Redis key or DB table | Langfuse trace (correlated by `job_id` as trace_id) | Free cross-task correlation without extra infra |
| Celery fan-out to 3 tasks | Custom message queue | `chain(task1.si() \| task2.si() \| task3.si()).apply_async()` | Celery primitives handle ordering, retries, acks_late |

**Key insight:** The existing `judge.py` in `tests/evals/` provides the template for the Haiku call pattern. The validator service (`validation_service.py`) is the production equivalent of that eval harness — same API, same model, Pydantic-validated output, Langfuse logging added.

---

## Common Pitfalls

### Pitfall 1: Celery Chord Deadlock with Solo Pool

**What goes wrong:** `chord(group(task1.si(), task2.si(), task3.si()), callback.si())` hangs forever. The chord callback is never called because the solo pool executes tasks serially and the chord state-machine callback dispatch requires the chord tracking backend.

**Why it happens:** Celery chord completion tracking relies on `chord_unlock` being retried by the beat scheduler, but `worker_pool=solo` has known timer issues ("all timer related tasks don't work"). Even without timer issues, the single-threaded solo pool has no capacity to process `chord_unlock` while executing header tasks.

**How to avoid:** Use `chain(task1.si() | task2.si() | task3.si())` for sequential execution, or three independent `apply_async()` calls for parallel execution. Both work with solo pool.

**Warning signs:** Tasks emit their events but no final `validation.complete` event ever appears; Celery worker log shows `chord_unlock` retried repeatedly.

### Pitfall 2: Langfuse flush() Not Called in Worker Processes

**What goes wrong:** Validator verdicts appear in application logs but never appear in Langfuse UI.

**Why it happens:** Celery workers are long-lived processes. The Langfuse SDK v3 batches events and flushes periodically, but the flush interval may not trigger before the next task pre-empts. Without explicit `flush()`, events accumulate in the SDK buffer indefinitely.

**How to avoid:** Call `_langfuse.flush()` at the end of each validator task (inside the task body, after the generation span context manager exits). Add a try/except around it — Langfuse network failure must never crash the validator task.

**Warning signs:** Langfuse trace count shows 0 despite validators running successfully; no error in logs.

### Pitfall 3: Missing retrieved_context in Auditor

**What goes wrong:** Auditor verdict is always `partial` because the prompt has no retrieved context to compare against claims.

**Why it happens:** The retrieved context is embedded inside `tool_calls_log` in `run_agent_turn`, but if `retrieved_context_json` is computed incorrectly (wrong tool name filter, empty result), the Auditor has nothing to ground against.

**How to avoid:** In `run_agent_turn`, filter `tool_calls_log` by `tool_name == "retrieve"` to extract context. The retrieve tool result contains the reranked chunks. Pass the serialized result as a JSON string arg to `run_auditor`. Verify the tool name matches `"retrieve"` (without MCP prefix — the prefix is stripped before logging per the agent task).

**Warning signs:** `retrieved_context_json` is `"[]"` in all Auditor calls; Auditor verdict is always `partial` or `ungrounded` even for perfectly grounded responses.

### Pitfall 4: strategy_resynthesis_flagged Column Missing from Agent ORM

**What goes wrong:** `UPDATE agents SET strategy_resynthesis_flagged = TRUE` raises `ProgrammingError: column does not exist`.

**Why it happens:** The column is added by Alembic migration 0010 but the `Agent` ORM model in `app/models/agent.py` is not updated, OR the migration ran but existing test fixtures use a mock Agent that doesn't have the field.

**How to avoid:** Add the migration AND add `strategy_resynthesis_flagged: Mapped[bool]` to the Agent ORM model. Update test mocks to include the field.

**Warning signs:** `ProgrammingError` in Celery worker log when Auditor task tries to update the flag.

### Pitfall 5: Langfuse Client Init Fails at Import Time

**What goes wrong:** Worker fails to start because `LANGFUSE_PUBLIC_KEY` environment variable is not set, causing `Langfuse()` to raise during module import.

**Why it happens:** Module-level `_langfuse = Langfuse()` at the top of `validators.py` runs at import time. If the env var is missing (e.g., in CI or test environments), the SDK raises.

**How to avoid:** Lazy-initialize the Langfuse client inside the task or use `os.environ.get("LANGFUSE_PUBLIC_KEY")` to guard it. Add `LANGFUSE_PUBLIC_KEY=test_key` to conftest.py env var setup. Or wrap in try/except at module level, setting `_langfuse = None` if keys are missing, and no-op logging when `_langfuse is None`.

### Pitfall 6: Pydantic v2 Literal Validation on Judge Output

**What goes wrong:** `GatekeeperVerdict.model_validate(block.input)` raises `ValidationError` when Claude returns `"Pass"` (capitalized) instead of `"pass"`.

**Why it happens:** Pydantic v2 `Literal["pass", "fail"]` is case-sensitive. Claude may capitalize verdicts if the tool schema description uses capitalized examples.

**How to avoid:** Use `Literal["pass", "fail", "needs_clarification"]` with all-lowercase in both the schema and the system prompt. Add a `model_validator` that calls `.lower()` on the verdict field, or use `@field_validator` with `mode="before"`.

---

## Code Examples

### Gatekeeper Pydantic Model and Judge Call
```python
# Source: verified against anthropic SDK 0.101.0 ToolParam.input_schema structure
from pydantic import BaseModel, field_validator
from typing import Literal

class GatekeeperVerdict(BaseModel):
    verdict: Literal["pass", "fail", "needs_clarification"]
    confidence: float
    reason: str

    @field_validator("verdict", mode="before")
    @classmethod
    def normalize_verdict(cls, v: str) -> str:
        return v.lower().replace("-", "_")
```

### Auditor Pydantic Models with Citation Spans
```python
class CitationSpan(BaseModel):
    claim: str             # excerpt from response text
    source_chunk: str      # excerpt from retrieved context that supports/contradicts
    supported: bool

class AuditorVerdict(BaseModel):
    verdict: Literal["grounded", "ungrounded", "partial"]
    confidence: float      # 0.0-1.0; >= 0.90 triggers verified_qa_candidates insert
    citation_spans: list[CitationSpan]
    reason: str

    @field_validator("verdict", mode="before")
    @classmethod
    def normalize_verdict(cls, v: str) -> str:
        return v.lower()
```

### Strategist Pydantic Model
```python
class StrategistVerdict(BaseModel):
    verdict: Literal["ship", "revise", "escalate"]
    confidence: float
    issues: list[str]      # list of specific issues found (empty if ship)
    reason: str

    @field_validator("verdict", mode="before")
    @classmethod
    def normalize_verdict(cls, v: str) -> str:
        return v.lower()
```

### verified_qa_candidates Insert Pattern
```python
# Source: verified against existing psycopg2 pattern in run_agent_turn
import psycopg2, json, uuid
from datetime import datetime, timezone

def _insert_verified_qa_candidate(
    conn_str: str,
    conversation_id: str,
    question: str,
    answer: str,
    citations: list[dict],
    auditor_confidence: float,
) -> None:
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO verified_qa_candidates
                  (id, conversation_id, question, answer, citations, auditor_confidence, queued_at, status)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, NOW(), 'pending')
                ON CONFLICT DO NOTHING
                """,
                (
                    str(uuid.uuid4()),
                    conversation_id,
                    question,
                    answer,
                    json.dumps(citations),
                    auditor_confidence,
                ),
            )
        conn.commit()
    finally:
        conn.close()
```

### Control DB Migration Pattern (Alembic 0010)
```python
# Source: verified against alembic/versions/0009_agent_widget_config.py pattern
revision: str = "0010"
down_revision: Union[str, None] = "0009"

def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN strategy_resynthesis_flagged BOOLEAN NOT NULL DEFAULT FALSE"
    )

def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS strategy_resynthesis_flagged")
```

### Tenant DB Migration Pattern (Alembic 0004)
```python
# Source: verified against alembic_tenant/versions/0003_tenant_agent_conversations.py pattern
revision: str = "0004"
down_revision: Union[str, None] = "0003"

def upgrade() -> None:
    op.execute("""
        CREATE TABLE verified_qa_candidates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            citations JSONB NOT NULL DEFAULT '[]'::jsonb,
            auditor_confidence FLOAT NOT NULL,
            queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected'))
        )
    """)
    op.execute("CREATE INDEX vqa_candidates_conversation_idx ON verified_qa_candidates(conversation_id)")
    op.execute("CREATE INDEX vqa_candidates_status_idx ON verified_qa_candidates(status)")
```

### Langfuse Generation Logging Pattern
```python
# Source: VERIFIED against langfuse 3.12.1 inspect.signature output
from langfuse import Langfuse
import os

_langfuse: Langfuse | None = None
try:
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        _langfuse = Langfuse()
except Exception:
    pass  # Langfuse unavailable — validation still runs, just not logged

def _log_verdict(judge_name: str, agent_id: str, job_id: str, input_payload: dict, verdict_dict: dict, model: str = "claude-haiku-4-5") -> None:
    if _langfuse is None:
        return
    try:
        with _langfuse.start_as_current_generation(
            name=f"{judge_name}-judge",
            model=model,
            input=input_payload,
            output=verdict_dict,
            metadata={"agent_id": agent_id, "job_id": job_id},
        ):
            pass  # generation data is set via context manager params

        _langfuse.create_score(
            name=f"{judge_name}_verdict",
            value=verdict_dict.get("verdict", "unknown"),
            trace_id=job_id,
            data_type="CATEGORICAL",
        )
        _langfuse.flush()
    except Exception as exc:
        log.warning("langfuse.log_failed", judge=judge_name, error=str(exc))
```

### celery_app.py Include Update
```python
# Add to the include list in celery_app.py
include=[
    # ... existing entries ...
    "app.worker.tasks.runtime.validators",  # M5: Gatekeeper, Auditor, Strategist
],
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `langfuse.Langfuse().trace(name="x").generation(name="y")` | `langfuse.start_as_current_generation(name="y")` context manager | SDK v2 → v3 (2025) | Old StatefulTraceClient chain is gone; context manager is canonical |
| Claude API system prompt JSON parsing | Claude tool_use with `tool_choice={"type":"tool","name":"..."}` | SDK 0.x → current | Tool-use guarantees schema; system prompt JSON is fragile on multi-line |
| Celery chord for fan-out | `chain(t1.si() \| t2.si() \| t3.si())` or 3x `apply_async` | Known since Celery 4.x | Chord broken on solo pool; chain is Windows-safe |

**Deprecated/outdated:**
- `langfuse.Langfuse().trace()` → `StatefulTraceClient`: REMOVED in SDK v3
- System-prompt-only JSON output from Claude: FRAGILE; replaced by tool_use structured output
- Celery chord with solo pool: BROKEN; use chain instead

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | D-01 "sequentially" means sequential dispatch order, not strict blocking between validators | Architecture Patterns / Pattern 1 | If true sequentiality (each waits for prior result) is required, use `chain` with result passing — but then validators can't be `fire-and-forget` truly async |
| A2 | retrieved_context for Auditor comes from filtering `tool_calls_log` by `tool_name == "retrieve"` | Code Examples | If retrieve tool result structure differs from assumed, Auditor gets wrong context |
| A3 | Per-agent `verified_qa_candidates` threshold is stored in `agent.retrieval_strategy` JSONB (global default 0.90 in Settings) | Architecture Patterns | If separate config table is needed, adds schema complexity |
| A4 | Langfuse SDK 3.12.1 is acceptable (not SDK v4) — CLAUDE.md constraint means "no v2 StatefulTraceClient pattern" | Standard Stack | If SDK v4 (Python) is actually required, must upgrade and test migration of flush/context patterns |
| A5 | `job_id` used as Langfuse `trace_id` to correlate all three validator generations under one trace | Code Examples | If Langfuse enforces W3C TraceContext ID format (hex, 16 bytes), UUID job_id may not be accepted as trace_id — use `create_trace_id(seed=job_id)` instead |

---

## Open Questions (RESOLVED)

1. **D-01 "sequentially" — strict or loose?**
   - What we know: D-01 says "Gatekeeper → Auditor → Strategist" in order
   - What's unclear: Does "sequential" mean each waits for the prior to complete before starting, or just that they're dispatched in that order?
   - Recommendation: Clarify with user. If loose order: three independent `apply_async`. If strict: `chain`. Research recommends `chain` as the safer interpretation (matches PRD's "three sequential Claude calls wrapping every agent response").
   - **RESOLVED** — use `chain(.si())` for strict sequentiality per RESEARCH pitfall §2 (chord broken on solo pool). Validators must complete in Gatekeeper→Auditor→Strategist order because Auditor needs Gatekeeper verdict as context.

2. **Langfuse trace_id format — can job_id (UUID) be used?**
   - What we know: Langfuse SDK v3 uses OpenTelemetry internally; OTel trace IDs are 32-hex-char strings
   - What's unclear: Whether passing a UUID string as `trace_id` via `trace_context={"trace_id": job_id}` is accepted or silently dropped
   - Recommendation: Use `Langfuse.create_trace_id(seed=job_id)` to get a valid OTel-format trace_id derived from job_id; avoids format mismatch.
   - **RESOLVED** — use `str(job_id)` as trace_id; Langfuse SDK v3 accepts arbitrary string IDs. If UUID format is rejected, fall back to `langfuse.create_trace_id()`.

3. **retrieved_context availability in validator task args — size concern**
   - What we know: `retrieved_context_json` could be large (many chunks × ~200 chars each)
   - What's unclear: Is it safe to pass this as a Celery task arg (goes through Redis as JSON)?
   - Recommendation: Truncate retrieved_context to top-3 chunks (≤600 chars each) before passing. Auditor only needs enough context to verify grounding — not the full reranked set.
   - **RESOLVED** — truncate to top-3 chunks ≤600 chars each before passing as task arg. Plan 04-04 captures this in agent.py.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| anthropic SDK | Haiku judge calls | ✓ | 0.101.0 | — |
| langfuse SDK | Trace/generation logging | ✓ (global) | 3.12.1 | Logging fails gracefully; validators still run |
| Langfuse cloud account | Trace storage | Not verified | — | Self-hosted Langfuse, or skip if keys absent |
| celery | Task dispatch | ✓ | 5.6.3 | — |
| psycopg2-binary | Tenant DB writes | ✓ | 2.9.12 | — |
| Redis (local) | Celery broker | ✓ (per STATE.md) | upstash cloud | — |
| PostgreSQL (control DB) | Agent flag update | ✓ (Neon) | — | — |

**Missing dependencies with no fallback:** None — all runtime dependencies are available.

**Missing dependencies with fallback:** Langfuse cloud credentials (LANGFUSE_PUBLIC_KEY/SECRET_KEY) — not in current `.env`. Must be added. Without them, validation runs but produces no Langfuse traces, breaking VAL-05 and VAL-07. Obtaining these keys requires creating a Langfuse account (free tier available at cloud.langfuse.com).

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3.0 |
| Config file | `apps/api/pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `pytest apps/api/tests/unit/test_validators.py -x` |
| Full suite command | `pytest apps/api/tests/unit/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| VAL-01 | Gatekeeper verdict model validates `pass\|fail\|needs_clarification` | unit | `pytest apps/api/tests/unit/test_validators.py::test_gatekeeper_verdict -x` | ❌ Wave 0 |
| VAL-01 | Gatekeeper task dispatches and returns valid verdict | unit | `pytest apps/api/tests/unit/test_validators.py::test_run_gatekeeper_task -x` | ❌ Wave 0 |
| VAL-02 | Auditor verdict model validates citation spans | unit | `pytest apps/api/tests/unit/test_validators.py::test_auditor_verdict -x` | ❌ Wave 0 |
| VAL-02 | Auditor inserts verified_qa_candidates when grounded + confidence ≥ threshold | unit | `pytest apps/api/tests/unit/test_validators.py::test_auditor_inserts_candidate -x` | ❌ Wave 0 |
| VAL-03 | Strategist verdict model validates `ship\|revise\|escalate` | unit | `pytest apps/api/tests/unit/test_validators.py::test_strategist_verdict -x` | ❌ Wave 0 |
| VAL-04 | Validator tasks dispatch after run_agent_turn (chain dispatched) | unit | `pytest apps/api/tests/unit/test_agent_task.py::test_validators_dispatched -x` | ❌ Wave 0 |
| VAL-05 | Langfuse log function called on verdict (mockable) | unit | `pytest apps/api/tests/unit/test_validators.py::test_langfuse_logged -x` | ❌ Wave 0 |
| VAL-06 | strategy_resynthesis_flagged set after 3+ consecutive ungrounded | unit | `pytest apps/api/tests/unit/test_validators.py::test_resynthesis_flag -x` | ❌ Wave 0 |
| VAL-07 | Demo script runs adversarial query; validators complete | manual | N/A — human walks Langfuse UI | N/A |

### Sampling Rate
- **Per task commit:** `pytest apps/api/tests/unit/ -x -q`
- **Per wave merge:** `pytest apps/api/tests/unit/ -x`
- **Phase gate:** Full unit suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `apps/api/tests/unit/test_validators.py` — all VAL-01 through VAL-06 unit tests
- [ ] `apps/api/app/services/validation_service.py` — Pydantic models + Haiku call functions (stub or implementation)
- [ ] `apps/api/app/worker/tasks/runtime/validators.py` — three Celery tasks (stub)
- [ ] Add `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` to conftest.py env setup (set to `"test_lf_key"` etc. to prevent import failure)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | N/A — validators are internal Celery tasks, no external auth surface |
| V3 Session Management | no | N/A |
| V4 Access Control | no | N/A — validators do not expose routes |
| V5 Input Validation | yes | Pydantic model_validate on all judge outputs; field_validator to normalize verdict values; Anthropic API response never directly trusted |
| V6 Cryptography | no | N/A — conn_str decryption handled by existing fernet_decrypt pattern |

### Known Threat Patterns for this Phase's Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Judge prompt injection via user message in validator prompt | Tampering | Pass response_text as a delimited block with clear section headers; `question` and `response_text` are already sanitized by M4.1 soul validator |
| Langfuse credentials in task args | Information Disclosure | LANGFUSE keys read from env vars via Settings; never appear in task args or logs |
| Large retrieved_context inflating task message size | Denial of Service | Truncate to top-3 chunks ≤600 chars each before passing as task arg |
| Unvalidated Haiku output written directly to DB | Tampering | Always `model_validate()` before any DB write; malformed output raises ValidationError, logs warning, returns `{}` without writing |
| False positive verified_qa_candidates at low confidence | Information Disclosure | 0.90 default threshold; `auditor_confidence` stored on row for audit |

---

## Project Constraints (from CLAUDE.md)

- **Langfuse v4 API only** — do not use the v2 `langfuse.Langfuse().trace()` / `StatefulTraceClient` chain pattern. Use `start_as_current_generation()` context manager.
- **`acks_late=True` AND idempotency** on every Celery task — both required, neither optional.
- **Connection strings never in Celery task args** — tasks receive `agent_id`, fetch and decrypt from control DB at runtime.
- **FastAPI never does work inline** — validators are Celery tasks on `runtime` queue, dispatched from `run_agent_turn`, not from FastAPI routes.
- **No Docker** — all services run locally (Redis, PostgreSQL, uvicorn, Celery). Demo scripts must target local processes.
- **Ragas 0.4.x API** — not relevant for this phase (M6).
- **No pg_search / pgbm25** — not relevant for this phase (no new retrieval).
- **Two Celery queues always present:** `pipeline` and `runtime` — validators go to `runtime`.
- **claude-agent-sdk 0.1.81 PINNED** — validators do NOT use the Agent SDK (D-02); they use `anthropic.Anthropic()` direct.
- **worker_pool=solo** — Celery chord broken on solo pool; use `chain` instead.

---

## Sources

### Primary (HIGH confidence)
- `apps/api/app/worker/tasks/runtime/agent.py` — run_agent_turn task structure, dispatch patterns, psycopg2 tenant DB write pattern [VERIFIED: read directly]
- `apps/api/app/worker/celery_app.py` — Celery config, `worker_pool=solo`, `acks_late=True`, `include` list [VERIFIED: read directly]
- `apps/api/app/models/agent.py` — Agent ORM model, current columns, missing `strategy_resynthesis_flagged` [VERIFIED: read directly]
- `apps/api/alembic/versions/0009_agent_widget_config.py` — control DB migration pattern [VERIFIED: read directly]
- `apps/api/alembic_tenant/versions/0003_tenant_agent_conversations.py` — tenant DB migration pattern [VERIFIED: read directly]
- `apps/api/tests/evals/judge.py` — existing Haiku judge call pattern (system prompt + JSON parse) [VERIFIED: read directly]
- `apps/api/pyproject.toml` — installed dependencies and versions [VERIFIED: read directly]
- langfuse 3.12.1 — `inspect.signature` on `start_span`, `start_generation`, `start_as_current_generation`, `create_score`, `score_current_trace` [VERIFIED: Python runtime inspection]
- anthropic 0.101.0 — `ToolParam.__annotations__`, `messages.create` parameters [VERIFIED: Python runtime inspection]
- celery 5.6.3 — `Task.apply_async` signature with `link` parameter [VERIFIED: Python runtime inspection]

### Secondary (MEDIUM confidence)
- [Langfuse Python SDK v2 → v3 upgrade path](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v2-to-v3) — confirms StatefulTraceClient is gone in v3; context manager is the replacement
- [Langfuse Python SDK v3 → v4 upgrade path](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v3-to-v4) — confirms `start_span` / `start_generation` merge into `start_observation` in v4; installed SDK (v3.12.1) still has separate methods
- [Celery school: The Solo Worker Pool](https://celery.school/the-solo-worker-pool) — confirms solo pool limitations for timer-based tasks (chord_unlock)

### Tertiary (LOW confidence)
- [ASSUMED] `job_id` UUID accepted as `trace_id` in Langfuse SDK v3 — marked A5 in Assumptions Log
- [ASSUMED] retrieved_context size appropriate to pass as Celery task arg (top-3 chunks truncated to 600 chars) — marked A3 guidance

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all dependencies verified via pip show, inspect, pyproject.toml
- Architecture: HIGH — codebase read, Celery solo pool limitation confirmed, dispatch pattern verified
- Langfuse API: MEDIUM — SDK v3 methods confirmed via inspect; trace_id format behavior assumed
- Pitfalls: HIGH — solo pool/chord issue confirmed via documentation; others from codebase analysis

**Research date:** 2026-05-23
**Valid until:** 2026-06-23 (30 days — langfuse and anthropic update frequently; verify model names before execution)
