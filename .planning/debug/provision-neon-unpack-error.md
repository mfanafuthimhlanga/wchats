---
slug: provision-neon-unpack-error
status: resolved
trigger: "provision_neon Celery task fails with ValueError: not enough values to unpack (expected 3, got 0)"
created: "2026-05-15"
updated: "2026-05-15"
phase: "02"
---

# Debug Session: provision-neon-unpack-error

## Symptoms

- **Expected:** POST /agents → provision_neon task runs → agent.status becomes "ready"
- **Actual:** provision_neon task fails; agent stays in status=pending indefinitely
- **Error:** `ValueError: not enough values to unpack (expected 3, got 0)` (traceback=null in Redis result)
- **Timeline:** Recurring across sessions (result keys from 2026-05-15T16:43 UTC; same error today)
- **Reproduction:** POST /tenants → POST /agents → pipeline worker picks up task → immediate FAILURE

## Context

- Stack: FastAPI + Celery + Neon + Upstash Redis (native Windows, no Docker)
- Dispatch: `chain(provision_neon.s(tenant_id, agent_id), apply_migrations.s()).apply_async(queue="pipeline")`
- Pipeline worker: alive (responds to inspect ping), queue depth 0 after failure
- Redis result keys: FAILURE status, exc_type=ValueError, exc_message=["not enough values to unpack (expected 3, got 0)"]

## Key Files

- `apps/api/app/worker/tasks/pipeline/provision.py` — provision_neon task
- `apps/api/app/worker/tasks/pipeline/migrations.py` — apply_migrations (chain second link)
- `apps/api/app/api/v1/agents.py` — dispatch site
- `scripts/start_native.ps1` — worker start script (fixed)

## Evidence

- timestamp: 2026-05-15T00:00Z
  finding: "No 3-var tuple unpack in any application code under apps/api/app/"

- timestamp: 2026-05-15T00:01Z
  finding: "traceback=null in Redis is normal for prefork pool — tracebacks are not pickleable across subprocess boundary"

- timestamp: 2026-05-15T00:02Z
  finding: "use_fast_trace_task=False in worker subprocess (else branch of process_initializer). trace_task_ret is used, not fast_trace_task. _localized=[] is NOT the issue."

- timestamp: 2026-05-15T00:03Z
  finding: "Kombu serialization roundtrip works: body=[['t','a'],{},{callbacks:null,...}] length=3, unpack succeeds. Message transport is not corrupt."

- timestamp: 2026-05-15T00:04Z
  finding: "loads(b'', 'application/json', 'utf-8') returns b''. Then 'args, kwargs, embed = b''' raises ValueError: not enough values to unpack (expected 3, got 0). This is the exact error."

- timestamp: 2026-05-15T00:05Z
  finding: "Pipeline worker PID 636 has TWO billiard pool children (PIDs 12568 and 14228) both with IDENTICAL spawn command: spawn_main(parent_pid=636, pipe_handle=2288). Verified via wmic."

- timestamp: 2026-05-15T00:06Z
  finding: "Two pool workers sharing the same pipe_handle=2288 race to read from the same IPC pipe. One reads the task message; the other reads empty/corrupted data. The losing child calls trace_task_ret with body=b'' causing the ValueError."

## Eliminated

- Application code unpack: no 3-var destructuring in provision.py, migrations.py, parse.py, chunk.py
- fast_trace_task / _localized=[]: use_fast_trace_task=False, so fast_trace_task is not invoked
- Redis transport corruption: serialization roundtrip verified correct
- Proto1/proto2 message format issues: strategy.py correctly takes proto2 path for Celery-dispatched tasks

## Root Cause

**Billiard Windows pipe_handle collision in the prefork pool.**

Celery's prefork pool on Windows uses billiard's spawn mechanism (`spawn_main(parent_pid=..., pipe_handle=...)`). The pipeline worker spawned two pool children that both received the same `pipe_handle=2288`. This causes a race condition: when the parent sends a task message over the pipe, both children attempt to read it. The child that wins reads the full task body; the child that loses reads an empty byte sequence (`b''`). In `trace_task_ret`, when `content_type='application/json'` and `body=b''`, `kombu.serialization.loads(b'', ...)` returns `b''`, and the subsequent `args, kwargs, embed = b''` raises `ValueError: not enough values to unpack (expected 3, got 0)`.

The `traceback=null` is expected because Python traceback objects cannot be pickled across subprocess boundaries.

## Resolution

root_cause: "Two billiard pool worker subprocesses on the pipeline worker shared the same Windows pipe_handle (2288), causing a race condition on IPC reads that produced empty task bodies."
fix: "Changed scripts/start_native.ps1 to pass --pool=solo to both Celery workers. The solo pool executes tasks directly in the worker's main process with no subprocess spawning, eliminating the IPC pipe race entirely on Windows."
fix_files:
  - scripts/start_native.ps1

## Action Required

Kill the current pipeline worker (PID 636) and its children (PIDs 12568, 14228), then restart using the updated start_native.ps1 which adds --pool=solo.
