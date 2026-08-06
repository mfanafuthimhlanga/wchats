"""
Unit tests for OPS-01: turn_metrics write path in run_agent_turn.

Behavior under test:
  (a) A successful turn inserts one turn_metrics row with cost_usd/num_turns/
      stop_reason from the (mocked) ResultMessage-shaped result, latency_ms
      from the wall-clock around asyncio.run, escalated from the turn result,
      and tool_count from len(tool_calls_log).
  (b) When agent.response already exists for job_id (idempotency guard), NO
      turn_metrics row is written.
  (c) A turn_metrics INSERT failure is caught and logged; it does not raise
      into the turn or prevent job completion.

Mock strategy (mirrors test_agent_turn_connection_batch.py / test_agent_task.py):
  - patch app.worker.tasks.runtime.agent.psycopg2.connect with a MagicMock;
    its return_value is the shared tenant_conn mock. _write_turn_metrics is
    NOT mocked — we assert on the real INSERT executed against the mocked
    cursor, exactly like _persist_messages is exercised in sibling tests.
  - patch asyncio.run at the asyncio.run() boundary (do NOT use AsyncMock for
    SDK calls — same convention as test_agent_task.py).
  - claude_agent_sdk is monkeypatched before any import of agent.py.
"""

from __future__ import annotations

import sys
import types
import uuid
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Monkeypatch claude_agent_sdk BEFORE importing the agent task module.
# ---------------------------------------------------------------------------

def _make_fake_claude_agent_sdk() -> types.ModuleType:
    fake = types.ModuleType("claude_agent_sdk")
    fake.ClaudeSDKClient = MagicMock(name="ClaudeSDKClient")
    fake.ClaudeAgentOptions = MagicMock(name="ClaudeAgentOptions")
    fake.AssistantMessage = MagicMock(name="AssistantMessage")
    fake.ResultMessage = MagicMock(name="ResultMessage")
    fake.TextBlock = MagicMock(name="TextBlock")
    fake.ToolUseBlock = MagicMock(name="ToolUseBlock")
    fake.ToolResultBlock = MagicMock(name="ToolResultBlock")
    fake.ClaudeSDKError = type("ClaudeSDKError", (Exception,), {})
    fake.CLINotFoundError = type("CLINotFoundError", (Exception,), {})
    fake.CLIConnectionError = type("CLIConnectionError", (Exception,), {})
    fake.ProcessError = type("ProcessError", (Exception,), {})
    fake.CLIJSONDecodeError = type("CLIJSONDecodeError", (Exception,), {})

    def _tool_decorator(name, description, schema):
        def wrapper(fn):
            fn._tool_name = name
            return fn
        return wrapper

    fake.tool = _tool_decorator
    fake.create_sdk_mcp_server = MagicMock(return_value=MagicMock(name="mcp_server"))
    return fake


if "claude_agent_sdk" not in sys.modules:
    sys.modules["claude_agent_sdk"] = _make_fake_claude_agent_sdk()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent() -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = "Metrics Test Agent"
    agent.soul_role = "assistant"
    agent.soul_voice = "friendly"
    agent.soul_do_list = []
    agent.soul_donot_list = []
    agent.retrieval_strategy = {}
    agent.neon_connection_string = b"encrypted-bytes"
    return agent


def _make_job(job_id: str) -> MagicMock:
    job = MagicMock()
    job.id = job_id
    job.status = "running"
    job.finished_at = None
    return job


