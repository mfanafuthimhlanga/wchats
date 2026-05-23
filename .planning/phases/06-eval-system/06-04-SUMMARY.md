---
phase: 06-eval-system
plan: 06-04
subsystem: infra
tags: [neon, branching, eval, requests, mocking]

# Dependency graph
requires:
  - phase: 06-01
    provides: Settings additions (EVAL_FAITHFULNESS_THRESHOLD, EVAL_RELEVANCY_THRESHOLD, VERIFIED_QA_HIT_THRESHOLD) and neon.py base patterns

provides:
  - create_branch(project_id, branch_name) -> tuple[str, str] in neon_service.py
  - delete_branch(project_id, branch_id) -> None in neon_service.py
  - 15 unit tests (mocked HTTP) for both methods in tests/unit/test_neon_branch.py

affects:
  - 06-06 (run_eval_suite Celery task — calls create_branch/delete_branch in try/finally)
  - 06-09 (demo script — indirectly uses eval isolation via neon branches)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - requests.post/get/delete with _neon_headers() and NeonHTTPError(r.status_code, r.text[:200]) pattern
    - conn_str returned as local variable only — never logged, never stored (D-18, T-03-02)
    - Branch endpoint created with {"type": "read_write"} to ensure queryable compute
    - connection_uri fetched with pooled=false (direct URI for psycopg2)

key-files:
  created:
    - apps/api/tests/unit/test_neon_branch.py
  modified:
    - apps/api/app/services/neon.py

key-decisions:
  - "D-17 (LOCKED): create_branch returns tuple[str, str] = (branch_id, conn_str); delete_branch returns None"
  - "D-18 (LOCKED): conn_str passed as local variable only — never logged, never persisted"
  - "T-03-02: log.debug calls for branch_created and branch_deleted include project_id and branch_id only"
  - "endpoints=[{type: read_write}] is required for the branch to have a queryable compute endpoint"
  - "pooled=false for connection_uri to get direct psycopg2 URI (consistent with apply_migrations pattern)"

patterns-established:
  - "Neon branch lifecycle: create_branch → eval run → delete_branch in finally block (D-10)"
  - "All Neon HTTP errors use NeonHTTPError(status_code, text[:200]) — truncated error body"
  - "New neon methods follow the exact requests + _neon_headers() + NeonHTTPError pattern as create_neon_project"

requirements-completed:
  - EVL-05

# Metrics
duration: 20min
completed: 2026-05-23
---

# Plan 06-04: neon_service.py — create_branch and delete_branch

**Neon branch-per-eval-run isolation implemented: create_branch returns (branch_id, conn_str) tuple via direct Neon REST calls; delete_branch deletes by branch ID; 15 mocked unit tests enforce API contract and T-03-02 no-credential-logging**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-05-23
- **Completed:** 2026-05-23
- **Tasks:** 2
- **Files modified:** 2 (neon.py modified, test_neon_branch.py created)

## Accomplishments
- Added `create_branch(project_id, branch_name) -> tuple[str, str]` to neon_service.py — POSTs branch with read_write endpoint, GETs direct connection URI, returns (branch_id, conn_str)
- Added `delete_branch(project_id, branch_id) -> None` — DELETEs branch by ID, raises NeonHTTPError on failure (suitable for finally block)
- 15 unit tests covering happy paths, error paths, URL/body/param correctness, error truncation, no-conn_str-in-logs (T-03-02), timeout values, and return types

## Task Commits

1. **Task 1: create_branch and delete_branch methods** - `fc59539` (feat)
2. **Task 2: unit tests for both methods** - `da44ac3` (test)

## Files Created/Modified
- `apps/api/app/services/neon.py` — Added create_branch() and delete_branch() after wait_for_neon_ready()
- `apps/api/tests/unit/test_neon_branch.py` — 15 unit tests with mocked HTTP (no Docker, no real Neon calls)

## Decisions Made
- Followed D-17 exactly: `create_branch` returns `tuple[str, str]` = `(branch_id, conn_str)` — not just conn_str
- `endpoints=[{"type": "read_write"}]` included in POST body — required for compute endpoint to exist on the branch
- `pooled=false` for connection_uri GET — matches apply_migrations pattern for direct psycopg2 connection
- `delete_branch` raises NeonHTTPError on failure (does not swallow) — caller in finally block must catch and log

## Deviations from Plan

None — plan executed exactly as written. Both functions match the create_neon_project() pattern precisely (requests library, _neon_headers(), NeonHTTPError with r.text[:200] truncation, structlog log.debug).

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required for this plan.

## Next Phase Readiness

- `create_branch` and `delete_branch` are ready for use in `run_eval_suite` Celery task (06-06)
- Branch name convention `eval-{run_id}` documented in create_branch docstring
- The `delete_branch` in a `finally` block pattern (D-10) is documented and tested

---
*Phase: 06-eval-system*
*Completed: 2026-05-23*
