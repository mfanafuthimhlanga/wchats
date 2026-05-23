---
phase: "07"
plan: "05"
subsystem: "red-team-tests"
tags: ["testing", "unit-tests", "red-team", "RED-04", "xfail-removal"]
dependency_graph:
  requires: ["07-02", "07-03", "07-04"]
  provides: ["unit-test-coverage-red-team"]
  affects: ["test suite", "CI"]
tech_stack:
  added: []
  patterns: ["unittest.mock.patch at module boundary", "contextmanager mock for get_sync_db", "psycopg2 cursor mock with side_effect chain"]
key_files:
  created:
    - apps/api/tests/unit/test_red_team_task.py
  modified:
    - apps/api/tests/unit/test_red_team_service.py
    - apps/api/tests/unit/test_sanitize.py
decisions:
  - "asyncio.run patched at app.services.red_team_service.asyncio.run — intercepts both asyncio.run and asyncio.wait_for in one shot"
  - "psycopg2.connect uses side_effect list for 3 sequential connections in run_red_team happy path"
  - "get_sync_db mocked as @contextmanager returning mock_db — matches actual @contextmanager usage in tasks"
  - "RedTeamFinding used directly (not MagicMock) in test_run_red_team_complete to exercise model_dump() in UPDATE step"
metrics:
  duration: "~10 min"
  completed: "2026-05-23T19:52:04Z"
  tasks_completed: 2
  files_modified: 3
---

# Phase 7 Plan 5: Tests — Unit Tests + RED-04 Corpus Injection Canary Summary

De-xfailed the two stubs from Plan 07-01 and completed the full test suite for Phase 7: 9 service unit tests, 3 Celery task unit tests, and 1 RED-04 corpus injection canary added to test_sanitize.py — all 18 tests PASSED with no xfail.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 07-05-T01 | test_red_team_service.py — de-xfail stubs + full service unit tests | 0acb6c3 | apps/api/tests/unit/test_red_team_service.py |
| 07-05-T02 | test_red_team_task.py + test_sanitize.py RED-04 canary | 0651543 | apps/api/tests/unit/test_red_team_task.py (new), apps/api/tests/unit/test_sanitize.py |

## Test Coverage Summary

### test_red_team_service.py (9 tests)

| Class | Test | Result |
|-------|------|--------|
| TestClassifySeverity | test_classify_severity_critical | PASSED |
| TestClassifySeverity | test_classify_severity_low | PASSED |
| TestClassifySeverity | test_classify_severity_raises_on_no_tool_use | PASSED |
| TestPromptInjectionAgent | test_prompt_injection_agent_finds_vulnerability | PASSED |
| TestPromptInjectionAgent | test_prompt_injection_agent_returns_empty_on_exception | PASSED |
| TestDataLeakageAgent | test_data_leakage_agent_resists | PASSED |
| TestHallucinationAgent | test_hallucination_agent_detects_false_confidence | PASSED |
| TestRedTeamResult | test_red_team_result_deployment_blocked_on_critical | PASSED |
| TestRedTeamResult | test_red_team_result_not_blocked_on_high | PASSED |

### test_red_team_task.py (3 tests)

| Class | Test | Result |
|-------|------|--------|
| TestRunRedTeamIdempotentSkip | test_run_red_team_idempotent_skip | PASSED |
| TestRunRedTeamBeatDispatches | test_run_red_team_beat_dispatches | PASSED |
| TestRunRedTeamComplete | test_run_red_team_complete | PASSED |

### test_sanitize.py (6 tests — 5 pre-existing + 1 new canary)

| Test | Result |
|------|--------|
| test_strips_system_prefix | PASSED |
| test_strips_inst_tags | PASSED |
| test_strips_html_comments | PASSED |
| test_strips_ignore_previous_case_insensitive | PASSED |
| test_returns_stripped_string | PASSED |
| test_corpus_injection_canary (NEW — RED-04) | PASSED |

**Total: 18 tests PASSED, 0 failed, 0 xfail, 0 error**

## Deviations from Plan

None — plan executed exactly as written.

The mock strategy described in the plan (patch `asyncio.run`, patch `psycopg2.connect`, patch `get_sync_db`, call tasks via `.run()`) worked as specified. No modifications to service or task code were needed.

## Known Stubs

None — all tests exercise real code paths via mocks. No hardcoded empty values in test assertions.

## Self-Check: PASSED

- [x] apps/api/tests/unit/test_red_team_service.py exists with 9 tests, no xfail decorators
- [x] apps/api/tests/unit/test_red_team_task.py exists with 3 tests
- [x] apps/api/tests/unit/test_sanitize.py contains test_corpus_injection_canary
- [x] Commit 0acb6c3 exists (test_red_team_service.py rewrite)
- [x] Commit 0651543 exists (test_red_team_task.py + canary)
- [x] All 18 tests PASSED in final run: `python -m pytest tests/unit/test_red_team_service.py tests/unit/test_red_team_task.py tests/unit/test_sanitize.py -v`
