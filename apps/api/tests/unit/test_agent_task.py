"""
Unit tests for the run_agent_turn Celery task (Plan 04-03).

Tests validate:
  - Idempotency: returns {"status": "already_complete"} if agent.response event exists
  - Agent-not-found guard: returns {} without raising when agent_id is unknown
  - First turn: creates the conversation row and emits agent.response with parsed
    citations
  - Subsequent turn: reads the conversation's history and hands it to the loop
  - Escalation: emits agent.escalated event before agent.response
  - Missing CITATIONS block: citations==[] and structlog.warning called
  - _read_turn_history: order, tiebreak and cap

Mock strategy: patch asyncio.run at 'app.worker.tasks.runtime.agent.asyncio.run'
with a canned dict return value. Do NOT use AsyncMock for the turn -- the task
uses asyncio.run() as the sync/async bridge; we mock that boundary only.
build_agent_turn is patched too: the real seam builds a provider client and a
live tool server, and this file is about the task body around them.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


TENANT_DSN = "postgresql://tenant"


def _seam(**kwargs):
    """Stand-in for `build_agent_turn`, and the one boundary these tests replace.

    The real seam builds a provider client and a live tool server bound to the
    tenant connection string; neither belongs in a test about the task body. Two
    fields the task reads off the turn are here: `calls`, the rows the loop
    accumulates, and `ledger`, where the task sends them once the turn is over.
    An empty `calls` prices the turn at unknown, never at zero.
    """
    return SimpleNamespace(calls=[], ledger=kwargs.get("ledger", lambda call: None))


def _a_model_call():
    """One finished row, the shape the ledger is handed."""
    from datetime import datetime, timezone

    from app.domain.model_call import ModelCall, ModelSource

    return ModelCall(
        purpose="agent_turn",
        provider="openai",
        requested_model="gpt-5.6-luna",
        served_model="gpt-5.6-luna",
        model_source=ModelSource.REPORTED,
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
        tenant_id="11111111-1111-1111-1111-111111111111",
    )


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
}

# Canned result with no CITATIONS block
_CANNED_RESULT_NO_CITATIONS = {
    "response_text": "Some answer text with no citations block.",
    "tool_calls_log": [],
    "escalated": False,
    "escalation_reason": None,
    "escalation_context": None,
}

# Canned result with escalation
_CANNED_RESULT_ESCALATED = {
    "response_text": "I'm connecting you to a human agent.\n\nCITATIONS:\n- Document: N/A | Section: N/A\n",
    "tool_calls_log": [],
    "escalated": True,
    "escalation_reason": "Customer expressed frustration",
    "escalation_context": "Customer waiting 3 weeks for order",
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
# Test 3: First turn -- creates the conversation, reads no history, returns citations
# ---------------------------------------------------------------------------

def test_the_chat_path_hands_the_seam_a_ledger_bound_to_the_tenant_db():
    """The turn a customer waits on writes its `model_calls` rows to ITS tenant.

    `callable(ledger)` proves nothing: `lambda call: None` satisfies it, spends
    the tenant's money and leaves no row, which is the failure #46 ended. So a
    real `ModelCall` is driven through the ledger the task handed the seam, and
    the assertion is where the row lands, in `record_model_call` with the decrypted
    tenant dsn, opened per row so a turn that dies mid-loop keeps what it paid for.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    job = _make_job(job_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    captured: dict = {}

    def _capturing_seam(**kwargs):
        captured.update(kwargs)
        return _seam(**kwargs)

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value=TENANT_DSN),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch(
            "app.worker.tasks.runtime.agent._create_conversation_row",
            return_value="00000000-0000-0000-0000-0000000000ab",
        ),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_capturing_seam),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_CITATION),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
        )

    ledger = captured.get("ledger")
    assert ledger is not None, "the chat path built a turn with no ledger at all"

    call = _a_model_call()
    with patch("app.core.model_client.record_model_call") as write:
        ledger(call)

    write.assert_called_once()
    assert write.call_args.args[0] is call
    assert write.call_args.args[1] == TENANT_DSN, (
        "the chat path's ledger wrote this turn's model_calls row to "
        f"{write.call_args.args[1]!r} rather than to the tenant database the "
        "turn was served from"
    )