def _make_db_ctx(db: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# Canned SDK result WITH the new OPS-01 fields (total_cost_usd/num_turns/stop_reason)
_CANNED_RESULT_WITH_METRICS = {
    "response_text": (
        "You can return items within 14 days.\n\n"
        "CITATIONS:\n"
        "- Document: FAQ.pdf | Section: 1\n"
    ),
    "tool_calls_log": [
        {"tool_name": "retrieve", "input": {"query": "returns"}, "result": "..."},
    ],
    "escalated": False,
    "escalation_reason": None,
    "escalation_context": None,
    "sdk_session_id": "sdk-metrics-001",
    "total_cost_usd": 0.0123,
    "num_turns": 3,
    "stop_reason": "end_turn",
}


def _find_turn_metrics_execute_call(mock_cursor: MagicMock):
    """Locate the cur.execute() call whose SQL targets turn_metrics."""
    cursor_obj = mock_cursor.cursor.return_value.__enter__.return_value
    for c in cursor_obj.execute.call_args_list:
        sql = c.args[0] if c.args else c.kwargs.get("sql", "")
        if "INSERT INTO turn_metrics" in sql:
            return c
    return None


# ---------------------------------------------------------------------------
# (a) Happy path: one turn_metrics row with correct values
# ---------------------------------------------------------------------------

def test_successful_turn_writes_one_turn_metrics_row():
    """A served turn inserts exactly one turn_metrics row with correct values."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    job = _make_job(job_id)
    local_conv_id = "aabbccdd-0000-0000-0000-000000000010"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, job]

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect") as mock_connect,
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys"),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_METRICS),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="How do returns work?",
            conversation_id=None,
        )

    tenant_conn_mock = mock_connect.return_value
    call = _find_turn_metrics_execute_call(tenant_conn_mock)
    assert call is not None, "Expected exactly one INSERT INTO turn_metrics call"

    params = call.args[1]
    # params order: (id, job_id, conversation_id, agent_id, cost_usd, num_turns,
    #                 latency_ms, escalated, tool_count, stop_reason)
    assert params[1] == job_id
    assert params[2] == local_conv_id
    assert params[3] == agent_id
    assert params[4] == 0.0123          # cost_usd from ResultMessage.total_cost_usd
    assert params[5] == 3               # num_turns from ResultMessage.num_turns
    assert isinstance(params[6], int)   # latency_ms — wall-clock int
    assert params[6] >= 0
    assert params[7] is False           # escalated
    assert params[8] == 1               # tool_count == len(tool_calls_log)
    assert params[9] == "end_turn"      # stop_reason

    # Only one turn_metrics INSERT for the whole turn
    cursor_obj = tenant_conn_mock.cursor.return_value.__enter__.return_value
    turn_metrics_calls = [
        c for c in cursor_obj.execute.call_args_list
        if c.args and "INSERT INTO turn_metrics" in c.args[0]
    ]
    assert len(turn_metrics_calls) == 1


# ---------------------------------------------------------------------------
# (b) Idempotent path: no turn_metrics row written
# ---------------------------------------------------------------------------

def test_idempotent_skip_writes_no_turn_metrics_row():
    """When agent.response already exists for job_id, no turn_metrics INSERT occurs."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = MagicMock()  # existing row

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect") as mock_connect,
    ):
        result = run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="Hello again",
            conversation_id=None,
        )

    assert result == {"status": "already_complete", "job_id": job_id}
    # No tenant connection should even be opened on the idempotent path.
    mock_connect.assert_not_called()


# ---------------------------------------------------------------------------
# (c) turn_metrics INSERT failure is swallowed — turn still completes
# ---------------------------------------------------------------------------

def test_turn_metrics_insert_failure_does_not_fail_the_turn():
    """A turn_metrics INSERT exception is caught; run_agent_turn still completes normally."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    job = _make_job(job_id)
    local_conv_id = "aabbccdd-0000-0000-0000-000000000011"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect") as mock_connect,
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch(
            "app.worker.tasks.runtime.agent._write_turn_metrics",
            side_effect=RuntimeError("tenant DB unreachable"),
        ),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys"),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_METRICS),
        patch("app.worker.tasks.runtime.agent.emit") as mock_emit,
        patch("app.worker.tasks.runtime.agent.log") as mock_log,
    ):
        # Must not raise — the turn_metrics failure is swallowed.
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="Trigger a metrics failure",
            conversation_id=None,
        )

    # The terminal agent.response event must still have been emitted — the
    # served turn completed despite the telemetry failure.
    emitted_event_types = [c.args[1] for c in mock_emit.call_args_list if len(c.args) > 1]
    assert "agent.response" in emitted_event_types

    # Job must still be marked complete.
    assert job.status == "complete"

    # Warning logged for the swallowed failure.
    warning_calls = [c for c in mock_log.warning.call_args_list]
    assert any(
        c.args and c.args[0] == "run_agent_turn.turn_metrics_write_failed"
        for c in warning_calls
    ), f"Expected a turn_metrics_write_failed warning log, got: {warning_calls}"
