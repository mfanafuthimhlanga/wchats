---
phase: 21-agent-management-backend-completion-make-the-operations-room
plan: 06
subsystem: api
tags: [alembic, celery, psycopg2, postgres, fastapi, eval-scenarios, provenance]

# Dependency graph
requires:
  - phase: 21-agent-management-backend-completion-make-the-operations-room (plan 03)
    provides: tenant migration chain through 0010 (retrieval_metrics)
  - phase: 21-agent-management-backend-completion-make-the-operations-room (plan 05)
    provides: bench_service (list_failing_traces, grade_trace, _fetch_customer_turn) and traces.py (filed | held | dismissed grading, TERRARIUM law)
provides:
  - "Tenant migration 0011: widened eval_scenarios.source CHECK (+production, +red_team) and new provenance/origin_trace_id columns, landed in one migration (Pitfall 2)"
  - "insert_provenance_scenario(conn, source, question, reference_answer, retrieved_contexts, provenance, origin_trace_id) shared insert helper in scenario_service.py"
  - "promote_trace_to_scenario Celery task (runtime queue, acks_late, idempotent on origin_trace_id) — app/worker/tasks/runtime/bench.py"
  - "GET /agents/{id}/eval-runs now returns a ledger block: born_in_production_count, red_team_count, authored_count"
affects: [21-08 (red-team finding containment — will call insert_provenance_scenario with source='red_team')]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dynamic CHECK-constraint discovery/drop via a DO $$ block querying pg_constraint/pg_attribute, instead of hardcoding an auto-generated constraint name"
    - "Shared *_provenance insert helper taking an already-open psycopg2 connection so callers control the transaction (idempotency pre-check + insert in one commit)"
    - "sys.modules stub for a single missing transitive import (langchain_community.chat_models.vertexai) to unit-test a route module that itself sits on the pre-existing broken ragas import chain, without touching app.main"

key-files:
  created:
    - apps/api/alembic_tenant/versions/0011_eval_scenarios_provenance.py
    - apps/api/app/worker/tasks/runtime/bench.py
    - apps/api/tests/unit/test_migration_0011.py
    - apps/api/tests/unit/test_promote_trace.py
  modified:
    - apps/api/app/services/scenario_service.py
    - apps/api/app/api/v1/evals.py
    - apps/api/app/worker/celery_app.py

key-decisions:
  - "The widened source CHECK is created under a stable explicit name (eval_scenarios_source_check_v2) rather than reusing the discovered auto-generated name, so re-running the migration is idempotent without a second discovery-and-diff pass."
  - "insert_provenance_scenario takes an open connection (not a conn_str) so promote_trace_to_scenario can run its idempotency pre-check and the insert on the same connection/transaction — this is also the shape 21-08's red-team file path will reuse."
  - "The ledger block is nested under a 'ledger' key in the GET /eval-runs response envelope (not flattened into the top level) to keep the ORRERY provenance counts visually and semantically distinct from the per-run eval_runs list."

patterns-established:
  - "Provenance-scenario writes: caller opens the connection, runs its own idempotency pre-check, calls insert_provenance_scenario, then commits — the helper itself never commits or closes."

requirements-completed: [OPS-11, OPS-12]

# Metrics
duration: ~25min
completed: 2026-07-15
status: complete
---

# Phase 21 Plan 06: Bench Flywheel Write Side (OPS-11/OPS-12) Summary

**Migration 0011 widens eval_scenarios.source to allow production/red_team provenance rows, promote_trace_to_scenario (acks_late + idempotent) files a graded trace into the golden suite, and GET /eval-runs now surfaces a born-in-production vs authored ledger.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-07-15 (UTC)
- **Tasks:** 3/3
- **Files modified/created:** 7

## Accomplishments
- Tenant migration 0011 dynamically discovers and drops whatever CHECK constraint governs `eval_scenarios.source` (no hardcoded auto-generated name), widens it to `('generated','mined','production','red_team')`, and adds nullable `provenance`/`origin_trace_id` columns — all in one migration, satisfying Pitfall 2's "must land together" requirement.
- `insert_provenance_scenario` shared helper in `scenario_service.py` — the single INSERT path both this plan's production promote and 21-08's red-team file will use.
- `promote_trace_to_scenario` Celery task: `acks_late=True`, runtime queue, `agent_id`/`trace_id`-only args (conn_str decrypted at runtime), idempotency pre-check on `origin_trace_id`, recovers the question/answer via the trace's own `agent.response` job_events payload (never `Job.conversation_id`, Pitfall 5).
- `GET /agents/{id}/eval-runs` now returns a `ledger` block (`born_in_production_count`, `red_team_count`, `authored_count`) computed via one additional `asyncio.to_thread(_query_tenant_db_sync)` round-trip in the same route; `provenance IS NULL` legacy rows always fold into `authored_count`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Migration 0011 — widen source CHECK + add provenance/origin_trace_id** - `cc31a33` (feat)
2. **Task 2: shared insert_provenance_scenario + promote_trace_to_scenario task** - `aaebc40` (test, RED) → `fb1ac0d` (feat, GREEN) — no refactor commit needed, implementation was clean on first pass
3. **Task 3: OPS-12 — born-in-production vs authored counts in GET /eval-runs** - `97f37ca` (feat)

**Plan metadata:** (this commit, following)

