"""
Unit tests for OPS-04: Langfuse v4 trace+generation emission on the agent turn.

Behavior under test:
  (a) With `_langfuse` monkeypatched to None (no keys configured), the turn
      completes and no Langfuse call is attempted.
  (b) With a mock Langfuse client, `create_score` is called with
      `trace_id=job_id` and `flush` is called exactly once per turn.
  (c) When the mock Langfuse client raises, the exception is swallowed and
      the turn still returns/completes normally.

Mock strategy (mirrors test_agent_turn_metrics.py / test_agent_task.py):
  - claude_agent_sdk is monkeypatched before any import of agent.py.
  - `app.worker.tasks.runtime.agent._langfuse` (module-level client) is
    patched directly per test — no real Langfuse network calls.
  - asyncio.run is mocked at the boundary with a canned ResultMessage-shaped
    dict (including total_cost_usd/num_turns/stop_reason).
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
    agent.name = "Langfuse Test Agent"
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


_CANNED_RESULT_WITH_METRICS = {
    "response_text": (
        "You can return items within 14 days.\n\n"
        "CITATIONS:\n"
        "- Document: FAQ.pdf | Section: 1\n"
    ),
    "tool_calls_log": [],
    "escalated": False,
    "escalation_reason": None,
    "escalation_context": None,
    "sdk_session_id": "sdk-lf-001",
    "total_cost_usd": 0.0456,
    "num_turns": 2,
    "stop_reason": "end_turn",
}


def _run_turn(job_id: str, agent_id: str, agent: MagicMock, job: MagicMock, local_conv_id: str):
    """Shared drive of run_agent_turn with the standard happy-path mocks."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, job]

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent._write_turn_metrics"),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys"),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_METRICS),
        patch("app.worker.tasks.runtime.agent.emit") as mock_emit,
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="How do returns work?",
            conversation_id=None,
        )
    return mock_emit


# ---------------------------------------------------------------------------
# (a) _langfuse is None — no-op, turn still completes
# ---------------------------------------------------------------------------

def test_langfuse_none_no_call_attempted_turn_completes():
    """When _langfuse is None, no Langfuse call is attempted and the turn completes."""
    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    job = _make_job(job_id)
    local_conv_id = "aabbccdd-1111-0000-0000-000000000001"

    with patch("app.worker.tasks.runtime.agent._langfuse", None):
        mock_emit = _run_turn(job_id, agent_id, agent, job, local_conv_id)

    emitted_event_types = [c.args[1] for c in mock_emit.call_args_list if len(c.args) > 1]
    assert "agent.response" in emitted_event_types
    assert job.status == "complete"


# ---------------------------------------------------------------------------
# (b) mock Langfuse client — create_score(trace_id=job_id), flush called once
# ---------------------------------------------------------------------------

def test_langfuse_trace_linked_by_job_id_and_flushed_once():
    """create_score is called with trace_id=job_id; flush is called exactly once."""
    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    job = _make_job(job_id)
    local_conv_id = "aabbccdd-1111-0000-0000-000000000002"

    mock_langfuse_client = MagicMock()
    # start_as_current_generation used as a context manager
    mock_langfuse_client.start_as_current_generation.return_value.__enter__ = MagicMock(
        return_value=MagicMock()
    )
    mock_langfuse_client.start_as_current_generation.return_value.__exit__ = MagicMock(
        return_value=False
    )

    with patch("app.worker.tasks.runtime.agent._langfuse", mock_langfuse_client):
        _run_turn(job_id, agent_id, agent, job, local_conv_id)

    assert mock_langfuse_client.create_score.called, "create_score must be called"
    for c in mock_langfuse_client.create_score.call_args_list:
        assert c.kwargs.get("trace_id") == job_id, (
            f"create_score must use trace_id=job_id, got: {c.kwargs}"
        )

    assert mock_langfuse_client.flush.call_count == 1, (
        f"flush must be called exactly once per turn (Pitfall 3), "
        f"got {mock_langfuse_client.flush.call_count}"
    )


# ---------------------------------------------------------------------------
# (c) Langfuse client raises — exception swallowed, turn still completes
# ---------------------------------------------------------------------------

def test_langfuse_exception_swallowed_turn_still_completes():
    """A Langfuse client exception must not propagate; the turn still completes."""
    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    job = _make_job(job_id)
    local_conv_id = "aabbccdd-1111-0000-0000-000000000003"

    mock_langfuse_client = MagicMock()
    mock_langfuse_client.start_as_current_generation.side_effect = RuntimeError(
        "Langfuse unreachable"
    )

    with patch("app.worker.tasks.runtime.agent._langfuse", mock_langfuse_client):
        # Must not raise.
        mock_emit = _run_turn(job_id, agent_id, agent, job, local_conv_id)

    emitted_event_types = [c.args[1] for c in mock_emit.call_args_list if len(c.args) > 1]
    assert "agent.response" in emitted_event_types
    assert job.status == "complete"
