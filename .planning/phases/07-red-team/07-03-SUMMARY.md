---
phase: 07-red-team
plan: "03"
subsystem: celery-tasks
tags: [celery, red-team, psycopg2, idempotency, beat-schedule, asyncio]

# Dependency graph
requires:
  - phase: 07-red-team
    plan: "02"
    provides: "red_team_service.py runner functions, RedTeamFinding model, migration 0006, settings"
provides:
  - "apps/api/app/worker/tasks/runtime/red_team.py: run_red_team_beat + run_red_team Celery tasks"
  - "celery_app.py: M7 include entry + red-team-weekly beat schedule (crontab Monday 03:00 UTC)"
affects: [07-04, 07-05, 07-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "run_red_team_beat: beat dispatcher that fans out per-agent run_red_team tasks (mirrors eval beat pattern)"
    - "run_red_team: acks_late + idempotency guard on red_team_runs (kind='m7:{agent_id}', 30-min window)"
    - "probe_fn closure: direct Anthropic API call (not _run_sdk_turn — SSE infrastructure not applicable)"
    - "asyncio.run(asyncio.wait_for(..., timeout=60.0)) bridge in probe_fn — Python 3.12 safe"
    - "Sequential agent execution: PromptInjection -> DataLeakage -> Hallucination (no Celery chord)"
    - "deployment_blocked = (max_severity == 'critical') — RED-06 gate condition"
    - "status='failed' UPDATE before retry on exception — per run_eval_suite pattern"

key-files:
  created:
    - apps/api/app/worker/tasks/runtime/red_team.py
  modified:
    - apps/api/app/worker/celery_app.py

key-decisions:
  - "probe_fn uses direct Anthropic API call instead of _run_sdk_turn from agent.py — _run_sdk_turn is tightly coupled to SSE infrastructure (job_id, db, redis, emit) not available in red-team context"
  - "asyncio.get_event_loop().run_in_executor wraps the synchronous Anthropic client inside the async probe — avoids blocking the event loop"
  - "Idempotency guard window is 30 minutes (vs 10 min for eval) — red team runs are longer"
  - "Separate psycopg2 connection for Step 3 (INSERT) and Step 5 (agent execution) — each closed in finally"
  - "update_complete inside the agents try block (not a separate try) — partial failure on UPDATE is logged as warning, not fatal"

patterns-established:
  - "run_red_team_beat + run_red_team pair exactly mirrors run_eval_suite_beat + run_eval_suite from Phase 6"
  - "red-team-weekly: day_of_week=1 (Monday 03:00 UTC) — fires one hour after eval-nightly to avoid Redis contention"

requirements-completed: ["RED-06", "RED-07"]

# Metrics
duration: 18min
completed: 2026-05-23
---

# Phase 7 Plan 03: Celery Tasks — run_red_team + run_red_team_beat + Beat Schedule Summary

**run_red_team_beat (weekly beat dispatcher) and run_red_team (per-agent execution task) wired into celery_app with red-team-weekly Monday 03:00 UTC beat entry; idempotency guard uses 30-min window on red_team_runs table**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-05-23T19:21:00Z
- **Completed:** 2026-05-23T19:39:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- `apps/api/app/worker/tasks/runtime/red_team.py` created with full M7 Celery task layer
- `run_red_team_beat`: acks_late=True, runtime queue, fans out run_red_team per status='ready' agent
- `run_red_team`: acks_late=True, max_retries=2, runtime queue, agent_id only (no conn_str in args — CTL-08)
- Idempotency guard checks `red_team_runs WHERE kind='m7:{agent_id}' AND status='running' AND started_at > NOW() - INTERVAL '30 minutes'`
- probe_fn closure implemented as direct Anthropic API call (asyncio.run(asyncio.wait_for(..., timeout=60.0)))
- All three agent runners called sequentially: PromptInjection → DataLeakage → Hallucination
- deployment_blocked = (max_severity == "critical") — exact RED-06 gate condition
- status='failed' UPDATE executed before retry on exception
- `celery_app.py` updated: M7 include entry + `red-team-weekly` beat schedule
- Import verification: `python -c "from app.worker.tasks.runtime.red_team import run_red_team, run_red_team_beat"` passes
- All beat_schedule acceptance criteria verified via Python assertions

## Task Commits

Each task was committed atomically:

1. **Task T01: run_red_team_beat + run_red_team Celery tasks** - `a23a26f` (feat)
2. **Task T02: celery_app.py — include + beat_schedule additions** - `354816a` (feat)

## Files Created/Modified

- `apps/api/app/worker/tasks/runtime/red_team.py` — M7 red team Celery tasks: run_red_team_beat (beat dispatcher) and run_red_team (per-agent 10-step execution flow with idempotency guard, probe_fn closure, sequential agent execution, severity aggregation, deployment gate)
- `apps/api/app/worker/celery_app.py` — Added M7 include entry + red-team-weekly beat schedule (crontab Monday 03:00 UTC)

## Decisions Made

- probe_fn uses direct Anthropic API call instead of `_run_sdk_turn` from `agent.py` — `_run_sdk_turn` requires `job_id`, `db`, `redis` parameters and is designed for SSE-streaming customer conversations, not adversarial probe sequences
- `asyncio.get_event_loop().run_in_executor(None, ...)` wraps the synchronous Anthropic client inside `_async_probe` to avoid blocking the event loop during the timeout window
- Idempotency window is 30 minutes (vs 10 min for eval runs) because red team runs take longer (3 agent sequences × 5 turns each)
- Two separate psycopg2 connections: one for Step 3 INSERT (closed immediately after), one for Step 5-7 agent execution block (closed in finally)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] probe_fn uses direct Anthropic API instead of _run_sdk_turn**
- **Found during:** Task T01 implementation
- **Issue:** Plan describes calling `_run_sdk_turn(agent, message, conn_str)` but the actual signature is `(message, options, job_id, local_conversation_id, conn_str, db, redis)`. The function emits SSE events, manages conversation rows, and requires database + Redis infrastructure not available in the red-team context.
- **Fix:** Implemented probe_fn as a direct Anthropic API call using `anthropic.Anthropic().messages.create()` wrapped in `asyncio.run(asyncio.wait_for(..., timeout=60.0))`. This is the correct implementation: probe_fn sends a raw message to the agent persona and returns the response text, exactly as the service layer expects.
- **Files modified:** `apps/api/app/worker/tasks/runtime/red_team.py`
- **Commit:** `a23a26f`

