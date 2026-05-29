"""
Unit tests for the run_agent_turn Celery task (Plan 04-03).

Tests validate:
  - Idempotency: returns {"status": "already_complete"} if agent.response event exists
  - Agent-not-found guard: returns {} without raising when agent_id is unknown
  - First turn: creates conversation row, stores sdk_session_id, emits agent.response
    with parsed citations
  - Subsequent turn: passes stored sdk_session_id as resume= to ClaudeAgentOptions
  - Escalation: emits agent.escalated event before agent.response
  - Missing CITATIONS block: citations==[] and structlog.warning called

Mock strategy: patch asyncio.run at 'app.worker.tasks.runtime.agent.asyncio.run'
with a canned dict return value. Do NOT use AsyncMock for SDK — the task uses
asyncio.run() as the sync/async bridge; we mock that boundary only.

IMPORTANT: claude_agent_sdk must be monkeypatched before any import of agent.py
because agent.py imports it at module level (same pattern as test_agent_tools.py).
"""

from __future__ import annotations

import sys
import types
import uuid
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Monkeypatch claude_agent_sdk BEFORE importing the agent task module.
# agent.py uses `from claude_agent_sdk import ...` at module level.
# The fake provides enough for the import to succeed; actual SDK calls are
# mocked at the asyncio.run() boundary in each test.
# ---------------------------------------------------------------------------

def _make_fake_claude_agent_sdk() -> types.ModuleType:
    """Minimal stub of claude_agent_sdk sufficient for module-level import."""
    fake = types.ModuleType("claude_agent_sdk")

    # Classes / types referenced in agent.py
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

    # Also provide tool / create_sdk_mcp_server used by agent_tools (dependency)
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

def _make_agent(agent_id: str | None = None) -> MagicMock:
    """Minimal agent mock with all fields used by run_agent_turn."""
    agent = MagicMock()
    agent.id = uuid.UUID(agent_id) if agent_id else uuid.uuid4()
    agent.name = "Test Agent"
    agent.soul_role = "customer service representative"
    agent.soul_voice = "helpful"
    agent.soul_do_list = []
    agent.soul_donot_list = []
    agent.retrieval_strategy = {}
    agent.neon_connection_string = b"encrypted-bytes"
    return agent


def _make_job(job_id: str | None = None) -> MagicMock:
    job = MagicMock()
    job.id = job_id or str(uuid.uuid4())
    job.status = "running"
    job.finished_at = None
    return job


def _make_db_ctx(db: MagicMock) -> MagicMock:
    """Wrap a mock DB session in a context-manager wrapper."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=db)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# Canned SDK result — happy path with one citation
_CANNED_RESULT_WITH_CITATION = {
    "response_text": (
        "You can return items within 14 days.\n\n"
        "CITATIONS:\n"
        "- Document: FAQ.pdf | Section: 1\n"
    ),
    "tool_calls_log": [],
    "escalated": False,
    "escalation_reason": None,
    "escalation_context": None,
    "sdk_session_id": "sdk-abc-123",
}

# Canned result with no CITATIONS block
_CANNED_RESULT_NO_CITATIONS = {
    "response_text": "Some answer text with no citations block.",
    "tool_calls_log": [],
    "escalated": False,
    "escalation_reason": None,
    "escalation_context": None,
    "sdk_session_id": "sdk-def-456",
}

# Canned result with escalation
_CANNED_RESULT_ESCALATED = {
    "response_text": "I'm connecting you to a human agent.\n\nCITATIONS:\n- Document: N/A | Section: N/A\n",
    "tool_calls_log": [],
    "escalated": True,
    "escalation_reason": "Customer expressed frustration",
    "escalation_context": "Customer waiting 3 weeks for order",
    "sdk_session_id": "sdk-esc-789",
}


# ---------------------------------------------------------------------------
# Test 1: Idempotency skip
# ---------------------------------------------------------------------------

def test_idempotency_skip():
    """If an agent.response event already exists for this job_id, return immediately."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())

    mock_db = MagicMock()
    # Idempotency row exists
    mock_db.execute.return_value.fetchone.return_value = MagicMock()

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.emit") as mock_emit,
    ):
        result = run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="hello",
            conversation_id=None,
        )

    assert result == {"status": "already_complete", "job_id": job_id}, (
        f"Expected already_complete return, got: {result}"
    )
    mock_emit.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Agent not found
# ---------------------------------------------------------------------------

