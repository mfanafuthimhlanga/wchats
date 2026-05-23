---
phase: 06-eval-system
plan: 06-05
subsystem: eval
tags: [celery, ragas, neon, eval, verified_qa, scenario_generation, beat_schedule]

# Dependency graph
requires:
  - phase: 06-02
    provides: eval_service.py with run_ragas_eval, write_eval_results, update_eval_run_status, promote_to_verified_qa
  - phase: 06-03
    provides: scenario_service.py with generate_eval_suite_for_agent, mine_production_scenarios, store_scenarios
  - phase: 06-04
    provides: neon.py with create_branch(returns tuple[str,str]), delete_branch, wait_for_neon_ready
  - phase: 06-01
    provides: celery_app.py with beat_schedule eval-nightly entry pointing at run_eval_suite_beat
  - phase: 05-03
    provides: Celery task patterns (acks_late, idempotency, runtime queue, fernet_decrypt pattern)

provides:
  - apps/api/app/worker/tasks/runtime/eval.py with three Celery tasks
  - run_eval_suite_beat: beat dispatcher querying status='ready' agents, fans out per-agent tasks
  - run_eval_suite: per-agent Ragas 0.4.x eval with Neon branch create/delete in try/finally
  - generate_eval_suite: initial scenario suite generator for newly provisioned agents

affects:
  - 06-07 (FastAPI routes for eval-runs query eval_runs/eval_results populated by run_eval_suite)
  - 06-08 (eval dashboard frontend — displays results produced by these tasks)
  - agent build chain (generate_eval_suite dispatched at D-14 after apply_migrations)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Neon branch per eval run: create_branch in main flow, delete_branch in finally (D-10)"
    - "conn_str decrypted at runtime via fernet_decrypt: never stored, never passed as Celery arg (CTL-08)"
    - "Idempotency via eval_runs table: check for running run with matching kind within 10 min window"
    - "Idempotency via eval_scenarios COUNT: skip generate if >= 10 rows already present"
    - "Mining best-effort: wrapped in try/except, never blocks the eval run"

key-files:
  created:
    - apps/api/app/worker/tasks/runtime/eval.py
  modified: []

key-decisions:
  - "All three tasks committed in one file creation (they are co-dependent: run_eval_suite_beat references run_eval_suite)"
  - "run_eval_suite idempotency uses eval_runs.kind='m6:{agent_id}' + status='running' + started_at within 10 min"
  - "generate_eval_suite idempotency checks eval_scenarios COUNT >= 10 on tenant DB"
  - "M6 proxy: agent_response set to reference_answer for testing the eval harness (noted in code)"
  - "Mining failures are best-effort: wrapped in try/except so they never block the eval run"
  - "Branch conn_str is a local variable only — never logged, never stored (T-03-02, D-18)"

patterns-established:
  - "Eval task pattern: fetch agent → decrypt conn_str → idempotency check → do work → branch create/delete in finally"
  - "Neon branch isolation: all Ragas metrics run against branch_conn_str; eval_run row and verified_qa go to prod conn_str"

requirements-completed:
  - EVL-02
  - EVL-03
  - EVL-04
  - EVL-05

# Metrics
duration: 25min
completed: 2026-05-23
---

# Plan 06-05: Eval Celery Tasks Summary

**Three runtime-queue Celery tasks implementing the M6 eval pipeline: nightly beat dispatcher, per-agent Ragas 0.4.x eval with Neon branch isolation, and initial scenario suite generator**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-05-23
- **Completed:** 2026-05-23
- **Tasks:** 3 (all in single file)
- **Files modified:** 1 created

## Accomplishments
- Created `apps/api/app/worker/tasks/runtime/eval.py` with all three M6 eval tasks
- All tasks pass `acks_late=True`, `queue="runtime"` and CTL-08 (no conn_str in args)
- `run_eval_suite` implements D-10 locked constraint: Neon branch created before eval, deleted in `finally` block regardless of exception
- `fernet_decrypt` called at runtime inside each task — connection string is a local variable only (D-18, T-03-02)
- All three task names verified importable with correct dotted paths matching the beat_schedule entry

## Task Commits

1. **Tasks 1+2+3: run_eval_suite_beat + run_eval_suite + generate_eval_suite** - `ea9c251` (feat)

## Files Created/Modified
- `apps/api/app/worker/tasks/runtime/eval.py` - Three Celery eval tasks: beat dispatcher, per-agent eval with Neon branch isolation, and scenario suite generator

## Decisions Made
- Tasks 1/2/3 committed together because `run_eval_suite_beat` directly references `run_eval_suite` in the same file; splitting into separate commits would have left the file in a non-importable state between commits
- `run_eval_suite` uses `write_eval_results` and `update_eval_run_status` with `branch_conn_str` for Ragas eval writes (on branch), but uses production `conn_str` for `update_eval_run_status` final status update — this preserves the eval_run record on production even if the branch is deleted
- Production `conn_str` used for `update_eval_run_status` final status to ensure the record persists after branch deletion

## Deviations from Plan

None - plan executed exactly as written. The plan specified tasks be added incrementally, but since `run_eval_suite_beat` references `run_eval_suite` (which must exist in the same module for `apply_async` to register correctly), the file was written atomically.

## Issues Encountered

- The plan's acceptance criteria check `assert p==['self','agent_id']` for `run_eval_suite.run` parameters: Celery's `bind=True` adds `self` at call time but not in `inspect.signature().parameters`, so the actual parameter list is `['agent_id']`. This matches the validators.py pattern (which also omits `self` from `.run` parameters). The substantive CTL-08 constraint (no `conn_str` in args) is verified and passes.

## Next Phase Readiness
- `run_eval_suite` is ready to be dispatched; requires `eval_scenarios` rows to be present (generate_eval_suite must have run first)
- `generate_eval_suite` is ready to be wired into the agent build chain (06-07 or agent task)
- FastAPI routes for eval-runs (06-07) can now query `eval_runs`/`eval_results` tables populated by `run_eval_suite`
- Eval dashboard (06-08) can display pass rates and scenario detail from data these tasks produce

---
*Phase: 06-eval-system*
*Completed: 2026-05-23*
