---
phase: 08-pre-deployment-checklist
plan: "06"
subsystem: testing
tags: [pytest, unit-tests, deployment, fastapi, celery, psycopg2, mock, asyncio]

requires:
  - phase: 08-03
    provides: run_deployment_checklist Celery task with idempotency + asyncio bridge
  - phase: 08-04
    provides: FastAPI deployment routes (GET detail, POST acknowledge, POST approve)
  - phase: 08-02
    provides: deployment_service.py (DeploymentReport, DeploymentWarning, signal collectors, run_orchestrator)
  - phase: 08-01
    provides: xfail stubs in test_deployment_service.py, test_deployment_task.py, test_deployment_routes.py

provides:
  - 15 passing unit tests replacing 10 xfail stubs across 3 files
  - DEP-01 coverage: _fetch_eval_summary_sync shape and no-runs branch verified
  - DEP-02 coverage: DeploymentReport Pydantic construction and Literal rejection
  - DEP-03 coverage: _DEPLOYMENT_SYSTEM_PROMPT content assertions (deployment_blocked, high_count, 0.70)
  - DEP-04 coverage: GET /checklist-runs/{run_id} returns 200 with report dict
  - DEP-05 coverage: POST /acknowledge updates warning_acknowledgments and all_warnings_acknowledged
  - DEP-06 coverage: POST /approve-deployment sets is_deployed=True and returns iframe_snippet; blocked rejected 422

affects:
  - 08-07-PLAN.md (E2E demo plan assumes unit gate is green)

tech-stack:
  added: []
  patterns:
    - psycopg2.connect mocked with cursor context manager for sync signal collector tests
    - _call_orchestrator_async patched as async coroutine side_effect to inject result_container["report"]
    - get_sync_db mocked as contextmanager yielding shared mock_db object for all with-block invocations
    - db.get.side_effect dispatches by model.__name__ to return correct Agent vs ChecklistRun mock
    - Celery Retry exception caught in failure-path test (CELERY_TASK_ALWAYS_EAGER=True behavior)
    - ASGITransport + dependency_overrides pattern for deployment route tests
    - app.include_router(deployment.router, prefix="/api/v1") added to main.py (missing from Plan 08-04)

key-files:
  created: []
  modified:
    - apps/api/tests/unit/test_deployment_service.py
    - apps/api/tests/unit/test_deployment_task.py
    - apps/api/tests/unit/test_deployment_routes.py
    - apps/api/app/main.py

key-decisions:
  - "[08-06] _call_orchestrator_async patched (not run_orchestrator) — task calls asyncio.run(_call_orchestrator_async(...)) so patch the coroutine, not the sync bridge"
  - "[08-06] Celery Retry exception caught with broad except in failure-path test — CELERY_TASK_ALWAYS_EAGER=True causes self.retry() to raise Retry; important assertion is db.commit() was called before retry"
  - "[08-06] deployment router was missing from main.py — Rule 1 auto-fix applied; all route tests returned 404 until fix"
  - "[08-06] Pre-existing unit test failures logged to .planning/deferred-items.md — test_agent_chat_routes, test_agents_patch, test_chunking_service and others fail before and after our changes; not introduced by 08-06"

patterns-established:
  - "Async coroutine mock: patch coroutine at module boundary with async def side_effect that mutates result_container"
  - "Multi-model db.get dispatch: db.get.side_effect = lambda model, pk: agent if model.__name__ == 'Agent' else run"

requirements-completed:
  - DEP-01
  - DEP-02
  - DEP-03
  - DEP-04
  - DEP-05
  - DEP-06

duration: ~40min
completed: 2026-05-24
---

# Phase 8 Plan 06: De-xfail Deployment Unit Tests Summary

**15 unit tests replacing 10 xfail stubs: psycopg2/asyncio/ORM mocked at module boundaries, Celery task idempotency and failure paths verified, FastAPI routes tested via ASGITransport with deployment router added to main.py**

## Performance

- **Duration:** ~40 min
- **Started:** 2026-05-24T15:00:00Z
- **Completed:** 2026-05-24T15:40:00Z
- **Tasks:** 2
- **Files modified:** 4 (3 test files + main.py)

## Accomplishments