def test_first_turn_creates_conversation_and_reads_no_history():
    """First turn (conversation_id=None) creates a conversation row and starts empty."""
    from app.core.config import settings
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
        patch("app.worker.tasks.runtime.agent._read_turn_history") as mock_history,
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="test-assistant-msg-id-first-turn",
        ) as mock_persist,
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_CITATION),
        patch("app.worker.tasks.runtime.agent.emit", side_effect=fake_emit),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="What is the return policy?",
            conversation_id=None,
        )

    # PROD-05: exactly one tenant-DB connection opened per turn
    mock_connect.assert_called_once_with(
        "postgresql://tenant", connect_timeout=settings.TENANT_DB_CONNECT_TIMEOUT_S
    )
    mock_connect.return_value.close.assert_called_once()

    # A first turn has no history to resume from, and ADR 0008 makes that a
    # QUERY rather than an absent session file, so the honest assertion is that
    # the query never runs, not that it came back empty.
    mock_history.assert_not_called()

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
# Test 4: Subsequent turn -- the conversation's history reaches the loop
# ---------------------------------------------------------------------------

def test_subsequent_turn_hands_the_stored_history_to_the_loop():
    """A follow-up turn resumes from `messages`, and the rows have to arrive.

    This replaces the `resume=` pin. ADR 0008 dropped the SDK's session files,
    which Railway wipes on every deploy, and put session
    state in `conversations` and `messages` instead. The property is unchanged:
    a follow-up turn must be given what was said before, or the agent answers
    every message as if it were the first. The mechanism moved, so the pin moved
    with it: the rows `_read_turn_history` returns are the rows `run_agent_loop`
    is handed, by identity.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    existing_conv_id = str(uuid.uuid4())
    stored_history = [
        {"role": "user", "content": "Do you deliver to Soweto?"},
        {"role": "assistant", "content": "Yes, within two working days."},
    ]

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    captured: dict = {}

    async def fake_loop(*args, **kwargs):
        captured.update(kwargs)
        return _CANNED_RESULT_WITH_CITATION

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect") as mock_connect,
        patch(
            "app.worker.tasks.runtime.agent._validate_conversation_owner",
            return_value={"id": existing_conv_id, "metadata": {}},
        ),
        patch(
            "app.worker.tasks.runtime.agent._read_turn_history",
            return_value=stored_history,
        ) as mock_history,
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
        patch("app.worker.tasks.runtime.agent.run_agent_loop", side_effect=fake_loop),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="Follow-up question",
            conversation_id=existing_conv_id,
        )

    mock_history.assert_called_once_with(mock_connect.return_value, existing_conv_id)
    assert captured.get("history") == stored_history, (
        "the loop was not handed the conversation's history; it got "
        f"{captured.get('history')!r}. Without it every follow-up turn answers "
        "as though it were the first message of the conversation."
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
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value="test-assistant-msg-id-escalation",
        ),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
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
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
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

    assert len(response_payloads) == 1, "Expected 1 agent.response event"
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
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_RETRIEVE),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.celery_chain", mock_celery_chain),
    ):
        run_agent_turn.run(
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
# Phase 12 -- D-11's wall-clock guard, and D-10's empty-answer diagnosis
# (Plan 12-01, 2026-05-29; D-10 fix 2026-06-01; carried across ADR 0008)
#
# D-10's two ceilings moved out of this file. `max_turns` and `max_budget_usd`
# were arguments to a constructor this module no longer calls; they are
# MAX_MODEL_CALLS_PER_TURN and AgentTurn.max_budget_usd now, pinned where they
# are assembled (test_agent_options_seam.py) and where they bite
# (test_agent_loop.py). What stays here belongs to the TASK: the wall-clock
# ceiling it wraps the turn in, and the record it writes when the answer comes
# back empty.
# ---------------------------------------------------------------------------

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
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
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




def test_an_empty_answer_still_records_why_the_turn_stopped():
    """D-10's diagnosis, moved to where it now lives: the turn_metrics row.

    The original defect was an empty `response_text` with no way to tell WHY.
    `max_turns=3` cut the agent off after the retrieve round trip, the budget
    ceiling produced the same empty text, and so did an error mid-turn: three
    causes, one signature, nothing recorded. The SDK's ResultMessage was the
    disambiguator and it was logged, which meant the answer lived in whatever
    log retention the host happened to have.

    `run_agent_loop` returns `stop_reason` instead, and this task writes it to
    `turn_metrics`. A row is durable and joinable; a log line is neither. So the
    assertion is on the column: an empty answer that stopped at the call ceiling
    must be distinguishable, later, from an empty answer that stopped anywhere
    else.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)
    local_conv_id = "00000000-0000-0000-0000-000000000031"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    exhausted = {
        "response_text": "",
        "tool_calls_log": [],
        "escalated": False,
        "escalation_reason": None,
        "escalation_context": None,
        "num_turns": 6,
        "stop_reason": "max_model_calls",
    }

    written: list[dict] = []

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
        patch(
            "app.worker.tasks.runtime.agent._write_turn_metrics",
            side_effect=lambda _conn, **kw: written.append(kw),
        ),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=exhausted),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="Something the agent could not finish answering",
            conversation_id=None,
        )

    assert len(written) == 1, f"expected one turn_metrics write, got {written}"
    row = written[0]
    assert row["stop_reason"] == "max_model_calls", (
        "an empty answer was recorded with stop_reason "
        f"{row['stop_reason']!r}. All three ways a turn ends early produce the "
        "same empty text, so a row that does not name the ceiling leaves the "
        "next reader with D-10 exactly as it was."
    )
    assert row["num_turns"] == 6, (
        f"num_turns was {row['num_turns']!r}. It is the other half of the "
        "diagnosis: 'stopped at the ceiling' means nothing without the count."
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
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_TERMINAL_RESPONSE_MSG_ID,
        ),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
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


