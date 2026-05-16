---
phase: 04-reasoning-engine-widget
plan: 03
subsystem: api
tags: [celery, celery-task, async-sdk, sse, psycopg2, smtplib, citation-extraction, escalation, conversation, idempotency]

requires:
  - phase: 04-02
    provides: "build_tool_server, build_system_prompt, RetrievalStrategy, four MCP tools"
  - phase: 04-01
    provides: "SMTP_HOST/SMTP_PORT/SMTP_FROM/OWNER_EMAIL in settings"
  - phase: 03
    provides: "retrieve_and_rank pattern, emit(), get_sync_db(), fernet_decrypt(), Agent, Job models"

provides:
  - "run_agent_turn Celery task on runtime queue — full SDK orchestration with SSE events"
  - "send_escalation_email fire-and-forget SMTP helper with structlog fallback"
  - "_create_conversation_row, _validate_conversation_owner, _set_sdk_session_id, _persist_messages psycopg2 helpers"
  - "R-02 resolved: SDK session_id captured from ResultMessage, stored in conversations.metadata, used as resume= on subsequent turns"
  - "R-05 resolved: allowed_tools uses full mcp__customer-tools__* namespace"

affects:
  - "04-04: FastAPI /chat/message route dispatches run_agent_turn"
  - "04-05: Widget SSE consumer expects agent.thinking → agent.tool_call → agent.response event sequence"
  - "04-06: Eval harness tests idempotency via agent.response sentinel in job_events"

tech-stack:
  added: []
  patterns:
    - "asyncio.run() as the only sync/async bridge in Celery tasks (Python 3.12 compat)"
    - "psycopg2 module-level helpers (try/finally conn.close) for tenant DB writes"
    - "sys.modules monkeypatch for claude_agent_sdk before test module import"
    - "Mock asyncio.run boundary (canned dict) — do NOT use AsyncMock for SDK calls"

key-files:
  created:
    - "apps/api/app/services/escalation.py"
    - "apps/api/app/worker/tasks/runtime/agent.py"
    - "apps/api/tests/unit/test_agent_task.py"
  modified:
    - "apps/api/app/worker/celery_app.py"

key-decisions:
  - "asyncio.run(asyncio.wait_for(_run_sdk_turn(...), timeout=30)) — wall-clock guard inside asyncio.run, not outside"
  - "sdk_session_id stored in conversations.metadata via jsonb_set UPDATE (parameterised)"
  - "Escalation detection from ToolUseBlock.name evidence only — never from parsed agent prose (T-04-03-03)"
  - "_persist_messages writes user + assistant messages + tool_calls rows in one psycopg2 connection"
  - "claude_agent_sdk monkeypatched in sys.modules before test import (same pattern as test_agent_tools.py)"

requirements-completed: [AGT-01, AGT-02, AGT-03, AGT-04, AGT-05]

duration: 24min
completed: 2026-05-16
---

# Phase 04 Plan 03: Agent Celery Task Summary

**run_agent_turn Celery task bridges async Claude Agent SDK into sync Celery worker via asyncio.run(), resolves R-02 sdk_session_id persistence, and emits five SSE events with citation extraction and SMTP escalation routing**

## Performance

- **Duration:** 24 min
- **Started:** 2026-05-16T18:17:59Z
- **Completed:** 2026-05-16T18:42:15Z
- **Tasks:** 2 (Task 1: escalation helper, Task 2: TDD agent task + tests)
- **Files modified:** 4

## Accomplishments

- `send_escalation_email(agent, reason, context)` is fire-and-forget: fallback to structlog.warning when SMTP unconfigured; SMTP exception caught and logged; never raises
- `run_agent_turn` Celery task on runtime queue with `bind=True, acks_late=True, max_retries=2`; idempotency guard prevents duplicate SDK calls on retry
- R-02 resolved: `ResultMessage.session_id` captured from stream, stored via `jsonb_set` in `conversations.metadata['sdk_session_id']`, passed as `resume=` on subsequent turns
- Six unit tests pass with mocked asyncio.run boundary — no real SDK subprocess launched in tests

## Task Signature and Decorator

```python
@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_agent_turn",
)
def run_agent_turn(
    self,
    job_id: str,
    agent_id: str,
    message: str,
    conversation_id: str | None,
) -> dict:
```

