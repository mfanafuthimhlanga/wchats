---
phase: 05-validation-chain
plan: 04
status: complete
wave: 4
completed_at: 2026-05-23
---

# Plan 05-04 Summary: Retrieve Result Capture + Validation Chain Dispatch

## What was done

Two targeted changes were made to `apps/api/app/worker/tasks/runtime/agent.py` to close a real gap identified during planning (Pitfall 3) and wire the validation chain into `run_agent_turn`.

### Change 1: Imports

Added at the top of `agent.py`:
- `import json` (module-level, replacing the inline import inside `_persist_messages`)
- `from celery import chain as celery_chain`
- `from app.worker.tasks.runtime.validators import run_gatekeeper, run_auditor, run_strategist`

### Change 2: Retrieve result capture in `_run_sdk_turn`

**The gap (Pitfall 3):** `_run_sdk_turn` previously recorded only `{tool_name, input}` per tool call. The `ToolResultBlock` content was emitted to SSE but discarded, meaning the Auditor would have had no retrieved context to ground claims against.

**The fix:** In the `ToolResultBlock` branch, after the existing `emit("agent.tool_result", ...)` call, we now walk `tool_calls_log` in reverse and store the result content onto the first `retrieve` entry lacking a `"result"` key:

```python
# Capture retrieve result for Auditor (M5 — plan 05-04)
for tc in reversed(tool_calls_log):
    if tc.get("tool_name") == "retrieve" and "result" not in tc:
        tc["result"] = str(getattr(block, "content", ""))[:1800]
        break
```

The `[:1800]` cap limits individual in-memory entries. Non-retrieve tools are unaffected.

### Change 3: Validation chain dispatch

Placed **immediately after** `emit(job_id, "agent.response", ...)` and `db.commit()` on the **success path only**, before `log.info("run_agent_turn.complete")`:

```python
# Dispatch validation chain (M5 — VAL-04)
retrieve_results = [tc.get("result") for tc in tool_calls_log
                     if tc.get("tool_name") == "retrieve" and tc.get("result")]
retrieved_context_json = json.dumps([str(r)[:600] for r in retrieve_results][:3])
celery_chain(
    run_gatekeeper.si(str(agent_id), job_id, response_text, message),
    run_auditor.si(str(agent_id), job_id, response_text, message,
                   retrieved_context_json, str(local_conversation_id)),
    run_strategist.si(str(agent_id), job_id, response_text, message),
).apply_async(queue="runtime")
```

**Truncation rule:** Top-3 retrieve results, each capped at 600 chars, before `json.dumps`. This guards against Redis task-arg DoS (threat T-05-04-01).

**Why `chain`, not `chord`:** `chord` deadlocks on `worker_pool=solo` because the callback task waits for all chord members to complete, but on a single-process pool there is no spare worker to run the callback (RESEARCH Pitfall 1). `chain` with `.si()` (immutable signatures) runs tasks sequentially and is safe on any pool configuration.

**Dispatch location:** The dispatch is strictly on the success branch — after the user already has their response — and does NOT execute on:
- idempotency-skip return (`agent.response` row already exists)
- agent-not-found return
- job-not-found return
- conversation-not-found return
- any exception branch

## Test changes (`apps/api/tests/unit/test_agent_task.py`)

### `test_validators_dispatched` (de-xfailed, implemented)
- Added `_CANNED_RESULT_WITH_RETRIEVE` fixture with a `tool_calls_log` entry that includes a `"result"` key to simulate the capture
- Patches `app.worker.tasks.runtime.agent.celery_chain` with a `MagicMock`
- Asserts `celery_chain` is called once with 3 positional task signatures
- Asserts `chain_instance.apply_async` is called once with `queue="runtime"`

### `test_validators_not_dispatched_on_idempotency_skip` (new)
- Confirms `celery_chain` is NOT called when the idempotency guard fires (early return with `{"status": "already_complete"}`)

## Verification

```
cd apps/api && pytest tests/unit/test_agent_task.py -x -q
# 8 passed (6 existing + 2 new)
```

All acceptance criteria met:
- `from celery import chain as celery_chain` present
- `from app.worker.tasks.runtime.validators import run_gatekeeper, run_auditor, run_strategist` present
- `run_gatekeeper.si(`, `run_auditor.si(`, `run_strategist.si(`, `.apply_async(queue="runtime")` all present
- `chord(` count = 0
- `[:600]` and `[:3]` truncation present
- `tool_name == "retrieve"` capture with `[:1800]` present
- `test_validators_dispatched` no longer xfail and passes
