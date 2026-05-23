---
phase: 07-red-team
plan: "01"
subsystem: database
tags: [alembic, migration, pydantic-settings, pytest, xfail, red-team]

# Dependency graph
requires:
  - phase: 06-eval-system
    provides: "tenant DB migration 0005, eval thresholds pattern in Settings, xfail stub pattern"
provides:
  - "Alembic migration 0006: status + deployment_blocked columns on red_team_runs"
  - "RED_TEAM_MAX_TURNS and RED_TEAM_ATTACK_SEQUENCES settings fields"
  - "2 xfail test stubs in test_red_team_service.py for Plan 07-05 to de-xfail"
affects: [07-02, 07-03, 07-04, 07-05, 07-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "IF NOT EXISTS guard on ALTER TABLE migrations (safe re-run on pre-altered tenants)"
    - "xfail strict=True stubs planted early — de-xfailed when service is implemented"
    - "env-var safety block with os.environ.setdefault before any app import in test files"

key-files:
  created:
    - apps/api/alembic_tenant/versions/0006_red_team_runs_status.py
    - apps/api/tests/unit/test_red_team_service.py
  modified:
    - apps/api/app/core/config.py

key-decisions:
  - "IF NOT EXISTS guards in upgrade() make migration idempotent on pre-altered tenant DBs"
  - "RED_TEAM_MAX_TURNS=5 and RED_TEAM_ATTACK_SEQUENCES=3 are plain int fields — no Field() wrapper needed"
  - "xfail strict=True so stubs convert to ERROR (not PASS) if accidentally un-stubbed before 07-05"

patterns-established:
  - "M7 settings block placed after M6 VERIFIED_QA_HIT_THRESHOLD — section comment as divider"

requirements-completed: ["RED-05", "RED-06"]

# Metrics
duration: 8min
completed: 2026-05-23
---

# Phase 7 Plan 01: Foundation — DB Migration + Settings + Test Stubs Summary

**Alembic migration 0006 adds status and deployment_blocked to red_team_runs; Settings gains RED_TEAM_MAX_TURNS=5 and RED_TEAM_ATTACK_SEQUENCES=3; two strict xfail stubs planted for Plan 07-05**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-05-23T19:09:00Z
- **Completed:** 2026-05-23T19:17:17Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Migration 0006 verified against acceptance criteria and committed (file pre-existed from prior session, already correct)
- RED_TEAM_MAX_TURNS and RED_TEAM_ATTACK_SEQUENCES added to Settings class after M6 threshold block
- test_red_team_service.py created with 2 strict xfail stubs; pytest exits 0 with 2 XFAIL results

## Task Commits

Each task was committed atomically:

1. **Task T01: Write tenant DB migration 0006_red_team_runs_status.py** - `bc4b75e` (feat)
2. **Task T02: Add RED_TEAM_* settings + xfail test stubs** - `31e610d` (feat)

## Files Created/Modified

- `apps/api/alembic_tenant/versions/0006_red_team_runs_status.py` - Tenant DB migration 0006: adds status TEXT (idempotency guard pattern) and deployment_blocked BOOLEAN (RED-06 gate) to red_team_runs
- `apps/api/app/core/config.py` - Added M7 settings block: RED_TEAM_MAX_TURNS=5, RED_TEAM_ATTACK_SEQUENCES=3
- `apps/api/tests/unit/test_red_team_service.py` - 2 strict xfail stubs for test_classify_severity_critical and test_prompt_injection_agent_finds_vulnerability

## Decisions Made

- Migration 0006 pre-existed from a prior session and already matched all acceptance criteria — no regeneration needed; verified and committed as-is
- IF NOT EXISTS guards on both ALTER TABLE statements make the migration safe to re-run on tenant DBs that may have been manually altered
- xfail strict=True ensures stubs fail loudly if de-xfailed prematurely (any unintentional pass becomes ERROR)

## Deviations from Plan

None — plan executed exactly as written. The pre-existing migration file matched the spec exactly.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Plan 07-02 (red_team_service.py scaffold + Celery task) can proceed immediately
- tenant DB migration 0006 provides the status and deployment_blocked columns required by run_red_team
- Settings fields RED_TEAM_MAX_TURNS and RED_TEAM_ATTACK_SEQUENCES available to all downstream plans
- test_red_team_service.py stubs will be de-xfailed in Plan 07-05

---
*Phase: 07-red-team*
*Completed: 2026-05-23*
