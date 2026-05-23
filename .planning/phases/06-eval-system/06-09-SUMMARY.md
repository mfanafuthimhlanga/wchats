---
phase: "06"
plan: "06-09"
title: "Demo script + unit tests + guarded E2E test"
status: complete
completed_at: "2026-05-23"
commits:
  - 63ac00f  # feat(06-09): demo_m6.sh
  - 79eac65  # test(06-09): eval_service + scenario_service unit tests
  - fed1b1e  # test(06-09): guarded E2E test
---

# Plan 06-09 Summary — Demo Script + Tests

## What Was Built

### Task 1: scripts/demo_m6.sh

- Created `scripts/demo_m6.sh` following `demo_m5.sh` structure.
- Implements D-32 LOCKED: local processes only — no Docker, no docker-compose anywhere.
- Prerequisite checks: `redis-cli ping`, `curl -sf $BASE_URL/health`, psql check.
- Section 1: triggers `generate_eval_suite` via Celery `apply_async` (not beat timer).
- Section 2: triggers `run_eval_suite` via `apply_async`, captures task ID, polls for
  completion (up to 20x × 15s = 5 minutes polling loop).
- Section 3: shows `verified_qa` entries promoted (via psql if TENANT_DB_URL set, or
  via FastAPI eval-runs route fallback).
- Section 4: sends widget query via `POST /api/v1/agents/{id}/query`, parses
  `trace.cache_hit` to confirm verified_qa cache hit.
- Human checkpoint: dashboard URL + verification instructions.
- All curl commands target `localhost:8000` (local uvicorn).
- `bash -n scripts/demo_m6.sh` passes; zero `docker` references confirmed.

### Task 2: Unit tests for eval_service.py

Created `apps/api/tests/unit/test_eval_service.py` with 6 tests:

| Test | Purpose |
|------|---------|
| `test_run_ragas_eval_builds_dataset` | D-02: `reference` key in dataset; D-04: 4 metrics instantiated |
| `test_run_ragas_eval_empty_scenarios_returns_empty` | Early-exit path when no valid scenarios |
| `test_run_ragas_eval_uses_correct_import` | D-01: `from ragas.metrics.collections import` in source; D-02: `ground_truths` absent |
| `test_promote_to_verified_qa_inserts_on_threshold_pass` | D-21/D-22: INSERT when ≥ 0.90, `promoted_by='system'` |
| `test_promote_to_verified_qa_skips_below_threshold` | D-21: no INSERT when faithfulness < 0.90 |
| `test_promote_to_verified_qa_skips_below_relevancy_threshold` | D-21: no INSERT when relevancy < 0.90 |

Key fix: Ragas metric classes (Faithfulness, AnswerRelevancy, etc.) validate the LLM
type at `__init__` — mocked the metric class constructors themselves, not just InstructorLLM.

### Task 3: Unit tests for scenario_service.py

Created `apps/api/tests/unit/test_scenario_service.py` with 7 tests:

| Test | Purpose |
|------|---------|
| `test_generate_scenarios_from_chunks_calls_haiku` | D-12: forced `tool_choice`, D-13: `source='generated'` |
| `test_generate_scenarios_includes_retrieved_contexts` | chunks included in `retrieved_contexts` |
| `test_generate_scenarios_raises_on_no_tool_block` | ValueError when no tool_use block |
| `test_generate_scenarios_raises_on_wrong_tool_name` | ValueError when tool name mismatch |
| `test_store_scenarios_idempotent` | `ON CONFLICT DO NOTHING` in INSERT SQL |
| `test_store_scenarios_returns_zero_on_empty_list` | empty list short-circuit |
| `test_store_scenarios_assigns_uuid_when_id_missing` | UUID auto-assignment |

Note: `test_neon_branch.py` already had 15 tests from plan 06-04 covering all specified
scenarios. No new tests needed — no duplication added.

### Task 4: Guarded E2E test

Created `apps/api/tests/integration/test_eval_e2e.py` with `EVAL_E2E_ENABLED=1` guard
(same pattern as `VALIDATION_E2E_ENABLED` in M5):

| Test | Purpose |
|------|---------|
| `test_run_eval_for_agent_full_sequence` | Full cycle: running → eval → write → promote → complete; promoted_count=1 |
| `test_run_eval_for_agent_no_promotion_below_threshold` | D-21: no INSERT for low scores |
| `test_eval_e2e_guard_is_active` | Sanity: runs only when guard set |

Without `EVAL_E2E_ENABLED=1`: 3 tests skip — safe for CI.

## Test Results

```
pytest tests/unit/test_eval_service.py tests/unit/test_scenario_service.py tests/unit/test_neon_branch.py -v
→ 28 passed in 47.30s

pytest tests/integration/test_eval_e2e.py -v  (without EVAL_E2E_ENABLED)
→ 3 skipped in 3.29s
```

## Acceptance Criteria

- [x] `scripts/demo_m6.sh` created — executable, `set -euo pipefail`, no Docker
- [x] `redis-cli ping` check in script
- [x] Polling loop for eval task completion
- [x] `verified_qa` entries shown after eval completes
- [x] Widget query + `cache_hit` trace check
- [x] Human checkpoint for dashboard verification
- [x] `apps/api/tests/unit/test_eval_service.py` — 6 tests pass
- [x] `apps/api/tests/unit/test_scenario_service.py` — 7 tests pass
- [x] `apps/api/tests/unit/test_neon_branch.py` — 15 tests pass (pre-existing 06-04)
- [x] Guarded E2E test created (`EVAL_E2E_ENABLED=1` guard)
- [x] All 28 unit tests pass: `pytest tests/unit/test_eval_service.py tests/unit/test_scenario_service.py tests/unit/test_neon_branch.py`
- [x] Each task committed individually (3 atomic commits)

## Phase 6 Complete

This is the final plan (Wave 5) of Phase 6. All 9/9 plans complete.

Plans completed:
- 06-01: Foundation (migration 0005, eval thresholds, Celery beat)
- 06-02: eval_service.py (Ragas 0.4.x harness) + neon.py branch methods
- 06-03: scenario_service.py (generator + miner)
- 06-04: run_eval_suite Celery task + beat dispatcher
- 06-05: verified_qa promotion + retrieval_service verified_qa_lookup
- 06-06: FastAPI eval routes (GET eval-runs, GET results, POST trigger)
- 06-07: Next.js eval dashboard (Pass Rates + Scenarios tabs)
- 06-08: Unit + integration tests (eval routes, run_eval_suite, retrieval cache)
- 06-09: Demo script + unit tests + guarded E2E test (this plan)
