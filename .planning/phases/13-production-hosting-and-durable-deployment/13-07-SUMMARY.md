---
phase: 13-production-hosting-and-durable-deployment
plan: "07"
subsystem: runtime-worker
tags: [contextvar, concurrency, celery, fargate, prod-14, prod-15, prod-06]
dependency_graph:
  requires: ["13-01", "13-03", "13-04"]
  provides: [contextvar-isolation, prefork-concurrency-2, lifted-retrieve-throttle]
  affects: [agent_tools, celery_app, fargate_runtime_worker, agent_task]
tech_stack:
  added: [contextvars.ContextVar]
  patterns: [contextvar-per-task-isolation, read-into-local-before-executor, environment-conditional-pool]
key_files:
  created:
    - apps/api/tests/unit/test_agent_tools_contextvar.py
  modified:
    - apps/api/app/services/agent_tools.py
    - apps/api/app/worker/celery_app.py
    - apps/api/app/worker/tasks/runtime/agent.py
    - deploy/terraform/fargate.tf
    - apps/api/tests/unit/test_agent_tools.py
decisions:
  - ContextVar per-task isolation replaces module-level globals in agent_tools.py
  - ContextVars read into locals before run_in_executor — executor threads do not inherit asyncio context
  - worker_pool solo only for development/test ENVIRONMENT; prefork on production
  - _RETRIEVE_CALLS_PER_TURN_MAX raised from 2 (Voyage RPM) to 8 (DoS guard)
  - Voyage-era AT-MOST-ONCE prompt instruction removed from agent.py
metrics:
  duration: "~25 minutes"
  completed: "2026-06-29"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 6
status: complete
---

# Phase 13 Plan 07: Runtime Worker Concurrency Safety Summary

ContextVar-based per-task isolation with asyncio.run propagation proven by unit tests, ENVIRONMENT-conditional worker_pool (solo on Windows dev / prefork on Fargate), runtime worker bumped to concurrency=2, and Voyage-era single-retrieve throttle lifted to a DoS guard ceiling of 8.

## What Was Built

### Task 1: ContextVar refactor of agent_tools.py (TDD)

**RED phase** — wrote `test_agent_tools_contextvar.py` (3 tests) that referenced `_conn_str_var`, `_agent_id_var`, `_retrieve_call_count_var` on the `agent_tools` module. Tests failed with `AttributeError` as expected.

**GREEN phase** — refactored `apps/api/app/services/agent_tools.py`:

Seven module-level globals replaced with `ContextVar` declarations:
```python
_conn_str_var: ContextVar[str] = ContextVar("conn_str", default="")
_agent_id_var: ContextVar[str] = ContextVar("agent_id", default="")
_agent_name_var: ContextVar[str] = ContextVar("agent_name", default="")
_strategy_var: ContextVar[RetrievalStrategy | None] = ContextVar("strategy", default=None)
_conversation_id_var: ContextVar[str] = ContextVar("conversation_id", default="")
_notify_fn_var: ContextVar = ContextVar("notify_fn", default=None)
_retrieve_call_count_var: ContextVar[int] = ContextVar("retrieve_call_count", default=0)
```

`build_tool_server` updated to call `.set()` on each ContextVar (plus `_retrieve_call_count_var.set(0)` for per-turn reset).

**Executor-thread caveat handled:** `retrieve_tool`, `lookup_structured_tool`, and `escalate_to_human_tool` each read all ContextVars into local variables at the top of the async body *before* any `run_in_executor` call. Executor lambdas capture those locals — not `.get()` calls inside the lambda that would run in the thread's context-free environment.

`test_agent_tools.py` updated to use `_*_var.set()` / `_*_var.get()` instead of direct global attribute access. All 16 existing tests still pass.

### Task 2: ENVIRONMENT-conditional pool, concurrency=2, throttle lift

**`celery_app.py`** — `worker_pool` is now:
```python
worker_pool="solo" if settings.ENVIRONMENT in ("development", "test") else "prefork"
```
Solo pool on Windows/dev (billiard fix preserved); prefork on Linux Fargate. The Fargate CMD `--pool=prefork` is the authoritative override; this default makes the config self-consistent.