def test_agent_not_found():
    """Task returns {} gracefully when agent_id does not exist."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.return_value = None  # agent not found

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.log") as mock_log,
    ):
        result = run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="hello",
            conversation_id=None,
        )

    assert result == {}
    mock_log.error.assert_called_once()
    call_args = mock_log.error.call_args
    assert "run_agent_turn.agent_not_found" in str(call_args)


# ---------------------------------------------------------------------------
# Test 3: First turn — creates conversation, stores sdk_session_id, returns citations
# ---------------------------------------------------------------------------

def test_first_turn_creates_conversation_and_stores_sdk_session_id():
    """First turn (conversation_id=None) creates a conversation row and stores sdk_session_id."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    local_conv_id = "00000000-0000-0000-0000-000000000001"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, job]

    emitted_events: list[tuple[str, dict]] = []

    def fake_emit(jid, event_type, payload, db, redis):
        emitted_events.append((event_type, payload or {}))

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id") as mock_set_sdk,
        patch("app.worker.tasks.runtime.agent._persist_messages") as mock_persist,
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys prompt"),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_CITATION),
        patch("app.worker.tasks.runtime.agent.emit", side_effect=fake_emit),
    ):
        result = run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
        )

    # sdk_session_id must be stored
    mock_set_sdk.assert_called_once_with(
        "postgresql://tenant", local_conv_id, "sdk-abc-123"
    )

    # agent.response must be emitted
    response_events = [(et, p) for et, p in emitted_events if et == "agent.response"]
    assert len(response_events) == 1, f"Expected 1 agent.response event, got: {emitted_events}"

    _, response_payload = response_events[0]
    assert response_payload["citations"] == [{"document_name": "FAQ.pdf", "section": "1"}], (
        f"Citations mismatch: {response_payload['citations']}"
    )
    assert str(local_conv_id) in str(response_payload.get("conversation_id", "")), (
        f"conversation_id not in response payload: {response_payload}"
    )

    # _persist_messages must be called
    mock_persist.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: Subsequent turn — resume= gets the stored sdk_session_id
# ---------------------------------------------------------------------------

def test_subsequent_turn_resumes_with_stored_sdk_session_id():
    """Subsequent turn passes stored sdk_session_id as resume= to ClaudeAgentOptions."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    existing_conv_id = str(uuid.uuid4())
    stored_sdk_session_id = "stored-sdk-session-id"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    options_kwargs_captured: list[dict] = []

    class FakeClaudeAgentOptions:
        def __init__(self, **kwargs):
            options_kwargs_captured.append(kwargs)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch(
            "app.worker.tasks.runtime.agent._validate_conversation_owner",
            return_value={"id": existing_conv_id, "metadata": {"sdk_session_id": stored_sdk_session_id}},
        ),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys prompt"),
        patch("app.worker.tasks.runtime.agent.ClaudeAgentOptions", side_effect=FakeClaudeAgentOptions),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_CITATION),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="Follow-up question",
            conversation_id=existing_conv_id,
        )

    assert len(options_kwargs_captured) == 1, "ClaudeAgentOptions must be instantiated once"
    assert options_kwargs_captured[0].get("resume") == stored_sdk_session_id, (
        f"resume= must be the stored sdk_session_id, got: {options_kwargs_captured[0].get('resume')}"
    )


# ---------------------------------------------------------------------------
# Test 5: Escalation — emits agent.escalated before agent.response
# ---------------------------------------------------------------------------

def test_escalation_emits_agent_escalated_event():
    """Escalated result emits agent.escalated BEFORE agent.response."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    local_conv_id = "00000000-0000-0000-0000-000000000002"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    emitted_events: list[str] = []

    def fake_emit(jid, event_type, payload, db, redis):
        emitted_events.append(event_type)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys prompt"),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_ESCALATED),
        patch("app.worker.tasks.runtime.agent.emit", side_effect=fake_emit),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="I am very frustrated!",
            conversation_id=None,
        )

    # agent.escalated must appear before agent.response
    assert "agent.escalated" in emitted_events, f"agent.escalated missing from {emitted_events}"
    assert "agent.response" in emitted_events, f"agent.response missing from {emitted_events}"
    escalated_idx = emitted_events.index("agent.escalated")
    response_idx = emitted_events.index("agent.response")
    assert escalated_idx < response_idx, (
        f"agent.escalated ({escalated_idx}) must come before agent.response ({response_idx})"
    )


# ---------------------------------------------------------------------------
# Test 6: Missing CITATIONS block — empty list + structlog.warning
# ---------------------------------------------------------------------------

