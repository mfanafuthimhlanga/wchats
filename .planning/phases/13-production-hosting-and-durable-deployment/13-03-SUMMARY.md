---
phase: 13-production-hosting-and-durable-deployment
plan: "03"
subsystem: runtime/agent
tags: [pooling, neon, psycopg2, celery, performance, prod-05]
dependency_graph:
  requires: []
  provides: [PROD-05-connection-pooling]
  affects: [apps/api/app/worker/tasks/runtime/agent.py]
tech_stack:
  added: []
  patterns: [single-connection-per-celery-task, try-except-finally-lifecycle]
key_files:
  modified:
    - apps/api/app/worker/tasks/runtime/agent.py
  created:
    - apps/api/tests/unit/test_agent_turn_connection_batch.py
decisions:
  - "Use explicit try/except/finally rather than context-manager form of psycopg2.connect — preserves the existing retry logic in the except block while guaranteeing close() in finally"
  - "Leave _mark_conversation_escalated in agent_tools.py untouched — it runs inside the SDK tool executor on a separate thread; sharing the turn connection across threads is unsafe"
  - "All four helpers now accept `conn` (open connection) instead of `conn_str` — connection lifecycle is fully owned by run_agent_turn"
metrics:
  duration: "~7 minutes"
  completed: "2026-06-29"
  tasks_completed: 1
  tasks_total: 1
  files_modified: 2
  files_created: 1
status: complete
---

# Phase 13 Plan 03: Neon Connection Pooling (PROD-05) Summary

One pooled tenant-DB connection per `run_agent_turn` — closed in `finally` — replacing four separate `psycopg2.connect()` calls that previously opened and closed a new connection inside each write helper.

## What Was Built

Collapsed four independent `psycopg2.connect()` + `close()` cycles per conversational turn into a single connection opened once in `run_agent_turn` and threaded through all four write helpers:

| Helper | Before | After |
|--------|--------|-------|
| `_create_conversation_row` | opened own connection | accepts `conn` arg |
| `_validate_conversation_owner` | opened own connection | accepts `conn` arg |
| `_set_sdk_session_id` | opened own connection | accepts `conn` arg |
| `_persist_messages` | opened own connection | accepts `conn` arg |

**Connection lifecycle in `run_agent_turn`:**
```python
tenant_conn = psycopg2.connect(conn_str, connect_timeout=5)
try:
    # ... all four write helpers receive tenant_conn ...
except Exception as exc:
    # ... existing retry/fail logic (unchanged) ...
finally:
    tenant_conn.close()  # runs even when _run_sdk_turn raises
```

- Uses `agent.neon_connection_string` (Neon PgBouncer pooled endpoint), NOT the direct connection
- PgBouncer transaction-mode compatible: no named prepared statements, no SET session vars
- `conn_str` never logged or passed in task args (CTL-08 invariant preserved)
- `_mark_conversation_escalated` in `agent_tools.py` left untouched (runs on SDK tool thread)

## Verification

```
pytest tests/unit/test_agent_turn_connection_batch.py tests/unit/test_agent_task.py -x -q
16 passed in 7.56s
```

Acceptance criteria confirmed:
- `grep -c "psycopg2.connect" apps/api/app/worker/tasks/runtime/agent.py` → `1` (single per-turn open)
- `grep -n "neon_direct_connection_string" apps/api/app/worker/tasks/runtime/agent.py` → no matches

## Deviations from Plan

None — plan executed exactly as written.

## Commits

| Hash | Description |
|------|-------------|
| f2aedef | feat(13-03): collapse 4 per-turn psycopg2.connect() calls into ONE (PROD-05) |

## Known Stubs

None.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. The refactor closes a connection leak on exception paths (T-13-03-03 mitigated by the finally block). No new threat surface.

## Self-Check: PASSED

- [x] `apps/api/app/worker/tasks/runtime/agent.py` modified and committed
- [x] `apps/api/tests/unit/test_agent_turn_connection_batch.py` created and committed
- [x] `apps/api/tests/unit/test_agent_task.py` updated and committed
- [x] Commit f2aedef verified in git log
- [x] 16 tests pass, 0 failures