## Files Created/Modified
- `apps/api/alembic_tenant/versions/0011_eval_scenarios_provenance.py` - widened source CHECK + provenance/origin_trace_id columns + index, dynamic constraint discovery via `pg_constraint`
- `apps/api/tests/unit/test_migration_0011.py` - source assertions + integration-gated DB roundtrip (INSERT with `source='production'` proves the CHECK was actually widened, not just that columns exist)
- `apps/api/app/services/scenario_service.py` - added `insert_provenance_scenario(conn, source, question, reference_answer, retrieved_contexts, provenance, origin_trace_id)`
- `apps/api/app/worker/tasks/runtime/bench.py` - new: `promote_trace_to_scenario` Celery task
- `apps/api/app/worker/celery_app.py` - registered `app.worker.tasks.runtime.bench` in the task `include` list
- `apps/api/app/api/v1/evals.py` - added `_LEDGER_SQL` + ledger computation/response block in `list_eval_runs`
- `apps/api/tests/unit/test_promote_trace.py` - promote_trace_to_scenario unit tests (signature, acks_late, idempotency, happy path, no-response-event) + ledger route tests (`-k ledger`)

## Decisions Made
- Widened CHECK constraint uses a stable explicit name (`eval_scenarios_source_check_v2`) instead of reusing whatever auto-generated name was discovered, so a second migration run is idempotent via a simple existence check rather than a repeat discovery-and-diff.
- `insert_provenance_scenario` takes an already-open `conn` (not a `conn_str`) — the caller (this plan's `promote_trace_to_scenario`, and 21-08's red-team file path) owns the idempotency pre-check and the commit, keeping both in one transaction.
- Ledger nested under a `"ledger"` key in the `GET /eval-runs` response, separate from the `"eval_runs"` list — mirrors the DOMAIN-NOTES "ORRERY ledger" naming as a distinct mechanism from the per-run VITALS list.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing test infra] Stubbed the pre-existing `langchain_community.chat_models.vertexai` import gap to unit-test the ledger route**
- **Found during:** Task 3 (OPS-12 ledger tests)
- **Issue:** `app.api.v1.evals` transitively imports `app.worker.tasks.runtime.eval` → `app.services.eval_service` → `ragas.metrics.collections` → `ragas.llms.base` → `langchain_community.chat_models.vertexai`, which raises `ModuleNotFoundError` in this environment (confirmed pre-existing on HEAD before this plan — `pytest tests/unit/test_eval_routes.py` fails to collect identically, and 21-05's `test_bench_routes.py` documents the same chain for `traces.py`/`app.main`). Unlike `traces.py`, `evals.py` itself sits on this broken chain, so the "targeted router import" trick alone wasn't enough to test the new ledger route.
- **Fix:** Installed a minimal stub module (`ChatVertexAI = MagicMock()`) into `sys.modules["langchain_community.chat_models.vertexai"]` at the top of `test_promote_trace.py`, before importing `app.api.v1.evals`, letting the rest of the real `ragas`/`eval_service` import chain proceed normally. This does not touch application code, `app.main`, or attempt to fix the underlying `ragas`/`langchain-community` version mismatch (out of scope for this plan) — it only unblocks importing `evals.py` directly inside the test process.
- **Files modified:** apps/api/tests/unit/test_promote_trace.py (test-only)
- **Verification:** `pytest tests/unit/test_promote_trace.py -x -q -k ledger` — 4 passed; `pytest tests/unit/test_bench_routes.py tests/unit/test_migration_0011.py tests/unit/test_scenario_service.py` — no regressions (39 passed, 1 skipped)
- **Committed in:** 97f37ca (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (test-infra workaround for a pre-existing, unrelated broken import chain)
**Impact on plan:** No application code changed by this deviation — test-only. The underlying `ragas`/`langchain-community` version mismatch remains open and out of scope; `test_eval_routes.py` still fails to collect exactly as before this plan.

## Issues Encountered
- `python -m pytest` via the default `python` on PATH failed with a corrupt-install error (`0x80070003`) unrelated to this plan — resolved by invoking the project's `.venv/Scripts/python.exe` directly for all test runs and acceptance-criteria checks.
- The local disk filled to 100% during summary write; resolved by running `uv cache clean` (freed ~812 MiB, non-destructive — package build cache only), no project files were touched.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `insert_provenance_scenario` is ready for 21-08 to call with `source='red_team'` — same shared insert path, no new schema work needed.
- Migration chain is at tenant head 0011; `promote_trace_to_scenario` is registered in `celery_app.py`'s include list but is not yet dispatched from anywhere (21-05's `grade_trace` route intentionally does not dispatch it in this plan — wiring the dispatch-on-'filed' call is left for a future plan/verification pass, per this plan's explicit "do not edit traces.py here" scope boundary).
- Live-gated verification (migration 0011 up/down roundtrip against a real tenant Neon, and a real `promote_trace_to_scenario` Celery run) is deferred to `/gsd-verify-work 21`, per this plan's `<verification>` section.

---
*Phase: 21-agent-management-backend-completion-make-the-operations-room*
*Completed: 2026-07-15*

## Self-Check: PASSED

All 7 created/modified files confirmed present on disk; all 4 task commit hashes (cc31a33, aaebc40, fb1ac0d, 97f37ca) confirmed present in git log.