## SSE Event Sequence and Payload Shapes

| Order | Event | Payload | When |
|-------|-------|---------|------|
| 1 | `agent.thinking` | `{"agent_id": agent_id}` | Always — first event |
| 2+ | `agent.tool_call` | `{"tool_name": str, "input": dict}` | Per ToolUseBlock seen in stream |
| 3+ | `agent.tool_result` | `{"tool_name": str, "summary": str[:200]}` | Per ToolResultBlock seen |
| 4 | `agent.escalated` | `{"reason": str, "context": str, "conversation_id": str}` | Only if escalate_to_human ToolUseBlock |
| 5 | `agent.response` | `{"text": str, "citations": list, "conversation_id": str}` | Always — terminal event |
| (err) | `agent.failed` | `{"error": str}` | On final retry exhaustion only |

## Conversation Ownership Validation Query

```sql
SELECT id, metadata
FROM conversations
WHERE id = %s AND agent_id = %s
LIMIT 1
```

Returns `None` on miss → emit `agent.failed` with `"error": "conversation_not_found"` and return early. Prevents cross-agent conversation hijacking (T-04-03-01).

## R-02 Resolution: sdk_session_id Capture and Storage

```
FIRST TURN (conversation_id is None):
  1. _create_conversation_row(conn_str, agent_id) → local_conversation_id (uuid4)
  2. ClaudeAgentOptions(resume=None, ...)
  3. asyncio.run(_run_sdk_turn(...)) → result["sdk_session_id"] = ResultMessage.session_id
  4. _set_sdk_session_id(conn_str, local_conversation_id, sdk_session_id)
     → UPDATE conversations SET metadata = jsonb_set(metadata, '{sdk_session_id}', to_jsonb(%s::text)) WHERE id = %s

SUBSEQUENT TURN (conversation_id provided):
  1. _validate_conversation_owner(conn_str, conversation_id, agent_id) → row
  2. sdk_resume = row["metadata"].get("sdk_session_id")
  3. ClaudeAgentOptions(resume=sdk_resume, ...)   ← passes stored SDK session ID, NOT local UUID
```

## Citation Regex Pattern

```python
CITATIONS_REGEX = re.compile(r"CITATIONS:\n((?:- Document: .+ \| Section: .+\n?)+)")
_CITATION_ENTRY  = re.compile(r"- Document: (.+) \| Section: (.+)")
```

- Missing block → `citations_list = []` + `structlog.warning("citation_block_missing", ...)`
- No exception raised — agent.response still emits with empty citations list

## Escalation Routing Path

```
ToolUseBlock.name.endswith("escalate_to_human")
  → escalated = True; escalation_reason = block.input.get("reason"); escalation_context = block.input.get("context")

After SDK turn returns:
  if escalated:
    emit(job_id, "agent.escalated", {"reason": ..., "context": ..., "conversation_id": ...}, db, _redis)
    send_escalation_email(agent, escalation_reason, escalation_context)  ← called as notify_fn lambda from build_tool_server

send_escalation_email:
  - If SMTP_HOST/SMTP_FROM/OWNER_EMAIL any None → structlog.warning("escalation.email_not_configured")
  - Else → smtplib.SMTP(host, port, timeout=5).starttls().sendmail(...)
  - On smtplib exception → structlog.warning("escalation.email_failed") — NEVER re-raises
```

## Unit Test Count

**6 tests — all pass:**

1. `test_idempotency_skip` — pre-existing agent.response event → returns `{"status": "already_complete"}`, emit never called
2. `test_agent_not_found` — db.get(Agent) returns None → returns `{}`, logs error
3. `test_first_turn_creates_conversation_and_stores_sdk_session_id` — first turn creates conversation, stores sdk_session_id, emits agent.response with parsed citations
4. `test_subsequent_turn_resumes_with_stored_sdk_session_id` — validates ClaudeAgentOptions receives `resume="stored-id"` from conversations.metadata
5. `test_escalation_emits_agent_escalated_event` — escalated=True result → agent.escalated emitted before agent.response
6. `test_citations_missing_returns_empty_list_and_warns` — no CITATIONS block → citations==[], structlog.warning called

## Task Commits

