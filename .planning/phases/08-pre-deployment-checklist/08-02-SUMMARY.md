---
phase: 08-pre-deployment-checklist
plan: "02"
subsystem: api
tags: [claude-agent-sdk, deployment, pydantic, psycopg2, celery, sonnet]

# Dependency graph
requires:
  - phase: 07-red-team
    provides: red_team_service.py Agent SDK pattern + report_finding side-effect tool pattern
  - phase: 08-01
    provides: migration 0011 (checklist_runs table), DEP_BLOCK_ON_HIGH_RED_TEAM setting

provides:
  - deployment_service.py with all 8 public symbols
  - DeploymentWarning and DeploymentReport Pydantic models
  - run_orchestrator synchronous bridge (asyncio.run + asyncio.wait_for 120s)
  - _run_orchestrator_loop async ClaudeSDKClient loop (submit_report side-effect)
  - 4 synchronous psycopg2 signal collectors (_fetch_eval_summary_sync, _fetch_red_team_summary_sync, _fetch_verified_qa_stats_sync, _fetch_corpus_stats_sync)
  - _make_iframe_snippet iframe helper returning widget.veridian.app script tag
  - _TOOL_SUBMIT_REPORT tool schema with enum-validated fields
  - _DEPLOYMENT_SYSTEM_PROMPT with blocking/warning/ship conditions

affects:
  - 08-03 (Celery task imports run_orchestrator + 4 signal collectors)
  - 08-04 (FastAPI routes import _make_iframe_snippet, DeploymentReport)
  - 08-07 (E2E test exercises full orchestrator flow)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Signal-first orchestrator: collect psycopg2 signals synchronously, pass as JSON context to Agent SDK Sonnet"
    - "Side-effect tool capture: runner captures ToolUseBlock and returns immediately, no tool result sent back"
    - "CTL-08 compliance: conn_str parameter never passed to structlog calls; only agent_id logged"

key-files:
  created:
    - apps/api/app/services/deployment_service.py

key-decisions:
  - "red_team_runs.findings is JSONB list — severity counts derived by iterating findings list, not querying a separate findings table"
  - "verified_qa column names are faithfulness and relevance (not faithfulness_score/relevance_score) per migration 0005"
  - "asyncio.wait_for appears in module docstring and function docstring in addition to actual call — grep -c returns 3; plan said 1 but functional code has exactly 1 call"
  - "DEP-01 latency/cost signals deferred to M10 per 08-CONTEXT.md §Deferred Ideas — M8 reads only eval, red team, verified QA, corpus stats"

patterns-established:
  - "Signal collection functions use psycopg2.connect(conn_str, connect_timeout=10) with try/finally conn.close()"
  - "_fetch_verified_qa_stats_sync wraps query in inner try/except to handle missing table gracefully"
  - "run_orchestrator swallows all exceptions via log.warning — task marks run as failed, not worker crash"

requirements-completed:
  - DEP-01
  - DEP-02
  - DEP-03

# Metrics
duration: 5min
completed: 2026-05-24
---

# Phase 08 Plan 02: Deployment Service Summary

**Claude Agent SDK Sonnet orchestrator with 4 psycopg2 signal collectors, submit_report side-effect tool, and iframe snippet helper — all 8 symbols importable from deployment_service.py**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-05-24T12:08:52Z
- **Completed:** 2026-05-24T12:13:55Z
- **Tasks:** 2 (committed together as single file creation)
- **Files modified:** 1

## Accomplishments

- Created `deployment_service.py` (353 lines) with all required public symbols passing import check
- Orchestrator follows exact report_finding side-effect pattern from red_team_service.py — ToolUseBlock captured, no tool result sent back
- CTL-08 security: conn_str never appears in any structlog call; grep confirms zero violations
- Signal collectors handle edge cases: no eval runs, JSONB findings parsing, missing verified_qa table, empty corpus

## Task Commits

Each task was committed atomically:

1. **Task 1: Pydantic models + tool schema + system prompt + iframe helper** - `828c525` (feat)
2. **Task 2: Four signal collection functions + _run_orchestrator_loop + run_orchestrator bridge** - `828c525` (feat — same commit, single file)

**Plan metadata:** (committed with state updates below)

## Files Created/Modified

- `apps/api/app/services/deployment_service.py` — Complete deployment service: models, tool schema, system prompt, iframe helper, 4 signal collectors, async orchestrator loop, synchronous bridge

## Decisions Made

- **JSONB findings parsing in _fetch_red_team_summary_sync:** The plan's note about "red_team_findings table" was checked against actual schema. `red_team_runs.findings` is a JSONB column (list of finding dicts with a `severity` key). Severity counts are derived by iterating the findings list — no separate table exists. This is the correct behavior based on migration 0001_tenant_v1_schema.py.
- **verified_qa column names:** Plan says `faithfulness_score` and `relevance_score` but actual migration 0005 defines them as `faithfulness` and `relevance`. Used correct column names.
- **asyncio.wait_for in docstrings:** Module docstring and run_orchestrator docstring reference the pattern for documentation purposes. Plan verification check returns 3 (not 1) — not a bug; the functional code has exactly one call at line 347.

## Deviations from Plan

None - plan executed as written. One naming clarification applied:

**[Rule 1 - Bug] Used correct verified_qa column names**
- **Found during:** Task 2 (writing _fetch_verified_qa_stats_sync)
- **Issue:** Plan text says `faithfulness_score` and `relevance_score` but the actual tenant DB migration 0005 defines columns as `faithfulness` and `relevance` (without `_score` suffix)
- **Fix:** Used `faithfulness` and `relevance` in the SQL query — matching actual schema
- **Files modified:** apps/api/app/services/deployment_service.py
- **Verification:** Column names confirmed by reading `apps/api/alembic_tenant/versions/0005_verified_qa_eval_scenarios.py`
- **Committed in:** 828c525

---

**Total deviations:** 1 auto-fixed (Rule 1 - schema mismatch)
**Impact on plan:** Critical fix — wrong column names would cause SQL error at runtime.

## Issues Encountered

None.

## Self-Check

## Self-Check: PASSED

- `apps/api/app/services/deployment_service.py` — EXISTS (353 lines)
- Commit `828c525` — EXISTS
- All 8 symbols importable: CONFIRMED
- conn_str never logged: CONFIRMED (grep returns 0)
- asyncio.wait_for functional call: 1 (plus 2 in docstrings)
- widget.veridian.app in snippet: CONFIRMED

## Next Phase Readiness

- Plan 08-03 (Celery task) can import `run_orchestrator`, all 4 `_fetch_*_sync` functions
- Plan 08-04 (FastAPI routes) can import `_make_iframe_snippet`, `DeploymentReport`, `DeploymentWarning`
- No blockers

---
*Phase: 08-pre-deployment-checklist*
*Completed: 2026-05-24*