# ---------------------------------------------------------------------------
# Neon cold start (observed on three live jobs, 2026-08-16).
#
# Per-tenant Neon projects scale to zero, so the tenant-DB connect in
# run_agent_turn is where the FIRST message after roughly five idle minutes
# lands, against an endpoint that needs 8-20s to wake. Three properties, one
# defect each:
#
#   1. the connect budget is the configured one. A 5s literal is shorter than
#      the wake, and psycopg2 spends it on every resolved address in turn.
#   2. an OperationalError at that connect is retried. The connect used to sit
#      OUTSIDE the task's own try, so the error escaped run_agent_turn with no
#      retry and no event at all — the widget saw nothing and the job died.
#   3. a terminal failure names what killed the turn BY TYPE. str() of a
#      TimeoutError and of a bare OperationalError are both empty, so
#      {"error": str(exc)} on its own is a payload that names nothing
#      (BACKLOG 1.30).
# ---------------------------------------------------------------------------


def test_tenant_connect_uses_the_configured_timeout():
    """The connect budget comes from settings, and its default clears the wake window."""
    from app.core.config import Settings, settings
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect") as mock_connect,
        patch(
            "app.worker.tasks.runtime.agent._create_conversation_row",
            return_value="00000000-0000-0000-0000-000000000040",
        ),
        patch(
            "app.worker.tasks.runtime.agent._persist_messages",
            return_value=_PERSISTED_ASSISTANT_MSG_ID,
        ),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_CITATION),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="First message after an idle period",
            conversation_id=None,
        )

    assert mock_connect.call_args.kwargs.get("connect_timeout") == (
        settings.TENANT_DB_CONNECT_TIMEOUT_S
    ), (
        f"the tenant connect must take its budget from "
        f"settings.TENANT_DB_CONNECT_TIMEOUT_S, got: {mock_connect.call_args}"
    )

    # The kwarg pin above is satisfied by any value the setting happens to hold,
    # so the setting's own DEFAULT is pinned separately: 5s is the value that
    # produced the observed failure, and anything below the wake window
    # reintroduces it.
    assert Settings.model_fields["TENANT_DB_CONNECT_TIMEOUT_S"].default >= 20, (
        "TENANT_DB_CONNECT_TIMEOUT_S must clear a Neon endpoint's 8-20s wake; "
        "at 5s every first-message-after-idle turn timed out."
    )