## Known Stubs

None — both Celery tasks are fully implemented. The two xfail test stubs in `test_red_team_service.py` remain as `xfail` (Plan 07-05 stubs, not service stubs).

## Threat Flags

None — `red_team.py` is a Celery task file with no new network endpoints or auth paths. The probe_fn callable accesses the Anthropic API using the module-level `_ANTHROPIC_CLIENT` (reads `ANTHROPIC_API_KEY` from env — existing pattern). The psycopg2 connections use `connect_timeout=5` and are always closed in finally blocks.

---

## Self-Check

Files created/modified:
- `apps/api/app/worker/tasks/runtime/red_team.py` — FOUND
- `apps/api/app/worker/celery_app.py` — FOUND (M7 include + red-team-weekly entry)

Commits:
- `a23a26f` — FOUND (T01: run_red_team_beat + run_red_team tasks)
- `354816a` — FOUND (T02: celery_app include + beat_schedule)

Acceptance criteria verified:
- `run_red_team_beat` acks_late=True, queue="runtime", name correct — PASS
- `run_red_team` acks_late=True, max_retries=2, queue="runtime", name correct — PASS
- `run_red_team` signature `(self, agent_id: str) -> dict` — no conn_str — PASS
- Idempotency check queries `red_team_runs WHERE kind='m7:{agent_id}' AND status='running' AND started_at > NOW() - INTERVAL '30 minutes'` — PASS
- Returns `{"status": "already_running"}` on idempotent skip — PASS
- probe_fn uses `asyncio.run(asyncio.wait_for(..., timeout=60.0))` — PASS
- Three agents called sequentially (no chord) — PASS
- `deployment_blocked = (max_severity == "critical")` — PASS
- `UPDATE red_team_runs SET status='failed'` on exception before retry — PASS
- Import test passes — PASS
- celery_app include check passes — PASS
- red-team-weekly beat entry with correct task name and crontab — PASS
- eval-nightly unchanged — PASS

pytest: 2 xfailed (expected — Plan 07-05 stubs unchanged), 7 passed (validators), 0 new failures

## Self-Check: PASSED
