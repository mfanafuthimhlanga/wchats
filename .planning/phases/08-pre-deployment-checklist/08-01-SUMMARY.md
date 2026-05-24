---
phase: "08"
plan: "01"
subsystem: pre-deployment-checklist
tags: [migration, orm, config, test-stubs, alembic, sqlalchemy]
dependency_graph:
  requires:
    - "07-06 (Phase 7 complete — red team routes + demo operational)"
    - "alembic/versions/0010_agent_strategy_resynthesis_flag.py (down_revision chain)"
  provides:
    - "checklist_runs control DB table (migration 0011)"
    - "agents.is_deployed boolean column (migration 0011)"
    - "ChecklistRun ORM model (used by plans 08-02, 08-03, 08-04)"
    - "DEP_BLOCK_ON_HIGH_RED_TEAM Settings field (used by deployment_service)"
    - "4 xfail test stub files (de-xfailed in plan 08-06)"
  affects:
    - "apps/api/app/models/agent.py (is_deployed added)"
    - "apps/api/app/core/config.py (M8 config block added)"
tech_stack:
  added: []
  patterns:
    - "IF NOT EXISTS DDL guards on all CREATE TABLE and ALTER TABLE statements"
    - "SQLAlchemy ORM with JSONB dialect type for list/dict columns"
    - "xfail stub pattern with env-var bootstrap (strict=True, de-xfail in later plan)"
    - "skipif guard for integration E2E tests (DEP_E2E_ENABLED)"
key_files:
  created:
    - apps/api/alembic/versions/0011_checklist_runs_is_deployed.py
    - apps/api/app/models/checklist_run.py
    - apps/api/tests/unit/test_deployment_service.py
    - apps/api/tests/unit/test_deployment_task.py
    - apps/api/tests/unit/test_deployment_routes.py
    - apps/api/tests/integration/test_deployment_e2e.py
  modified:
    - apps/api/app/models/agent.py
    - apps/api/app/core/config.py
decisions:
  - "[08-01] Migration 0011 uses IF NOT EXISTS on all three DDL statements for safe re-run on pre-altered DBs (T-08-01-01)"
  - "[08-01] checklist_runs is in control DB (not tenant DB) — platform metadata, no PII, IDOR check gated in routes (Plan 08-04)"
  - "[08-01] DEP_BLOCK_ON_HIGH_RED_TEAM defaults True — safe production default; operators can set to False to degrade high findings to warnings"
metrics:
  duration: "~12 min"
  completed_date: "2026-05-24"
  tasks_completed: 2
  files_created: 6
  files_modified: 2
---

# Phase 8 Plan 01: Foundation — Migration, ORM, Config, Test Stubs Summary

**One-liner:** Alembic migration 0011 creates `checklist_runs` table and `agents.is_deployed` column; ChecklistRun ORM + DEP_BLOCK_ON_HIGH_RED_TEAM config field + 4 xfail test stubs establish the Phase 8 foundation for all downstream plans.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | Alembic migration 0011 + ChecklistRun ORM + Agent.is_deployed + DEP_BLOCK_ON_HIGH_RED_TEAM | 72a3cb6 | 4 files (2 created, 2 modified) |
| 2 | Wave 0 xfail test stub files (4 files) | 5b211b9 | 4 files created |

## Verification Results

All must-have verification checks pass:

1. `python -c "from app.models.checklist_run import ChecklistRun; print('OK')"` → OK
2. `python -c "from app.core.config import settings; print(settings.DEP_BLOCK_ON_HIGH_RED_TEAM)"` → True
3. `python -c "from app.models.agent import Agent; print(hasattr(Agent, 'is_deployed'))"` → True
4. `pytest tests/unit/test_deployment_service.py tests/unit/test_deployment_task.py tests/unit/test_deployment_routes.py -v` → 10 xfailed (exit 0)
5. `pytest tests/integration/test_deployment_e2e.py -v` → 1 skipped (exit 0, DEP_E2E_ENABLED unset)
6. `grep "revision.*=.*0011" apps/api/alembic/versions/0011_checklist_runs_is_deployed.py | wc -l` → 1

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

The 4 test files are intentionally stubbed with xfail markers. They are tracked for de-xfail in Plan 08-06 when `deployment_service.py`, `deployment.py` routes, and `run_deployment_checklist` task are implemented.

| File | Line | Stub Type | Resolved In |
|------|------|-----------|-------------|
| tests/unit/test_deployment_service.py | 42–79 | xfail (strict=True) | Plan 08-06 |
| tests/unit/test_deployment_task.py | 42–69 | xfail (strict=True) | Plan 08-06 |
| tests/unit/test_deployment_routes.py | 42–69 | xfail (strict=True) | Plan 08-06 |
| tests/integration/test_deployment_e2e.py | 21–22 | E2E stub (skipif guard) | Plan 08-07 |

## Threat Flags

No new threat surface introduced beyond what is specified in the plan's threat model (T-08-01-01 and T-08-01-02 both addressed: IF NOT EXISTS guards applied to all DDL, JSONB report column contains quality signals not PII).

## Self-Check: PASSED

- [x] `apps/api/alembic/versions/0011_checklist_runs_is_deployed.py` exists
- [x] `apps/api/app/models/checklist_run.py` exists, imports cleanly
- [x] `apps/api/app/models/agent.py` contains `is_deployed`
- [x] `apps/api/app/core/config.py` contains `DEP_BLOCK_ON_HIGH_RED_TEAM: bool = True`
- [x] 4 test stub files exist and collect without errors
- [x] Commit 72a3cb6 exists (Task 1)
- [x] Commit 5b211b9 exists (Task 2)
