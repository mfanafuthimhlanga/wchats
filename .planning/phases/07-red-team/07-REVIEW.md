---
phase: "07"
reviewed: "2026-05-23"
depth: standard
files_reviewed: 9
files_reviewed_list:
  - apps/api/app/services/red_team_service.py
  - apps/api/app/worker/tasks/runtime/red_team.py
  - apps/api/app/api/v1/red_team.py
  - apps/api/app/schemas/red_team.py
  - apps/api/app/core/config.py
  - apps/api/app/worker/celery_app.py
  - apps/api/alembic_tenant/versions/0006_red_team_runs_status.py
  - apps/api/tests/unit/test_red_team_service.py
  - apps/api/tests/unit/test_red_team_task.py
findings:
  critical: 2
  warning: 5
  info: 2
  total: 9
status: fixed
fixed: "2026-05-23"
---

# Phase 07: Code Review Report

**Reviewed:** 2026-05-23
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found

## Summary

Phase 7 implements three adversarial Agent SDK runners (prompt injection, data leakage, hallucination), a Haiku severity classifier, Celery tasks with idempotency guards, and FastAPI routes for listing, fetching, and triggering red team runs. The architectural intent is sound — no conn_str in task args, IDOR checks at the route layer, per-tenant DB isolation. However, two critical correctness bugs were found: (1) a missing `WHERE` filter in the list query exposes every agent's runs to any agent request within the tenant, and (2) the `probe_fn` is synchronous but called from inside a running async event loop, which will raise `RuntimeError` at runtime and silently swallow all probe results. Five additional warnings cover a deprecated asyncio API, a misleading trigger response, a potential `ValueError` in `max()` on an empty-severity scenario, an unvalidated run-to-agent relationship in the detail query, and a structural connection leak window.

---

## Critical Issues

### CR-01: `_LIST_RED_TEAM_RUNS_SQL` has no `WHERE` filter — returns all runs, not agent-scoped runs

**File:** `apps/api/app/api/v1/red_team.py:67`
**Issue:** The list query fetches ALL rows from `red_team_runs` with no `WHERE` clause. The `agent_id` path parameter is correctly IDOR-checked against the authenticated tenant, but it is never passed to the SQL query. Any authenticated user can request `GET /agents/<any_valid_agent_id>/red-team-runs` and receive red team findings (including `probe_message` and `agent_response` text) for every agent in their tenant, not just the requested one. Because `red_team_runs` stores `kind = f"m7:{agent_id}"`, the intended filter key already exists in the table.

**Fix:**
```python
_LIST_RED_TEAM_RUNS_SQL = """
    SELECT id, kind, status, started_at, finished_at, findings, max_severity, deployment_blocked
    FROM red_team_runs
    WHERE kind = %(kind)s
    ORDER BY started_at DESC
    LIMIT 20
"""

# In list_red_team_runs, pass the kind filter:
rows = await asyncio.to_thread(
    _query_tenant_db_sync,
    conn_str,
    _LIST_RED_TEAM_RUNS_SQL,
    {"kind": f"m7:{agent_id}"},
)
```

Apply the same fix to `_GET_RED_TEAM_RUN_SQL` (see WR-02).

---

### CR-02: `probe_fn` is sync and calls `asyncio.run()` — raises `RuntimeError` when called from inside `_run_agent_loop` (running event loop)

**File:** `apps/api/app/services/red_team_service.py:271` (also lines 365, 458) and `apps/api/app/worker/tasks/runtime/red_team.py:116-126`

**Issue:** All three runner functions (`run_prompt_injection_agent`, `run_data_leakage_agent`, `run_hallucination_agent`) define an inner async coroutine `_run_agent_loop` and bridge it to sync via `asyncio.run(asyncio.wait_for(_run_agent_loop(...), ...))`. Inside `_run_agent_loop`, the code calls `probe_fn(probe_message)` — a synchronous function. That sync function (`probe_fn` built by `_build_probe_fn`) calls `asyncio.run(asyncio.wait_for(_async_probe(...), ...))` at line 123 of `red_team.py`. Python's `asyncio.run()` raises `RuntimeError: This event loop is already running` when called from within a coroutine that is already running inside an event loop. At runtime, every probe call silently returns `""` (the exception is caught at line 124-126 of `red_team.py`), meaning no agent interaction ever actually occurs, no findings are generated, and all runs complete with zero findings regardless of agent vulnerability. The unit tests do not catch this because they mock `asyncio.run` at the service level.

