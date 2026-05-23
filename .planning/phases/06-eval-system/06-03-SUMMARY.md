---
phase: "06"
plan: "06-03"
title: "scenario_service.py — scenario generator (EVL-02) + production conversation mining (EVL-03)"
status: complete
completed_at: "2026-05-23"
subsystem: eval
tags: [anthropic, haiku, psycopg2, eval_scenarios, tool_use, mining]

requires:
  - phase: "06-01"
    provides: "eval_scenarios table in tenant DB (migration 0005), eval threshold settings"

provides:
  - "generate_scenarios_from_chunks(): Claude Haiku forced tool-use scenario generator (EVL-02)"
  - "store_scenarios(): psycopg2 INSERT INTO eval_scenarios ON CONFLICT DO NOTHING (idempotent)"
  - "generate_eval_suite_for_agent(): batch generator from tenant knowledge chunks"
  - "_fetch_messages_for_conversation(): tenant DB message lookup for conversation mining"
  - "mine_production_scenarios(): cross-DB production failure miner (EVL-03)"

affects: ["06-05", "06-06"]

tech-stack:
  added: []
  patterns:
    - "SCENARIO_TOOL dict with submit_scenarios tool_choice — same forced-structured-output pattern as validation_service.py"
    - "Cross-DB mining: query control DB job_events → correlate via jobs table → query tenant DB messages"
    - "Mined scenarios store reference_answer='' to signal no ground truth (filtered at eval run time)"

key-files:
  created:
    - "apps/api/app/services/scenario_service.py"
  modified: []

key-decisions:
  - "D-12 LOCKED: Claude API direct (not Agent SDK), Haiku model for scenario generation"
  - "D-13 LOCKED: source='generated' for chunk-derived scenarios"
  - "D-16 LOCKED: source='mined' for production conversation scenarios"
  - "conversation_id is NOT in job_events payload (it is a Celery task arg in validators.py, not emitted) — mining correlates via jobs table job_id → conversation_id"
  - "Mined scenarios with reference_answer='' are stored but excluded from Ragas eval by run_eval_suite task (ContextRecall + Faithfulness require reference)"
  - "No asyncio anywhere — all calls synchronous per D-12"

patterns-established:
  - "SCENARIO_TOOL schema: submit_scenarios with scenarios[].{question, reference_answer, scenario_category} enum"
  - "store_scenarios() psycopg2 try/finally with ON CONFLICT DO NOTHING — same idempotency pattern as validators.py"
  - "generate_eval_suite_for_agent() batching: min(5, max(1, len(chunks)//(num_scenarios//5))) chunks per batch"

requirements-completed:
  - EVL-02
  - EVL-03

duration: 35min
completed: 2026-05-23
---

# 06-03 Summary — scenario_service.py

**Claude Haiku scenario generator (forced tool-use, source='generated') + cross-DB production conversation miner (source='mined') writing to tenant DB eval_scenarios table**

## Performance

- **Duration:** 35 min
- **Completed:** 2026-05-23
- **Tasks:** 2
- **Files created:** 1

## Accomplishments

- Task 1: `generate_scenarios_from_chunks()` using Claude Haiku with forced `tool_choice={"type":"tool","name":"submit_scenarios"}` — exact pattern from `validation_service.py`. SCENARIO_TOOL enforces `scenarios[].{question, reference_answer, scenario_category}` schema with enum `["factual","edge_case","out_of_scope","multi_step"]`. Returns source='generated' dicts with `retrieved_contexts` attached from source chunks.
- Task 1: `store_scenarios()` uses psycopg2 try/finally with `INSERT INTO eval_scenarios ON CONFLICT DO NOTHING` — idempotent across retries.
- Task 1: `generate_eval_suite_for_agent()` fetches up to 100 recent tenant chunks, computes batch_size, processes batches until num_scenarios reached.
- Task 2: `_fetch_messages_for_conversation()` queries tenant DB `messages` table by `conversation_id::uuid` — returns `[{role, content}]`.
- Task 2: `mine_production_scenarios()` implements cross-DB join strategy: Step 1 queries control DB `job_events` for `verdict IN ('fail','ungrounded','partial')` for the agent; Step 2 correlates via `jobs` table to get `conversation_id`, then fetches messages from tenant DB. Mined scenarios have `source='mined'` and `reference_answer=''`.

## Task Commits

File was committed as part of prior execution context:

1. **Task 1 + Task 2: scenario_service.py (both tasks)** - `da44ac3` (committed alongside 06-04 neon branch tests)

Note: Both tasks were committed atomically in a single commit. The content satisfies all acceptance criteria from 06-03-PLAN.md.

## Files Created/Modified

- `apps/api/app/services/scenario_service.py` — 397 lines. Module docstring per spec. ANTHROPIC_CLIENT, HAIKU_MODEL, SCENARIO_TOOL, generate_scenarios_from_chunks, store_scenarios, generate_eval_suite_for_agent, _fetch_messages_for_conversation, mine_production_scenarios.

## Decisions Made

- **Cross-DB join reality**: `conversation_id` is a Celery task arg in `validators.py` (not emitted in job_events payload). Mining correlates job_id → conversation_id via the `jobs` table in the control DB, then fetches messages from the tenant DB. Scenarios where the question cannot be recovered are skipped.
- **Empty reference_answer for mined scenarios**: `reference_answer=""` is stored honestly (D-16 intent). The downstream `run_eval_suite` task filters these before building the Ragas `EvaluationDataset` (ContextRecall and Faithfulness require a reference answer).
- **No asyncio**: All calls synchronous per D-12 LOCKED.

## Deviations from Plan

None — plan executed as specified. The cross-DB mining strategy adapts to the actual `validators.py` payload structure (question not in emit payload) as flagged in the plan's read_first note.

## Verification Results

All plan verification commands passed:

```
# From apps/api/:
python -c "import ast; ast.parse(open('app/services/scenario_service.py').read()); print('parse ok')"
# → parse ok

python -c "from app.services.scenario_service import generate_scenarios_from_chunks, store_scenarios, generate_eval_suite_for_agent, mine_production_scenarios; print('all imports ok')"
# → all imports ok
```

Content checks confirmed:
- No `asyncio` in file
- `source='generated'` present
- `source='mined'` present
- `ANTHROPIC_CLIENT = anthropic.Anthropic()` present
- `SCENARIO_TOOL` with name `"submit_scenarios"` present
- `tool_choice={"type": "tool", "name": "submit_scenarios"}` present
- `block.type == "tool_use"` iteration present
- `INSERT INTO eval_scenarios` with `ON CONFLICT DO NOTHING` present
- `reference_answer=""` for mined scenarios present

## Acceptance Criteria

- [x] `apps/api/app/services/scenario_service.py` exists
- [x] File contains `ANTHROPIC_CLIENT = anthropic.Anthropic()`
- [x] File contains `SCENARIO_TOOL` with name `"submit_scenarios"`
- [x] File contains `def generate_scenarios_from_chunks(`
- [x] File contains `tool_choice={"type": "tool", "name": "submit_scenarios"}`
- [x] File contains `block.type == "tool_use"` iteration (matching validation_service.py pattern)
- [x] `generate_scenarios_from_chunks` returns dicts with `source="generated"` (D-13)
- [x] File contains `def store_scenarios(`
- [x] File contains `INSERT INTO eval_scenarios` with `ON CONFLICT DO NOTHING`
- [x] File contains `def generate_eval_suite_for_agent(`
- [x] File does NOT use asyncio (synchronous Haiku calls — D-12)
- [x] AST parse exits 0 from `apps/api/`
- [x] File contains `def mine_production_scenarios(`
- [x] File contains `def _fetch_messages_for_conversation(`
- [x] `mine_production_scenarios` queries `job_events` for `verdict IN ('fail','ungrounded','partial')` for the given `agent_id`
- [x] Mined scenarios have `source='mined'` (D-16)
- [x] File contains `reference_answer=""` for mined scenarios (honest about missing ground truth)
- [x] File does NOT use asyncio (synchronous only)
- [x] All function imports succeed

## Next Phase Readiness

- `scenario_service.py` is ready for use by the `generate_eval_suite` and `mine_eval_scenarios_beat` Celery tasks (06-05)
- `generate_eval_suite_for_agent()` accepts `agent_id` + `tenant_conn_str` — matches the Celery task arg pattern (CLAUDE.md rule: no conn strings in task args; task fetches and decrypts at runtime)
- `mine_production_scenarios()` accepts a `control_db` SQLAlchemy Session — callers use `get_sync_db()` context manager

---
*Phase: 06-eval-system*
*Completed: 2026-05-23*
