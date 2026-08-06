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
with a canned dict return value. Do NOT use AsyncMock for SDK -- the task uses
asyncio.run() as the sync/async bridge; we mock that boundary only.

IMPORTANT: claude_agent_sdk must be monkeypatched before any import of agent.py
because agent.py imports it at module level (same pattern as test_agent_tools.py).
"""

from __future__ import annotations

import sys
import types
import uuid
from unittest.mock import MagicMock, patch

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


# Canned SDK result -- happy path with one citation
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
# WIRE-05 (23-01): fixed, obviously-synthetic _persist_messages return values.
# A bare patch() of _persist_messages hands back a MagicMock; emit() is also
# separately patched at every site below, so a MagicMock message_id would
# flow straight into a captured payload silently rather than crash (T-23-GA-03,
# the "false green" this repair closes). Every patch site below therefore
# supplies an explicit return_value. Sites whose test asserts on the exact
# value use their own distinct per-test literal (not this shared constant) so
# two asserting tests can never be satisfied by the same string by accident.
# ---------------------------------------------------------------------------

_PERSISTED_ASSISTANT_MSG_ID = "test-assistant-msg-id-shared"


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
# Test 3: First turn -- creates conversation, stores sdk_session_id, returns citations
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
        patch("app.worker.tasks.runtime.agent.psycopg2.connect") as mock_connect,
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id") as mock_set_sdk,
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="test-assistant-msg-id-first-turn",
        ) as mock_persist,
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

    # PROD-05: exactly one tenant-DB connection opened per turn
    mock_connect.assert_called_once_with("postgresql://tenant", connect_timeout=5)
    mock_connect.return_value.close.assert_called_once()

    # sdk_session_id must be stored — first arg is now the shared connection (not conn_str)
    mock_set_sdk.assert_called_once_with(
        mock_connect.return_value, local_conv_id, "sdk-abc-123"
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

    # WIRE-05: message_id must equal the exact string this test's own patch
    # returned -- not a MagicMock, not a second freshly-minted identifier.
    assert response_payload["message_id"] == "test-assistant-msg-id-first-turn", (
        f"message_id must equal the value _persist_messages returned, "
        f"got: {response_payload.get('message_id')!r}"
    )

    # _persist_messages must be called
    mock_persist.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: Subsequent turn -- resume= gets the stored sdk_session_id
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
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch(
            "app.worker.tasks.runtime.agent._validate_conversation_owner",
            return_value={"id": existing_conv_id, "metadata": {"sdk_session_id": stored_sdk_session_id}},
        ),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
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
# Test 5: Escalation -- emits agent.escalated before agent.response
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

    emitted_events: list[tuple[str, dict]] = []

    def fake_emit(jid, event_type, payload, db, redis):
        emitted_events.append((event_type, payload or {}))

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="test-assistant-msg-id-escalation",
        ),
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
    event_types = [et for et, _ in emitted_events]
    assert "agent.escalated" in event_types, f"agent.escalated missing from {event_types}"
    assert "agent.response" in event_types, f"agent.response missing from {event_types}"
    escalated_idx = event_types.index("agent.escalated")
    response_idx = event_types.index("agent.response")
    assert escalated_idx < response_idx, (
        f"agent.escalated ({escalated_idx}) must come before agent.response ({response_idx})"
    )

    # WIRE-05: the terminal payload carries message_id on an escalating turn
    # too (escalation fires BEFORE, not INSTEAD OF, the terminal event) --
    # and the escalation payload itself never gains an identifier, since it
    # is not the event the widget attaches feedback to.
    escalated_payload = next(p for et, p in emitted_events if et == "agent.escalated")
    response_payload = next(p for et, p in emitted_events if et == "agent.response")
    assert response_payload.get("message_id") == "test-assistant-msg-id-escalation", (
        f"agent.response payload must carry message_id on an escalating turn too, "
        f"got: {response_payload}"
    )
    assert "message_id" not in escalated_payload, (
        f"agent.escalated payload must never carry message_id, got: {escalated_payload}"
    )


# ---------------------------------------------------------------------------
# Test 6: Missing CITATIONS block -- empty list + structlog.warning
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
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
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
# M5 -- VAL chain dispatch from run_agent_turn (Plan 05-04)
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
    """run_agent_turn dispatches the Gatekeeper->Auditor->Strategist chain after agent.response."""
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
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
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
    # chain_call_args is (gatekeeper_sig, auditor_sig, strategist_sig, retrieval_faithfulness_sig)
    # Phase 21 (OPS-07): run_retrieval_faithfulness.si(agent_id, job_id) is appended as the
    # chain's 4th/last step -- it must run strictly after run_auditor commits its verdict,
    # since the sample-rate-OR-auditor-flag gate is evaluated inside that task itself
    # (see app/worker/tasks/runtime/retrieval_eval.py's module docstring).
    assert len(chain_call_args) == 4, (
        "Expected 4 chain tasks (gatekeeper, auditor, strategist, retrieval_faithfulness), "
        f"got {len(chain_call_args)}"
    )


def test_validators_not_dispatched_on_idempotency_skip():
    """Validation chain is NOT dispatched on the idempotency-skip path."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())

    mock_db = MagicMock()
    # Idempotency row exists -- triggers early return
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
# Phase 12 -- D-10 retrieve cap + D-11 wall-clock guard regression tests
# (Plan 12-01, 2026-05-29; D-10 fix 2026-06-01)
# ---------------------------------------------------------------------------