**Fix:** The `probe_fn` returned by `_build_probe_fn` must be an `async` function so that `_run_agent_loop` can `await` it directly:

```python
# In red_team.py — _build_probe_fn: change probe_fn to async
async def probe_fn(message: str) -> str:
    """Async probe: sends one message to the deployed agent and returns response text."""
    try:
        return await asyncio.wait_for(_async_probe(message), timeout=60.0)
    except Exception as exc:
        log.warning("probe_fn.timeout_or_error", error=str(exc))
        return ""

# In red_team_service.py — update Callable type annotation and all three call sites:
# Change: Callable[[str], str]  →  Callable[[str], Awaitable[str]]
# Change: probe_fn(probe_message)  →  await probe_fn(probe_message)
```

Update the `Callable` import to also import `Awaitable` from `typing`. The three call sites in the service file (lines 271, 365, 458) must all become `await probe_fn(probe_message)`.

---

## Warnings

### WR-01: `asyncio.get_event_loop()` deprecated in async context — use `asyncio.get_running_loop()`

**File:** `apps/api/app/worker/tasks/runtime/red_team.py:99`
**Issue:** `asyncio.get_event_loop()` called from within an `async def` function is deprecated since Python 3.10. In Python 3.12 it emits a `DeprecationWarning` when there is no current event loop set (it will create a new one instead of returning the running one). The correct API inside a coroutine is `asyncio.get_running_loop()`, which always returns the loop that is actually running the coroutine.

**Fix:**
```python
# Line 99 — replace:
response = await asyncio.get_event_loop().run_in_executor(
# with:
response = await asyncio.get_running_loop().run_in_executor(
```

---

### WR-02: `_GET_RED_TEAM_RUN_SQL` does not verify the run belongs to the requested agent — run_id can cross-agent fetch

**File:** `apps/api/app/api/v1/red_team.py:141`
**Issue:** The detail query filters only by `run_id`. The route accepts both `agent_id` and `run_id`, verifies agent ownership, then queries `WHERE id = %(run_id)s` with no `AND kind = %(kind)s` constraint. A client can request `GET /agents/<agent_A_id>/red-team-runs/<run_id_from_agent_B>` and successfully retrieve agent B's run data, as long as both agents belong to the same tenant. This is a same-tenant IDOR that bypasses agent-level scope.

**Fix:**
```python
_GET_RED_TEAM_RUN_SQL = """
    SELECT id, kind, status, started_at, finished_at, findings, max_severity, deployment_blocked
    FROM red_team_runs
    WHERE id = %(run_id)s
      AND kind = %(kind)s
"""

# In get_red_team_run, pass both params:
rows = await asyncio.to_thread(
    _query_tenant_db_sync,
    conn_str,
    _GET_RED_TEAM_RUN_SQL,
    {"run_id": str(run_id), "kind": f"m7:{agent_id}"},
)
```

---

### WR-03: `max()` on findings severities will raise `ValueError` if any finding carries an unrecognized severity string

**File:** `apps/api/app/worker/tasks/runtime/red_team.py:326`
**Issue:** `SEVERITY_ORDER.index(s)` inside the `max()` key function raises `ValueError` if `s` is not one of `["low", "medium", "high", "critical"]`. The `RedTeamFinding` Pydantic model enforces the `Literal` type for `severity`, but the `raw_findings` dicts in the service layer are populated directly from `block.input` (agent SDK tool-use output) before Pydantic validation. If the Agent SDK returns a non-standard severity string and the classify step raises (which is itself caught and not re-raised), the unvalidated raw value could propagate. Additionally, `"none"` (the no-findings fallback) is deliberately excluded from `SEVERITY_ORDER`, but a defensive guard is absent.

**Fix:**
```python
SEVERITY_ORDER = ["low", "medium", "high", "critical"]
severities = [f.severity for f in all_findings if f.severity in SEVERITY_ORDER]
max_severity = (
    max(severities, key=lambda s: SEVERITY_ORDER.index(s))
    if severities
    else "none"
)
```