def test_citations_missing_returns_empty_list_and_warns():
    """Missing CITATIONS block yields citations==[] and logs a warning."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    local_conv_id = "00000000-0000-0000-0000-000000000003"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    response_payloads: list[dict] = []

    def fake_emit(jid, event_type, payload, db, redis):
        if event_type == "agent.response":
            response_payloads.append(payload or {})

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys prompt"),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_NO_CITATIONS),
        patch("app.worker.tasks.runtime.agent.emit", side_effect=fake_emit),
        patch("app.worker.tasks.runtime.agent.log") as mock_log,
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What's your return policy?",
            conversation_id=None,
        )

    assert len(response_payloads) == 1, f"Expected 1 agent.response event"
    assert response_payloads[0]["citations"] == [], (
        f"Expected empty citations list, got: {response_payloads[0]['citations']}"
    )

    # structlog.warning must have been called with citation_block_missing or similar
    warning_calls = [str(c) for c in mock_log.warning.call_args_list]
    assert any("citation" in w.lower() or "citations" in w.lower() for w in warning_calls), (
        f"Expected citation warning, got: {warning_calls}"
    )


# ---------------------------------------------------------------------------
# M5 — VAL chain dispatch from run_agent_turn (Plan 05-04)
# ---------------------------------------------------------------------------

# Canned result with a retrieve tool call carrying a captured result
_CANNED_RESULT_WITH_RETRIEVE = {
    "response_text": (
        "You can return items within 14 days.\n\n"
        "CITATIONS:\n"
        "- Document: FAQ.pdf | Section: 1\n"
    ),
    "tool_calls_log": [
        {
            "tool_name": "retrieve",
            "input": {"query": "return policy"},
            "result": "Return policy: 14 days, no questions asked.",
        }
    ],
    "escalated": False,
    "escalation_reason": None,
    "escalation_context": None,
    "sdk_session_id": "sdk-val-001",
}


def test_validators_dispatched():
    """run_agent_turn dispatches the Gatekeeper→Auditor→Strategist chain after agent.response."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    local_conv_id = "00000000-0000-0000-0000-000000000010"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, job]

    # Mock the chain result so apply_async can be asserted
    mock_chain_instance = MagicMock(name="chain_instance")
    mock_celery_chain = MagicMock(name="celery_chain", return_value=mock_chain_instance)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys prompt"),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_RETRIEVE),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", mock_celery_chain),
    ):
        result = run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
        )

    # chain(...).apply_async must have been called once with queue="runtime"
    mock_celery_chain.assert_called_once()
    mock_chain_instance.apply_async.assert_called_once_with(queue="runtime")

    # Verify auditor was called with 6 positional args (the last call arg to celery_chain)
    chain_call_args = mock_celery_chain.call_args[0]
    # chain_call_args is (gatekeeper_sig, auditor_sig, strategist_sig)
    assert len(chain_call_args) == 3, (
        f"Expected 3 chain tasks (gatekeeper, auditor, strategist), got {len(chain_call_args)}"
    )


def test_validators_not_dispatched_on_idempotency_skip():
    """Validation chain is NOT dispatched on the idempotency-skip path."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())

    mock_db = MagicMock()
    # Idempotency row exists — triggers early return
    mock_db.execute.return_value.fetchone.return_value = MagicMock()

    mock_celery_chain = MagicMock(name="celery_chain")

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", mock_celery_chain),
    ):
        result = run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="hello",
            conversation_id=None,
        )

    assert result == {"status": "already_complete", "job_id": job_id}
    mock_celery_chain.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 12 — D-10 retrieve cap + D-11 wall-clock guard regression tests
# (Plan 12-01, 2026-05-29)
# ---------------------------------------------------------------------------

def test_max_turns_capped_to_three():
    """D-10 regression: ClaudeAgentOptions must be constructed with max_turns=3."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    local_conv_id = "00000000-0000-0000-0000-000000000020"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, job]

    options_kwargs_captured: list[dict] = []

    class FakeClaudeAgentOptions:
        def __init__(self, **kwargs):
            options_kwargs_captured.append(kwargs)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys prompt"),
        patch("app.worker.tasks.runtime.agent.ClaudeAgentOptions", side_effect=FakeClaudeAgentOptions),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_CITATION),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
        )

    assert len(options_kwargs_captured) == 1, (
        "ClaudeAgentOptions must be instantiated exactly once per turn"
    )
    assert options_kwargs_captured[0]["max_turns"] == 3, (
        f"D-10 regression: expected max_turns=3, got max_turns={options_kwargs_captured[0].get('max_turns')}"
    )


def test_wall_clock_guard_is_ninety_seconds():
    """D-11 regression: asyncio.wait_for must be called with timeout=90."""
    import asyncio as _asyncio

    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    local_conv_id = "00000000-0000-0000-0000-000000000021"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, job]

    wait_for_kwargs: list[dict] = []

    async def fake_wait_for(coro, timeout):
        wait_for_kwargs.append({"timeout": timeout})
        # Close the passed coroutine to avoid ResourceWarning; return the canned result.
        coro.close()
        return _CANNED_RESULT_WITH_CITATION

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys prompt"),
        # Patch wait_for but keep asyncio.run real so it drives the fake coroutine.
        patch("app.worker.tasks.runtime.agent.asyncio.wait_for", side_effect=fake_wait_for),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
        )

    assert len(wait_for_kwargs) == 1, (
        "asyncio.wait_for must be called exactly once per turn"
    )
    assert wait_for_kwargs[0]["timeout"] == 90, (
        f"D-11 regression: expected timeout=90, got timeout={wait_for_kwargs[0].get('timeout')}"
    )
