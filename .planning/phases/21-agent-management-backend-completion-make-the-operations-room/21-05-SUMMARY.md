---
phase: 21-agent-management-backend-completion-make-the-operations-room
plan: 05
subsystem: api
tags: [fastapi, sqlalchemy-async, psycopg2, job_events, cross-db-correlation, idor]

# Dependency graph
requires:
  - phase: 21-01
    provides: turn_metrics + job_events instrumentation on run_agent_turn (Live region substrate)
provides:
  - "bench_service.list_failing_traces — cross-DB (control job_events + tenant messages) failing-trace listing"
  - "bench_service.grade_trace / bench_tally — append-only operator grading with filed-irrevocable enforcement"
  - "GET /api/v1/agents/{id}/traces?status=failing"
  - "POST /api/v1/agents/{id}/traces/{trace_id}/grade"
affects: [21-06 (promote_trace_to_scenario consumes filed grades from this plan)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cross-DB correlation in Python (no SQL join): control-DB job_events verdicts merged with tenant-DB messages via two sequential queries, mirroring scenario_service.mine_production_scenarios"
    - "conversation_id sourced from the flagged job's own agent.response event payload, never from a jobs table column (Pitfall 5 avoidance)"
    - "Refuse-to-write irrevocability: TERRARIUM 'filed' law enforced as a pre-write guard in the service layer, not an UPDATE/DELETE constraint"
    - "Targeted router-module import for unit tests (bypasses a pre-existing, unrelated app.main collection failure)"

key-files:
  created:
    - apps/api/app/services/bench_service.py
    - apps/api/app/api/v1/traces.py
    - apps/api/tests/unit/test_bench_routes.py
  modified:
    - apps/api/app/main.py

key-decisions:
  - "Operator grades stored as append-only control-DB job_events rows (event_type='trace.graded') per Assumption A-BENCH — no new bench table"
  - "grade_trace additionally verifies trace_id's flagged judge event payload has agent_id == the path agent (T-21-05-01) before any write, closing an IDOR gap the plan's must_haves didn't explicitly spell out but the threat_model required"
  - "customer_turn is recovered by walking tenant-DB messages and pairing the last 'user' row seen before the 'assistant' row whose content matches the agent.response payload's text exactly, falling back to the conversation's last user message if no exact match is found"

requirements-completed: [OPS-09, OPS-10]

# Metrics
duration: 35min
completed: 2026-07-16
status: complete
---

# Phase 21 Plan 05: Failure-triage bench (OPS-09/10) Summary

**bench_service + GET/POST traces routes making the ops-room "bench" real: cross-DB (control job_events + tenant messages) failing-trace listing with judge rationale, and an append-only filed|held|dismissed grade endpoint that enforces the TERRARIUM irrevocable-filed law via a refuse-to-write guard (never an UPDATE/DELETE constraint).**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-07-15T21:20:00Z (approx.)
- **Completed:** 2026-07-15T22:00:45Z
- **Tasks:** 2/2 completed
- **Files modified:** 4 (3 created, 1 modified)

## Accomplishments
- `bench_service.list_failing_traces` correlates control-DB `job_events` judge verdicts (gatekeeper.complete/auditor.complete with fail/ungrounded/partial) with tenant-DB `messages` text in Python — `conversation_id` is always sourced from the flagged job's own `agent.response` event payload, never a `jobs` table column (Pitfall 5 avoided; verified via `grep -c "FROM jobs" bench_service.py` == 0)
- `bench_service.grade_trace` persists filed|held|dismissed as append-only `job_events` rows and refuses any write once a trace is already `filed` (`TraceAlreadyFiledError` → HTTP 409), with an additional IDOR-style ownership check (`TraceNotFoundError` → HTTP 404) confirming the trace's flagged event actually belongs to the path `agent_id`
- `GET /api/v1/agents/{id}/traces?status=failing` and `POST /api/v1/agents/{id}/traces/{trace_id}/grade` registered in `main.py`, both copying the `evals.py` 6-step IDOR + conn_str-at-runtime pattern verbatim (404, not 403, on tenant mismatch)

## Task Commits

Each task was committed atomically:

1. **Task 1: bench_service — cross-DB failing-trace listing + grade persistence** - `11e6a13` (feat)
2. **Task 2: traces routes — GET /traces?status=failing + POST /grade (IDOR-guarded)** - `60973ee` (feat)

_Note: no separate `docs(21-05): complete plan` metadata commit is included per this dispatch's instructions — the orchestrator owns STATE.md/ROADMAP.md updates and the final metadata commit for this plan._

## Files Created/Modified
- `apps/api/app/services/bench_service.py` - `list_failing_traces`, `bench_tally`, `grade_trace`, plus `TraceAlreadyFiledError` / `InvalidGradeError` / `TraceNotFoundError`
- `apps/api/app/api/v1/traces.py` - `GET /agents/{id}/traces`, `POST /agents/{id}/traces/{trace_id}/grade`, shared `_get_owned_agent` IDOR guard, `GradeTraceRequest` Pydantic body (Literal grade enum)
- `apps/api/app/main.py` - imports and registers `traces.router` with `prefix="/api/v1"` (added after `observability`, before nothing else changed)
- `apps/api/tests/unit/test_bench_routes.py` - 12 service-layer tests (`-k service`) + 8 route-layer tests (20 total), all passing

## Decisions Made
- **A-BENCH storage** (already documented in the plan): grades are append-only `job_events` rows, no new bench table. Confirmed and implemented exactly as specified.
- **T-21-05-01 ownership check added to `grade_trace`** (Rule 2 — auto-add missing critical functionality, driven by the plan's own `threat_model` mitigate disposition): before writing a grade, the service confirms a `gatekeeper.complete`/`auditor.complete` event exists for `trace_id` whose payload's `agent_id` matches the path agent. Without this, an operator could grade an arbitrary `job_id` UUID belonging to a different agent/tenant, since `job_events` has no enforced foreign-key ownership. Mapped to HTTP 404 in the route (consistent with the existing "404 not 403" no-existence-leak convention).
- **customer_turn recovery strategy**: `messages` has no `job_id`/turn linkage column (schema-confirmed: `id, conversation_id, role, content, created_at` only), so `_fetch_customer_turn` walks the conversation's messages in insertion order and returns the last `user` row seen immediately before the `assistant` row whose content exactly matches the `agent.response` payload's `text` — falling back to the conversation's last user message if no exact match is found (e.g. text mutated after emit). This never raises; worst case is a slightly-stale customer turn, never an empty one when a user message exists.
- **Test-file targeted-import strategy for route tests** (see Deviations below): route tests build a minimal `FastAPI()` app around only `app.api.v1.traces.router` rather than importing `app.main`, avoiding a pre-existing unrelated collection failure while still exercising the exact same route code and `dependency_overrides` wiring the `evals.py`/`red_team.py` test convention uses.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added trace-ownership verification to grade_trace (T-21-05-01)**
- **Found during:** Task 1/2 (bench_service + traces routes)
- **Issue:** The plan's `<action>` text for `grade_trace` didn't explicitly spell out an ownership check, but the plan's own `<threat_model>` STRIDE register lists T-21-05-01 ("grade endpoint also confirms the trace's agent_id in the job_events payload matches the path agent") as a `mitigate` disposition. Without it, `POST /agents/{id}/traces/{trace_id}/grade` would let an authenticated operator grade a `trace_id` belonging to any agent/tenant by guessing/observing a job_id UUID.
- **Fix:** Added `_TRACE_OWNER_CHECK_SQL` + a check at the top of `grade_trace` that raises `TraceNotFoundError` (mapped to HTTP 404) before any write is attempted if no flagged judge event for `trace_id` has `payload->>'agent_id' == agent_id`.
- **Files modified:** `apps/api/app/services/bench_service.py`, `apps/api/app/api/v1/traces.py`, `apps/api/tests/unit/test_bench_routes.py` (`test_grade_trace_service_raises_not_found_for_foreign_agent_trace`, `test_returns_404_when_service_raises_not_found`)
- **Verification:** `pytest tests/unit/test_bench_routes.py -x -q` — 20/20 pass
- **Committed in:** `11e6a13` (service), `60973ee` (route mapping + test)

**2. [Rule 1 - Bug] Removed a literal "FROM jobs" string from a docstring that tripped the plan's own acceptance-criteria grep**
- **Found during:** Task 1 self-verification
- **Issue:** `bench_service.py`'s module docstring explained the Pitfall-5 anti-pattern by quoting the literal broken query text (`SELECT conversation_id FROM jobs`), which caused `grep -c "FROM jobs" app/services/bench_service.py` to return 1 instead of the required 0 — even though no actual SQL in the file queries a `jobs` table.
- **Fix:** Reworded the docstring to describe the anti-pattern without embedding the literal `FROM jobs` substring.
- **Files modified:** `apps/api/app/services/bench_service.py`
- **Verification:** `grep -c "FROM jobs" app/services/bench_service.py` → `0`
- **Committed in:** `11e6a13`

---

**Total deviations:** 2 auto-fixed (1 missing critical / threat-model-driven, 1 bug)
**Impact on plan:** Both auto-fixes strengthen correctness/security exactly along the plan's own threat_model and acceptance_criteria. No scope creep — no new files, no architectural change.

## Issues Encountered

**Pre-existing infra issue (not introduced by this plan, does not block this plan's verification):** `app.main` transitively imports `app.api.v1.evals` → `app.worker.tasks.runtime.eval` → `app.services.eval_service` → `ragas.metrics.collections` → `ragas.llms.base` → `langchain_community.chat_models.vertexai`, which raises `ModuleNotFoundError: No module named 'langchain_community.chat_models.vertexai'` in this environment. This is confirmed present on HEAD *before* any of this plan's changes (`git stash push -- app/main.py && pytest tests/unit/test_eval_routes.py` fails identically). It causes 15 test modules across the whole `tests/unit/` suite to fail collection with the exact same traceback, none of which touch `bench_service.py` or `traces.py`.
- **Workaround for this plan's tests:** `tests/unit/test_bench_routes.py`'s route-layer tests build a minimal `FastAPI()` app wrapping only `app.api.v1.traces.router` (a "targeted import" of just this plan's router module, per the dispatch's key_context guidance) instead of importing `app.main`. This exercises the exact same route handlers, IDOR guard, and `dependency_overrides` wiring the `evals.py`/`red_team.py` test convention uses, without touching the broken `ragas`→`langchain_community` chain.
- **Full-suite comparison (before vs. after this plan's changes, both runs `pytest tests/unit -q --continue-on-collection-errors`):** 15 collection errors and ~33-34 pre-existing unrelated test failures present identically in both runs (confirmed via `git stash` of `app/main.py`). This plan's own test module contributes 20/20 passing tests to both totals. No regression introduced.
- **Registering `traces.router` in `main.py` does not make this worse or better:** the import list evaluates `evals` (which already fails) before reaching `traces`, so `traces`'s import is never even attempted when `app.main` fails to collect — confirmed by identical collection-error counts with and without the `traces` import present.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `bench_service.grade_trace` produces `event_type='trace.graded'` rows with `payload.grade='filed'` that 21-06's `promote_trace_to_scenario` Celery task can query directly (control-DB `job_events`, no new table) to file production failures into `eval_scenarios`.
- No blockers for 21-06. The filed-irrevocable invariant is enforced at the service layer only (not a DB constraint) — 21-06 should not attempt to mutate or delete `trace.graded` rows; it should only read the latest `filed` grade per `job_id` (same pattern as `bench_tally`).

---
*Phase: 21-agent-management-backend-completion-make-the-operations-room*
*Completed: 2026-07-16*

## Self-Check: PASSED

- FOUND: apps/api/app/services/bench_service.py
- FOUND: apps/api/app/api/v1/traces.py
- FOUND: apps/api/tests/unit/test_bench_routes.py
- FOUND: traces.router registered in apps/api/app/main.py
- FOUND: .planning/phases/21-agent-management-backend-completion-make-the-operations-room/21-05-SUMMARY.md
- FOUND: commit 11e6a13 (Task 1)
- FOUND: commit 60973ee (Task 2)