def test_max_turns_allows_synthesis_after_retrieve():
    """D-10 fix regression: ClaudeAgentOptions must use max_turns >= 6.

    Root cause of the empty-answer bug: max_turns=3 cut the agent off after
    the retrieve tool round-trip (tool_use + tool_result = ~2 CLI turns),
    leaving no turn to compose the final text answer.  The fix raises
    max_turns to 6 so the agent can always synthesize after one retrieve call.
    The Voyage RPM guard is now enforced by the tool-level counter in
    agent_tools.retrieve_tool instead of relying on max_turns.
    """
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
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
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
    actual_max_turns = options_kwargs_captured[0].get("max_turns")
    assert actual_max_turns is not None and actual_max_turns >= 6, (
        f"D-10 fix: max_turns must be >= 6 to allow synthesis after retrieve, "
        f"got max_turns={actual_max_turns}. "
        f"max_turns=3 caused empty response (bug: empty-answer-on-retrieve)."
    )


def test_wall_clock_guard_is_ninety_seconds():
    """D-11 regression: asyncio.wait_for must be called with timeout=90."""

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
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
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


# ---------------------------------------------------------------------------
# Phase 12 -- D-10 fix phase 2: budget config + ResultMessage instrumentation
# (Debug session: empty-answer-on-retrieve, re-opened 2026-06-01)
# ---------------------------------------------------------------------------

