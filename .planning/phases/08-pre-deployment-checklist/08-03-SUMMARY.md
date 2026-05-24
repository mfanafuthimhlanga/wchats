---
phase: 08-pre-deployment-checklist
plan: "03"
subsystem: api
tags: [celery, redis, postgresql, psycopg2, claude-agent-sdk, asyncio, deployment-checklist]

# Dependency graph
requires:
  - phase: 08-pre-deployment-checklist/08-02
    provides: deployment_service.py (run_orchestrator, _fetch_*_sync, DeploymentReport)
  - phase: 08-pre-deployment-checklist/08-01
    provides: ChecklistRun ORM model, checklist_runs control DB table (migration 0011)
provides:
  - run_deployment_checklist Celery task (runtime queue, acks_late=True)
  - Dual-DB split: control DB via ORM for checklist_runs; tenant DB via psycopg2 for signals
  - 60-minute idempotency guard against control DB
  - 7-step lifecycle: fetch→idempotency→insert→collect→orchestrate→update-complete→update-failed
affects:
  - 08-04 (deployment API routes — imports and dispatches this task)
  - 08-05 (demo script — polls checklist_run status created by this task)
  - 08-07 (E2E test — exercises this task end-to-end)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - asyncio shim pattern: _call_orchestrator_async thin awaitable wraps _run_orchestrator_loop to avoid nested asyncio.run inside asyncio.wait_for
    - dual-DB split in Celery task: get_sync_db() ORM for control DB writes; psycopg2 _fetch_*_sync for tenant DB reads
    - graceful signal degradation: each _fetch_*_sync wrapped in try/except with empty-defaults fallback

key-files:
  created:
    - apps/api/app/worker/tasks/runtime/deployment.py
  modified: []

key-decisions:
  - "_call_orchestrator_async thin shim: run_orchestrator is a sync bridge with its own asyncio.run. To pass it to asyncio.wait_for we need an awaitable, so _call_orchestrator_async calls _run_orchestrator_loop directly instead of calling run_orchestrator (avoids nested asyncio.run)"
  - "60-minute idempotency window (vs 30-minute in red_team.py) because deployment checklist involves Sonnet call with 120s timeout — generous buffer prevents false idempotency skips"
  - "Each _fetch_*_sync signal wrapped individually in try/except with empty-defaults: partial signal failure must not abort the whole run"
  - "conn_str never in task kwargs — only agent_id dispatched; conn_str fetched and decrypted inside task body (CTL-08 / T-08-03-01)"

patterns-established:
  - "Idempotency against control DB via ORM: use select(ChecklistRun).where(...created_at > text('now() - interval N minutes')) with scalar_one_or_none()"
  - "Control DB lifecycle management: 3 separate get_sync_db() blocks (INSERT, UPDATE complete, UPDATE failed) avoids session leakage across long-running async work"
  - "asyncio shim for run_orchestrator: _call_orchestrator_async awaitable calls service _run_orchestrator_loop directly when the bridge function is synchronous"

requirements-completed: [DEP-01, DEP-02, DEP-03]

# Metrics
duration: 3min
completed: 2026-05-24
---

# Phase 8 Plan 03: run_deployment_checklist Celery Task Summary

**run_deployment_checklist Celery task with acks_late=True, 60-min idempotency guard, dual-DB split (ORM for control DB checklist_runs; psycopg2 for tenant DB signals), asyncio.wait_for bridge, and full status lifecycle management**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-05-24T12:22:20Z
- **Completed:** 2026-05-24T12:25:20Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments

- Created `run_deployment_checklist` Celery task with full 7-step execution pipeline
- Enforces dual-DB split: control DB via `get_sync_db()` ORM for `checklist_runs` writes; psycopg2 `_fetch_*_sync` functions for tenant DB signal collection
- 60-minute idempotency window prevents duplicate concurrent runs; control DB ORM check (not tenant DB psycopg2, matching T-08-03-04)
- `conn_str` fetched from control DB and decrypted at runtime — never in task kwargs (CTL-08, T-08-03-01)
- `asyncio.wait_for(timeout=120.0)` guard prevents runaway Sonnet calls (T-08-03-03)
- Graceful signal degradation: each `_fetch_*_sync` wrapped in try/except with empty-defaults so a missing tenant table doesn't abort the run

## Task Commits

Each task was committed atomically:

1. **Task 1: run_deployment_checklist Celery task — full 7-step implementation** - `f6fde48` (feat)

**Plan metadata:** (committed with SUMMARY below)

## Files Created/Modified

- `apps/api/app/worker/tasks/runtime/deployment.py` - run_deployment_checklist Celery task; 311 lines; acks_late=True, queue=runtime, max_retries=2; full 7-step pipeline; _call_orchestrator_async shim

## Decisions Made

- **asyncio shim pattern:** `run_orchestrator` is a sync bridge function with its own internal `asyncio.run`. To use `asyncio.wait_for` (which requires an awaitable), created `_call_orchestrator_async` that calls `_run_orchestrator_loop` directly — avoids nested `asyncio.run()` which raises `RuntimeError` in Python 3.12.
- **60-minute idempotency window:** Longer than red_team.py's 30-minute window to accommodate Sonnet's 120s timeout plus DB overhead. Prevents false skips if a previous run is still awaiting the orchestrator response.
- **Graceful signal degradation:** Each `_fetch_*_sync` call wrapped individually in try/except. If `eval_runs` table is empty or `verified_qa` hasn't been populated, the signals default to empty values and the orchestrator proceeds with what's available.

## Deviations from Plan

None — plan executed exactly as written. The `_call_orchestrator_async` shim was necessary to implement the plan's `asyncio.wait_for` requirement correctly (avoiding nested asyncio.run) and is consistent with Python 3.12 asyncio semantics. This is implementation correctness, not a deviation from spec.

## Issues Encountered

None — all acceptance criteria passed on first attempt.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `run_deployment_checklist` task is ready for Plan 08-04 (FastAPI routes) to dispatch via `run_deployment_checklist.apply_async(kwargs={"agent_id": str(agent_id)}, queue="runtime")`
- The task name `"app.worker.tasks.runtime.deployment.run_deployment_checklist"` must be added to `celery_app.py` include list before the worker can discover it (Plan 08-04 or a dedicated config task)
- No blockers

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced by this task. All threat mitigations from the plan's threat register are implemented:

| Threat ID | Status |
|-----------|--------|
| T-08-03-01 | Mitigated — only agent_id in task kwargs, conn_str fetched inside task body |
| T-08-03-02 | Mitigated — 60-minute idempotency window, control DB ORM check |
| T-08-03-03 | Mitigated — asyncio.wait_for(timeout=120.0) kills runaway Sonnet calls |
| T-08-03-04 | Mitigated — checklist_runs writes use get_sync_db() ORM only, never psycopg2 conn_str |

## Self-Check: PASSED

- `apps/api/app/worker/tasks/runtime/deployment.py` exists: FOUND
- Commit `f6fde48` exists: FOUND (HEAD on worktree-agent-aa0e6cacc3c37f312)
- `from app.worker.tasks.runtime.deployment import run_deployment_checklist` imports successfully: VERIFIED
- `acks_late=True` present: VERIFIED (count=2, decorator + docstring reference)
- `interval '60 minutes'` present: VERIFIED (count=1)
- `get_sync_db` present: VERIFIED (count=7, >= 3 required)
- `asyncio.wait_for` present: VERIFIED (count=5)
- No `conn_str` in `apply_async` kwargs: VERIFIED

---
*Phase: 08-pre-deployment-checklist*
*Completed: 2026-05-24*