def test_operational_error_at_tenant_connect_is_retried_on_the_wake_window():
    """A cold tenant endpoint retries; it does not escape the task uncaught."""
    import psycopg2
    import pytest
    from celery.exceptions import Retry

    from app.worker.tasks.runtime.agent import TENANT_WAKE_RETRY_COUNTDOWN_S, run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    connect_error = psycopg2.OperationalError("connection timeout expired")
    mock_retry = MagicMock(return_value=Retry())

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect", side_effect=connect_error),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch.object(run_agent_turn, "retry", mock_retry),
    ):
        with pytest.raises(Retry):
            run_agent_turn.run(
                job_id=job_id,
                agent_id=agent_id,
                message="First message after an idle period",
                conversation_id=None,
            )

    mock_retry.assert_called_once()
    assert mock_retry.call_args.kwargs["exc"] is connect_error, (
        f"the retry must carry the connect failure itself, got: {mock_retry.call_args}"
    )
    assert mock_retry.call_args.kwargs["countdown"] == TENANT_WAKE_RETRY_COUNTDOWN_S, (
        f"a wake-triggered retry must wait the wake window "
        f"({TENANT_WAKE_RETRY_COUNTDOWN_S}s), not the generic exponential "
        f"countdown, which fires while the endpoint is still suspended. "
        f"Got: {mock_retry.call_args}"
    )


def test_terminal_failure_emits_agent_failed_carrying_the_error_type():
    """Retries exhausted: agent.failed is emitted and it names the exception type."""
    import psycopg2

    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    # Three reads: the agent, the job, and the job again on the failure path.
    mock_db.get.side_effect = [agent, job, job]

    # Deliberately message-less: str() of this is "", which is the shape that
    # made the old {"error": str(exc)} payload say nothing at all.
    connect_error = psycopg2.OperationalError()
    assert str(connect_error) == "", "the fixture must be the empty-str() case"

    emitted_events: list[tuple[str, dict]] = []

    def fake_emit(jid, event_type, payload, db, redis):
        emitted_events.append((event_type, payload or {}))

    run_agent_turn.push_request(retries=run_agent_turn.max_retries)
    try:
        with (
            patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
            patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
            patch("app.worker.tasks.runtime.agent.psycopg2.connect", side_effect=connect_error),
            patch("app.worker.tasks.runtime.agent.emit", side_effect=fake_emit),
        ):
            run_agent_turn.run(
                job_id=job_id,
                agent_id=agent_id,
                message="First message after an idle period",
                conversation_id=None,
            )
    finally:
        run_agent_turn.pop_request()

    failed = [payload for event_type, payload in emitted_events if event_type == "agent.failed"]
    assert len(failed) == 1, (
        f"a turn that died at the tenant connect must still emit agent.failed — "
        f"the observed defect was ZERO events reaching the widget. "
        f"Got: {emitted_events}"
    )

    payload = failed[0]
    assert payload.get("error_type") == "OperationalError", (
        f"agent.failed must name the exception type; got: {payload}"
    )
    assert payload.get("error"), (
        f"agent.failed's error must be non-empty even when str(exc) is '' "
        f"(BACKLOG 1.30 — repr is the fallback); got: {payload}"
    )

    assert job.status == "failed", "the job row must be marked failed on the terminal path"


def test_terminal_failure_emits_agent_failed_when_the_job_row_is_unreadable():
    """The emission is not conditional on the job-status bookkeeping succeeding.

    Sibling of the test above with ONE difference: the failure path's read of
    the job row comes back None. The turn still died and the widget is still
    waiting, so agent.failed is still owed. This is the case that goes red if
    the emission is ever moved back under the `if job2:` that guards the status
    write — the shape the emission had before, and the second silent-death path
    beside the connect that started all this.
    """
    import psycopg2

    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    # Third read — the one on the failure path — finds nothing.
    mock_db.get.side_effect = [agent, job, None]

    emitted_events: list[tuple[str, dict]] = []

    def fake_emit(jid, event_type, payload, db, redis):
        emitted_events.append((event_type, payload or {}))

    run_agent_turn.push_request(retries=run_agent_turn.max_retries)
    try:
        with (
            patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
            patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
            patch(
                "app.worker.tasks.runtime.agent.psycopg2.connect",
                side_effect=psycopg2.OperationalError(),
            ),
            patch("app.worker.tasks.runtime.agent.emit", side_effect=fake_emit),
        ):
            run_agent_turn.run(
                job_id=job_id,
                agent_id=agent_id,
                message="First message after an idle period",
                conversation_id=None,
            )
    finally:
        run_agent_turn.pop_request()

    failed = [payload for event_type, payload in emitted_events if event_type == "agent.failed"]
    assert len(failed) == 1, (
        f"agent.failed must be emitted even when the job row cannot be read on "
        f"the failure path — the customer's only signal may not be conditional "
        f"on bookkeeping. Got: {emitted_events}"
    )
    assert failed[0].get("error_type") == "OperationalError", (
        f"agent.failed must still name the exception type; got: {failed[0]}"
    )