---

### WR-04: `trigger_red_team_run` returns `run_id = task.id` (Celery task ID), not the actual `red_team_runs.id`

**File:** `apps/api/app/api/v1/red_team.py:268`
**Issue:** The POST trigger response returns `{"job_id": task.id, "run_id": task.id, ...}`. The `run_id` field is documented (in schema comment) as a "placeholder" because the actual UUID is generated inside the Celery task. However, any client that calls `GET /agents/{agent_id}/red-team-runs/{run_id}` using the returned `run_id` will always receive HTTP 404, because the value is a Celery task UUID which is never stored in `red_team_runs.id`. The API contract is broken — the trigger endpoint claims to return a `run_id` but it is unusable for the detail endpoint.

**Fix:** Either (a) generate the `run_id` UUID in the route before dispatching and pass it as a task kwarg so the task uses it, or (b) rename the field to `task_id` and remove `run_id` from the response, updating the schema accordingly.

```python
# Option (a) — generate run_id in the route:
import uuid
run_id = str(uuid.uuid4())
task = run_red_team.apply_async(
    kwargs={"agent_id": str(agent_id), "run_id": run_id},
    queue="runtime",
)
return {"job_id": task.id, "run_id": run_id, "message": "Red team run queued"}
# (run_red_team task signature must accept optional run_id parameter)
```

---

### WR-05: `_run_conn` opened outside the `try` block — connection leaked if the `try` block is never entered

**File:** `apps/api/app/worker/tasks/runtime/red_team.py:267`
**Issue:** `_run_conn = psycopg2.connect(conn_str, connect_timeout=5)` is opened on line 267, then the `try:` block begins on line 268. If anything raises between the `connect()` call and the `try:` entry (e.g., `MemoryError`, `KeyboardInterrupt`, a future code insertion), the `finally: _run_conn.close()` on line 289 will not run and the connection is leaked. The pattern used in Step 2 (idempotency check) correctly opens inside the `try`.

**Fix:**
```python
# Wrap the connect() call inside the try block:
try:
    _run_conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with _run_conn.cursor() as _cur:
            _cur.execute(...)
        _run_conn.commit()
    except Exception as exc:
        ...
finally:
    if '_run_conn' in dir():
        _run_conn.close()
```

Or more cleanly, use a context-manager pattern consistent with how `_check_conn` is handled in Step 2.

---

## Info

### IN-01: `SEVERITY_ORDER` is redefined inside the task function on every execution — should be a module-level constant

**File:** `apps/api/app/worker/tasks/runtime/red_team.py:326`
**Issue:** `SEVERITY_ORDER = ["low", "medium", "high", "critical"]` is re-created on every `run_red_team` invocation. This list duplicates the severity ordering implicitly defined by the `Literal` type in `RedTeamFinding`. It should be a module-level constant shared with the service layer to establish a single source of truth.

**Fix:**
```python
# At module top-level in red_team_service.py:
SEVERITY_ORDER: list[str] = ["low", "medium", "high", "critical"]

# Import and use in red_team.py:
from app.services.red_team_service import SEVERITY_ORDER
```

---

### IN-02: Test suite mocks `asyncio.run` at the service level — tests cannot detect the nested `asyncio.run` RuntimeError (CR-02)

**File:** `apps/api/tests/unit/test_red_team_service.py:154`
**Issue:** All three agent runner tests patch `app.services.red_team_service.asyncio.run` to return a pre-built list, bypassing the actual async runner and the `probe_fn` call chain entirely. This is an effective unit test strategy for the happy path, but it means the test suite cannot detect the `RuntimeError: This event loop is already running` that occurs at runtime (CR-02). An integration-level test that calls the runner with a real (or async-aware mock) `probe_fn` without mocking `asyncio.run` is needed to catch this class of bug.

**Fix:** Add at least one test that exercises `run_prompt_injection_agent` without mocking `asyncio.run`, using an `asyncio`-compatible mock `probe_fn` (i.e., an async callable or a sync callable wrapped appropriately), to verify the event loop bridging works end-to-end.

---

_Reviewed: 2026-05-23_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