- De-xfailed all 10 stubs across 3 test files — 15 real assertions now covering DEP-01 through DEP-06
- Verified full signal collection path: `_fetch_eval_summary_sync` mocked with psycopg2 cursor producing `Decimal` metric rows
- Verified orchestrator integration: `_call_orchestrator_async` coroutine side_effect injects `result_container["report"]`; task returns `{"status": "complete", "run_id": ..., "recommendation": "ship"}`
- Verified idempotency guard: `get_sync_db` context manager mocked; `db.execute().scalar_one_or_none()` returning mock run triggers early return `{"status": "already_running"}`
- Verified approval gate: `recommendation="block"` rejected 422 with "blocked" in detail; `ship_with_warnings` with unacknowledged warnings rejected 422; clean `ship` path returns `deployed=True` + iframe_snippet

## Task Commits

1. **Task 1: De-xfail test_deployment_service.py** - `86f3f2e` (test)
2. **Task 2: De-xfail test_deployment_task.py + test_deployment_routes.py + main.py fix** - `ccb0220` (test)

## Files Created/Modified

- `apps/api/tests/unit/test_deployment_service.py` - 7 tests: TestRunOrchestrator, TestDeploymentReport (2), TestBlockingConditions (2), TestSignalCollectionFunctions (2)
- `apps/api/tests/unit/test_deployment_task.py` - 3 tests: idempotency skip, happy path complete, failure path sets failed + commits
- `apps/api/tests/unit/test_deployment_routes.py` - 5 tests: GET detail, acknowledge all-acked, approve-blocked-when-unacked, approve-sets-deployed-true, approve-rejects-blocked
- `apps/api/app/main.py` - Added `deployment` to router imports and `app.include_router(deployment.router, prefix="/api/v1")`

## Decisions Made

- `_call_orchestrator_async` is the correct patch target (not `run_orchestrator`) because the Celery task calls `asyncio.run(asyncio.wait_for(_call_orchestrator_async(...), timeout=120.0))` directly — patching the coroutine function lets the async side_effect populate `result_container["report"]` before `asyncio.run` returns
- Celery `Retry` exception in failure-path test is expected behavior under `CELERY_TASK_ALWAYS_EAGER=True`; test catches it with broad `except` and asserts `db.commit.called` to verify `status='failed'` was persisted
- `db.get.side_effect` dispatches by `model.__name__` (string) rather than `model is Agent` identity check because SQLAlchemy model identity can differ across test isolation boundaries

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Deployment router missing from app/main.py**
- **Found during:** Task 2 (test_deployment_routes.py — all tests returned 404)
- **Issue:** `app.api.v1.deployment` router was implemented in Plan 08-04 but never registered in main.py. All 5 route tests returned 404 because the paths `/api/v1/agents/{id}/checklist-runs/*` and `/api/v1/agents/{id}/approve-deployment` did not exist in the FastAPI app.
- **Fix:** Added `deployment` to the router import line and `app.include_router(deployment.router, prefix="/api/v1")` at the end of main.py
- **Files modified:** `apps/api/app/main.py`
- **Verification:** All 5 route tests PASSED after fix; pre-existing eval route tests still pass (no regression)
- **Committed in:** `ccb0220` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Essential for route tests to resolve any path. No scope creep — the fix was a missing router registration, not a new feature.

## Issues Encountered

- Pre-existing unit test failures discovered in unrelated test files (`test_agent_chat_routes.py`, `test_agents_patch.py`, `test_chunking_service.py`, `test_docling_service.py`, `test_eval_routes.py`, `test_jobs_routes.py`, `test_jwt.py`, `test_parse_task.py`, `test_services.py`, `test_tenants_route.py`). Verified pre-existing via `git stash` before changes. Logged in `.planning/deferred-items.md`. Not caused by Plan 08-06 changes.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All 15 deployment unit tests green — DEP-01 through DEP-06 have automated coverage
- Plan 08-07 (E2E demo + integration) can proceed with unit gate verified
- Deployment router is now registered in main.py — live server will serve all deployment routes

## Self-Check: PASSED

- FOUND: apps/api/tests/unit/test_deployment_service.py
- FOUND: apps/api/tests/unit/test_deployment_task.py
- FOUND: apps/api/tests/unit/test_deployment_routes.py
- FOUND: .planning/phases/08-pre-deployment-checklist/08-06-SUMMARY.md
- FOUND: commit 86f3f2e (test_deployment_service.py — 7 tests)
- FOUND: commit ccb0220 (test_deployment_task.py + test_deployment_routes.py + main.py — 8 tests)
- VERIFIED: 15/15 tests PASSED in target files
- VERIFIED: No xfail decorators remaining in any of the 3 files

---
*Phase: 08-pre-deployment-checklist*
*Completed: 2026-05-24*