1. **Task 1: escalation helper** — `477bfbf` (feat)
2. **Task 2 (TDD RED): failing tests** — `1e462c8` (test)
3. **Task 2 (TDD GREEN): agent.py + celery_app + test update** — `7c624da` (feat)

## Files Created/Modified

- `apps/api/app/services/escalation.py` — fire-and-forget SMTP with structlog fallback
- `apps/api/app/worker/tasks/runtime/agent.py` — run_agent_turn Celery task; module-level psycopg2 helpers; _run_sdk_turn async helper; CITATIONS_REGEX
- `apps/api/app/worker/celery_app.py` — extended include list with `app.worker.tasks.runtime.agent`
- `apps/api/tests/unit/test_agent_task.py` — 6 unit tests with claude_agent_sdk monkeypatch + asyncio.run mock

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed claude-agent-sdk==0.1.81**
- **Found during:** Task 2 verification (GREEN phase)
- **Issue:** `claude_agent_sdk` not installed on the Python interpreter; module-level import in `agent_tools.py` fails at `python -c "from app.worker.tasks.runtime.agent import ..."` verification
- **Fix:** Installed `claude-agent-sdk==0.1.81` from PyPI (71.6 MB whl download)
- **Files modified:** Python environment only (pyproject.toml already listed it)
- **Verification:** `python -c "import claude_agent_sdk; print('OK')"` exits 0
- **Committed in:** environment change only (not a code commit)

**2. [Rule 1 - Bug] Removed forbidden pattern from comment text**
- **Found during:** Task 2 post-implementation grep check
- **Issue:** `grep -v '^#' agent.py | grep -c "loop.run_until_complete"` returned 1 because the phrase appeared in an *indented* comment (not `^#` line), which `grep -v '^#'` does not strip
- **Fix:** Rewrote the inline comment to avoid the forbidden substring while preserving the meaning
- **Files modified:** `apps/api/app/worker/tasks/runtime/agent.py`
- **Verification:** `grep -v '^#' agent.py | grep -c "loop.run_until_complete"` returns 0
- **Committed in:** `7c624da` (Task 2 commit)

**3. [Rule 2 - Missing Critical] Added claude_agent_sdk sys.modules monkeypatch to test file**
- **Found during:** Task 2 TDD RED phase (test file first run)
- **Issue:** Tests failed with `ModuleNotFoundError: No module named 'claude_agent_sdk'` even when the SDK is installed in some environments, because `agent_tools.py` imports `claude_agent_sdk` at module level. The test_agent_tools.py file already established this pattern with `sys.modules["claude_agent_sdk"] = _make_fake_sdk()`.
- **Fix:** Added identical monkeypatch block in `test_agent_task.py` before any `from app.worker.tasks.runtime.agent import ...` statement
- **Files modified:** `apps/api/tests/unit/test_agent_task.py`
- **Verification:** All 6 tests pass in both SDK-present and SDK-absent environments
- **Committed in:** `7c624da` (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (1 blocking install, 1 bug in comment, 1 missing critical test setup)
**Impact on plan:** All fixes required for correct operation and test isolation. No scope creep.

## Threat Surface Scan

No new network endpoints introduced. `agent.py` adds one psycopg2 tenant DB write path per turn, but this was in scope (planned in the threat model). No new trust boundaries beyond those documented in T-04-03-01 through T-04-03-07.

## Self-Check: PASSED

Files exist:
- `apps/api/app/services/escalation.py` — FOUND
- `apps/api/app/worker/tasks/runtime/agent.py` — FOUND
- `apps/api/app/worker/celery_app.py` (modified) — FOUND
- `apps/api/tests/unit/test_agent_task.py` — FOUND

Commits exist:
- `477bfbf` — feat(04-03): add escalation.send_escalation_email
- `1e462c8` — test(04-03): add failing tests for run_agent_turn
- `7c624da` — feat(04-03): add run_agent_turn Celery task

## Next Phase Readiness

- `run_agent_turn` is ready to be dispatched from the FastAPI `/chat/message` route (Plan 04-04)
- Widget SSE consumer can subscribe to `job_events:{job_id}` and render the event sequence
- `conversation_id` returned in `agent.response` payload enables multi-turn conversations from the widget

---
*Phase: 04-reasoning-engine-widget*
*Completed: 2026-05-16*