def test_terminal_failure_emits_agent_failed_when_the_job_status_write_raises():
    """A failing job-status commit cannot take the customer's signal down with it.

    The status write and the emission used to share one try/except-pass, so a
    raise from get_sync_db(), from the job read, or from this commit swallowed
    agent.failed along with the bookkeeping. They are separate boundaries now,
    and the emission runs first.
    """
    import psycopg2

    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(str(agent_id))
    job = _make_job(job_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job, job]
    # The job-status commit on the failure path is the thing that breaks.
    mock_db.commit.side_effect = RuntimeError("control DB went away")

    emitted_events: list[tuple[str, dict]] = []

    def fake_emit(jid, event_type, payload, db, redis):
        emitted_events.append((event_type, payload or {}))

    run_agent_turn.push_request(retries=run_agent_turn.max_retries)
    try:
        with (
            patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
            patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
            patch(
                "app.worker.tasks.runtime.agent.psycopg2.connect",
                side_effect=psycopg2.OperationalError(),
            ),
            patch("app.worker.tasks.runtime.agent.emit", side_effect=fake_emit),
        ):
            run_agent_turn.run(
                job_id=job_id,
                agent_id=agent_id,
                message="First message after an idle period",
                conversation_id=None,
            )
    finally:
        run_agent_turn.pop_request()

    failed = [payload for event_type, payload in emitted_events if event_type == "agent.failed"]
    assert len(failed) == 1, (
        f"a failing job-status write must not suppress agent.failed — the two "
        f"need separate failure boundaries, with the emission first. "
        f"Got: {emitted_events}"
    )


# ---------------------------------------------------------------------------
# _read_turn_history, the session state ADR 0008 put in the database
#
# The SDK's `resume` stored session files on the container filesystem and
# Railway replaces that filesystem on every deploy, so a follow-up turn now
# reads what was said from `messages`. Three properties, one defect each:
#
#   1. within one timestamp the question precedes the answer. `_persist_messages`
#      writes both rows in ONE transaction, so both carry the same
#      transaction_timestamp() and created_at alone leaves their order to the
#      plan (issue #79).
#   2. the cap takes the NEWEST rows, not an arbitrary forty.
#   3. what comes back is oldest first, because that is the order a message list
#      is read in.
#
# The fake cursor below reads the ORDER BY out of the SQL it is handed rather
# than assuming one, so deleting a clause from the query changes what these
# tests see. A fixture that sorted by its own rule would be describing a
# contract the query had abandoned.
# ---------------------------------------------------------------------------


class _OrderedCursor:
    """A cursor that honours the ORDER BY in the SQL it executes.

    `rows` arrive in HEAP order, which is what a sequential scan returns when nothing
    orders it. Insertion order is deliberately the opposite of the tiebreak's,
    so a query that has lost the tiebreak falls back to heap order and the
    ordering test sees it.
    """

    def __init__(self, rows):
        self._rows = list(rows)
        self._result: list = []
        self.last_params = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params):
        self.last_params = params
        conv_id, limit = params
        selected = [
            row for row in self._rows
            if row["conversation_id"] == conv_id and row["role"] in ("user", "assistant")
        ]
        # Python's sort is stable, so a key that omits a clause leaves the rows
        # in heap order for the rows that clause would have separated, which is
        # exactly what PostgreSQL does with an unordered scan.
        if "CASE role WHEN 'assistant' THEN 0 ELSE 1 END" in sql:
            selected.sort(key=lambda r: (-r["created_at"], 0 if r["role"] == "assistant" else 1))
        elif "created_at DESC" in sql:
            selected.sort(key=lambda r: -r["created_at"])
        self._result = [(r["role"], r["content"]) for r in selected[:limit]]

    def fetchall(self):
        return self._result