**`deploy/terraform/fargate.tf`** — runtime worker CMD changed from `--concurrency=1` to `--concurrency=2`. This delivers PROD-15 (concurrency > 1) now that the ContextVar refactor makes it safe.

**`agent.py`** — removed the Voyage-era prompt-level "retrieve at most once per response" instruction (lines 541-545 in the old file). The DoS guard in `retrieve_tool` supersedes the prompt-level cap.

**`agent_tools.py`** — `_RETRIEVE_CALLS_PER_TURN_MAX` raised from `2` (Voyage 3 RPM free-tier limit) to `8` (high DoS ceiling). Counter retained and ContextVar-backed; per-turn reset via `build_tool_server` preserved.

## Tests Proved

| Test | Assertion | Result |
|------|-----------|--------|
| `test_asyncio_run_propagation` | ContextVar set before `asyncio.run()` is visible inside the coroutine | PASS |
| `test_two_context_no_bleed` | Two `copy_context()` runs see only their own `conn_str` / `agent_id` / counter | PASS |
| `test_retrieve_counter_isolated` | Per-turn counter increments isolated between contexts (3 vs 7) | PASS |
| 16 existing `test_agent_tools` tests | No behavioral regression after ContextVar refactor | PASS |
| 12 existing `test_agent_task` tests | agent.py / asyncio.run boundary unchanged | PASS |

Total: **31/31 tests pass**.

## Deviations from Plan

### Auto-included (no approval needed)

**1. [Rule 2 — Missing critical feature] `_RETRIEVE_CALLS_PER_TURN_MAX` raised in Task 1**
- Found during: Task 1 agent_tools.py refactor
- Issue: The plan's Task 2 action describes raising the ceiling, but the constant lives in agent_tools.py and was being refactored in Task 1. Raising it in the same file edit was more coherent than leaving it at 2 in Task 1 and patching it in Task 2.
- Fix: Set `_RETRIEVE_CALLS_PER_TURN_MAX = 8` during the Task 1 agent_tools.py rewrite; Task 2 committed the celery_app.py / fargate.tf / agent.py changes as planned.
- No behavioral regression — the value was never read between Task 1 and Task 2 commits.

## Known Stubs

None. This plan modifies concurrency infrastructure, not data-rendering paths.

## Threat Surface Scan

No new network endpoints, auth paths, or file access patterns introduced. Changes are internal to worker process state management and Fargate task configuration. The T-13-07-01 through T-13-07-04 mitigations in the plan's threat register are fully implemented:

| Threat ID | Status |
|-----------|--------|
| T-13-07-01 Cross-tenant state bleed | Mitigated — ContextVar per-task isolation + no-bleed test |
| T-13-07-02 Stale retrieve counter across turns | Mitigated — `.set(0)` reset per `build_tool_server` call; verified by isolation test |
| T-13-07-03 Unbounded retrieve calls after throttle lift | Mitigated — DoS guard ceiling 8 retained; ContextVar-backed per-turn counter |
| T-13-07-04 Concurrency silently capped at 1 | Mitigated — ENVIRONMENT-conditional `worker_pool` + `--concurrency=2` Fargate CMD |

## Self-Check: PASSED

All modified files confirmed present on disk:
- FOUND: apps/api/app/services/agent_tools.py
- FOUND: apps/api/app/worker/celery_app.py
- FOUND: apps/api/app/worker/tasks/runtime/agent.py
- FOUND: deploy/terraform/fargate.tf
- FOUND: apps/api/tests/unit/test_agent_tools_contextvar.py
- FOUND: apps/api/tests/unit/test_agent_tools.py

All commits confirmed in git log:
- e2203a1: test(13-07): add failing tests for ContextVar propagation and isolation
- bc655c3: feat(13-07): refactor agent_tools globals to ContextVar with executor-thread safety
- 9cebf1b: feat(13-07): ENVIRONMENT-conditional worker pool, concurrency=2 Fargate CMD, lifted retrieve throttle