def test_max_budget_uses_settings_not_hardcoded():
    """D-10 fix phase 2 regression: ClaudeAgentOptions must use settings.AGENT_MAX_BUDGET_USD.

    Root cause (additional to max_turns): max_budget_usd=0.05 was too low for a
    turn using extended thinking + retrieved context + synthesis on Sonnet.
    When the budget is exceeded, the CLI emits result{subtype:error_max_budget,
    is_error:true} and receive_response() terminates with response_text="".
    The fix raises the default to 0.50 and makes it env-configurable via
    settings.AGENT_MAX_BUDGET_USD so it can be tuned without code changes.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    local_conv_id = "00000000-0000-0000-0000-000000000022"

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
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys prompt"),
        patch("app.worker.tasks.runtime.agent.ClaudeAgentOptions", side_effect=FakeClaudeAgentOptions),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_CITATION),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="Who is Bantuson?",
            conversation_id=None,
        )

    assert len(options_kwargs_captured) == 1, (
        "ClaudeAgentOptions must be instantiated exactly once per turn"
    )
    actual_budget = options_kwargs_captured[0].get("max_budget_usd")
    # Must not be the old hardcoded 0.05 value
    assert actual_budget is not None and actual_budget > 0.05, (
        f"D-10 fix phase 2: max_budget_usd must be > 0.05 (old hardcoded value was too low "
        f"for thinking+retrieve+synthesis), got max_budget_usd={actual_budget}"
    )
    # The default from Settings is 0.50
    assert actual_budget >= 0.50, (
        f"D-10 fix phase 2: default max_budget_usd must be >= 0.50, got {actual_budget}. "
        f"The 0.05 cap was exhausted by extended-thinking + retrieve + synthesis on Sonnet."
    )


def test_result_message_stop_reason_logged():
    """D-10 fix phase 2: _run_sdk_turn must log ResultMessage diagnostic fields.

    The ResultMessage subtype/is_error/num_turns/total_cost_usd fields are the
    ONLY reliable disambiguator when response_text is empty (error_max_turns,
    error_max_budget, and error_during_execution all produce the same empty-text
    signature with no exception). This test verifies the info and warning log
    lines are emitted when the SDK returns an error ResultMessage.

    Strategy: call _run_sdk_turn directly with a fake async SDK client that yields
    only a fake ResultMessage (with is_error=True / subtype=error_max_budget).
    The isinstance() check in _run_sdk_turn uses the patched ResultMessage class.
    """
    import asyncio as _asyncio
    from unittest.mock import AsyncMock

    # Import the private helper directly (module-level async function)
    from app.worker.tasks.runtime.agent import _run_sdk_turn

    # Minimal fake ResultMessage with is_error=True (budget-exceeded scenario)
    class _FakeResultMessage:
        session_id = "sess-budget-test"
        subtype = "error_max_budget"
        is_error = True
        num_turns = 3
        total_cost_usd = 0.062
        stop_reason = None
        api_error_status = None

    fake_rm = _FakeResultMessage()

    # Fake async SDK client
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.query = AsyncMock()

    async def _fake_receive():
        yield fake_rm

    fake_client.receive_response = _fake_receive

    log_calls_info: list[dict] = []
    log_calls_warn: list[dict] = []

    def _capture_info(event, **kwargs):
        log_calls_info.append({"event": event, **kwargs})

    def _capture_warn(event, **kwargs):
        log_calls_warn.append({"event": event, **kwargs})

    # Dummy types so that isinstance() calls in _run_sdk_turn do not raise
    # TypeError: isinstance() arg 2 must be a type.
    # The module-level AssistantMessage/ToolUseBlock/ToolResultBlock are MagicMock
    # instances (from the fake SDK stub), which are not valid isinstance targets.
    # We patch them to trivial classes that the fake ResultMessage instance won't match.
    class _DummyAssistantMessage:
        pass

    class _DummyToolUseBlock:
        pass

    class _DummyToolResultBlock:
        pass

    with (
        patch("app.worker.tasks.runtime.agent.ClaudeSDKClient", return_value=fake_client),
        # Patch ResultMessage to the fake class so isinstance() resolves correctly
        patch("app.worker.tasks.runtime.agent.ResultMessage", _FakeResultMessage),
        # Patch these to proper types so isinstance() does not raise TypeError
        patch("app.worker.tasks.runtime.agent.AssistantMessage", _DummyAssistantMessage),
        patch("app.worker.tasks.runtime.agent.ToolUseBlock", _DummyToolUseBlock),
        patch("app.worker.tasks.runtime.agent.ToolResultBlock", _DummyToolResultBlock),
        patch("app.worker.tasks.runtime.agent.log") as mock_log,
    ):
        mock_log.info.side_effect = _capture_info
        mock_log.warning.side_effect = _capture_warn

        _asyncio.run(
            _run_sdk_turn(
                message="test",
                options=MagicMock(),
                job_id="job-diag-001",
                local_conversation_id="conv-diag-001",
                conn_str="postgresql://fake",
                db=MagicMock(),
                redis=MagicMock(),
            )
        )

    # _run_sdk_turn.result info log must have been emitted with stop-reason fields
    result_logs = [c for c in log_calls_info if c.get("event") == "_run_sdk_turn.result"]
    assert len(result_logs) >= 1, (
        f"Expected _run_sdk_turn.result log line -- not found. log_calls_info={log_calls_info}"
    )
    rl = result_logs[0]
    assert rl.get("subtype") == "error_max_budget", (
        f"subtype must be logged; expected error_max_budget, got: {rl}"
    )
    assert rl.get("is_error") is True, f"is_error must be logged: {rl}"
    assert rl.get("num_turns") == 3, f"num_turns must be logged: {rl}"
    assert rl.get("total_cost_usd") == 0.062, f"total_cost_usd must be logged: {rl}"
    assert "response_length" in rl, f"response_length must be logged: {rl}"

    # _run_sdk_turn.sdk_error warning must be emitted on is_error=True path
    error_logs = [c for c in log_calls_warn if c.get("event") == "_run_sdk_turn.sdk_error"]
    assert len(error_logs) >= 1, (
        f"Expected _run_sdk_turn.sdk_error warning for is_error=True. "
        f"log_calls_warn={log_calls_warn}"
    )


# ---------------------------------------------------------------------------
# Phase 23 (23-01) -- WIRE-05 Gap A: agent.response carries the assistant
# message id, proven by name so removing the emit field or returning the
# wrong local turns THIS test red rather than merely changing a value.
# ---------------------------------------------------------------------------

_TERMINAL_RESPONSE_MSG_ID = "test-assistant-msg-id-terminal-response"


def test_agent_response_carries_assistant_message_id():
    """The terminal agent.response payload's message_id is the exact string
    _persist_messages returned for this turn -- not a MagicMock (a bare patch
    site regression, T-23-GA-03) and not a second, independently-minted id
    (T-23-GA-04)."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    local_conv_id = "00000000-0000-0000-0000-000000000030"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, job]

    response_payloads: list[dict] = []

    def fake_emit(jid, event_type, payload, db, redis):
        if event_type == "agent.response":
            response_payloads.append(payload or {})

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_TERMINAL_RESPONSE_MSG_ID,
        ),
        patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
        patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys prompt"),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_CITATION),
        patch("app.worker.tasks.runtime.agent.emit", side_effect=fake_emit),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
        )

    assert len(response_payloads) == 1, (
        f"Expected exactly 1 agent.response event, got: {response_payloads}"
    )
    payload = response_payloads[0]

    # 1. the key is present
    assert "message_id" in payload, f"agent.response payload missing message_id: {payload}"
    # 2. its value is the exact string the patch returned
    assert payload["message_id"] == _TERMINAL_RESPONSE_MSG_ID, (
        f"message_id must equal the value _persist_messages returned, "
        f"got: {payload['message_id']!r}"
    )
    # 3. its value is a string rather than an object -- the assertion that makes
    #    a bare-patch regression (a MagicMock silently flowing through) impossible
    #    to reintroduce without this test catching it.
    assert isinstance(payload["message_id"], str), (
        f"message_id must be a string, not {type(payload['message_id'])}; "
        f"a non-str here means a bare patch site is handing a MagicMock to the emit."
    )