class _OrderedConn:
    def __init__(self, rows):
        self.cursor_obj = _OrderedCursor(rows)

    def cursor(self):
        return self.cursor_obj


CONV = "conv-history-0001"


def _row(role, content, created_at, conv=CONV):
    return {
        "conversation_id": conv,
        "role": role,
        "content": content,
        "created_at": created_at,
    }


def test_the_history_puts_the_question_before_the_answer_within_one_timestamp():
    """Issue #79. Both rows of a turn share one transaction_timestamp().

    The heap holds the USER row first, which is what a scan can return and what
    `created_at DESC` alone would therefore preserve. The CASE is what pulls the
    assistant row to the front of the DESC scan so the reversal puts the
    question first. Without it the model reads its own answer before the
    question it answered.
    """
    from app.worker.tasks.runtime.agent import _read_turn_history

    conn = _OrderedConn([
        _row("user", "Do you deliver to Soweto?", 100),
        _row("assistant", "Yes, within two working days.", 100),
    ])

    history = _read_turn_history(conn, CONV)

    assert history == [
        {"role": "user", "content": "Do you deliver to Soweto?"},
        {"role": "assistant", "content": "Yes, within two working days."},
    ], (
        f"the turn came back as {history}. Its user row and its assistant row "
        "share one transaction_timestamp(), so created_at alone cannot order "
        "them and the CASE tiebreak is what settles it (issue #79). Reversed, "
        "the model reads the answer before the question."
    )


def test_the_history_comes_back_oldest_first():
    """A message list is read forwards; the query reads backwards to reach the cap."""
    from app.worker.tasks.runtime.agent import _read_turn_history

    conn = _OrderedConn([
        _row("user", "first question", 100),
        _row("assistant", "first answer", 100),
        _row("user", "second question", 200),
        _row("assistant", "second answer", 200),
    ])

    history = _read_turn_history(conn, CONV)

    assert [h["content"] for h in history] == [
        "first question",
        "first answer",
        "second question",
        "second answer",
    ], f"the conversation came back out of order: {history}"


def test_the_history_cap_takes_the_newest_rows():
    """TURN_HISTORY_MAX_MESSAGES bounds the read, and it bounds it from the END.

    Every history row travels on every model call of the turn, so an uncapped
    read makes the hundredth turn of a conversation cost more than the first
    hundred together. Taking the newest rows is the half that matters: a cap
    that kept the OLDEST forty would leave the agent answering the current
    question from a conversation that stopped twenty exchanges ago.
    """
    from app.worker.tasks.runtime.agent import (
        TURN_HISTORY_MAX_MESSAGES,
        _read_turn_history,
    )

    total = TURN_HISTORY_MAX_MESSAGES + 6
    conn = _OrderedConn([
        _row("user" if i % 2 == 0 else "assistant", f"message {i}", i)
        for i in range(total)
    ])

    history = _read_turn_history(conn, CONV)

    assert conn.cursor_obj.last_params[1] == TURN_HISTORY_MAX_MESSAGES, (
        "the query did not carry the cap as its LIMIT; it carried "
        f"{conn.cursor_obj.last_params[1]!r}"
    )
    assert len(history) == TURN_HISTORY_MAX_MESSAGES
    assert history[0]["content"] == f"message {total - TURN_HISTORY_MAX_MESSAGES}", (
        f"the cap kept the wrong end of the conversation; it starts at "
        f"{history[0]['content']!r}. Forty rows is twenty exchanges, and they "
        "have to be the twenty that just happened."
    )
    assert history[-1]["content"] == f"message {total - 1}"


def test_the_history_ignores_rows_from_another_conversation():
    """The scoping clause, which nothing else here would notice losing."""
    from app.worker.tasks.runtime.agent import _read_turn_history

    conn = _OrderedConn([
        _row("user", "ours", 100),
        _row("user", "somebody else's", 100, conv="conv-history-0002"),
    ])

    history = _read_turn_history(conn, CONV)

    assert [h["content"] for h in history] == ["ours"], (
        f"another conversation's messages reached this turn: {history}"
    )
