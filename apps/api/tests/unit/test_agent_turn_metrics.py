"""
Unit tests for OPS-01: turn_metrics write path in run_agent_turn.

Behavior under test:
  (a) A successful turn inserts one turn_metrics row with num_turns and
      stop_reason from the loop's result dict, cost_usd PRICED FROM THE TURN'S
      OWN `model_calls` ROWS, latency_ms from the wall-clock around asyncio.run,
      escalated from the turn result, and tool_count from len(tool_calls_log).
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
    the turn, same convention as test_agent_task.py).
  - patch build_agent_turn with `_seam_with`, which returns a SimpleNamespace
    carrying two fields: `calls`, the ledger rows the test declares, and
    `ledger`, where the task sends them once the turn is over. It is not an
    AgentTurn. The real seam builds a provider client and a live tool server
    bound to the tenant connection string, and neither belongs in a test about
    one metrics row; what this file needs from the turn is the rows the cost is
    derived from, so those are what the double carries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.domain.model_call import ModelCall, ModelSource
from app.domain.pricing import cost_usd
from tests.agent_loop_doubles import canned_turn_result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


#: The ledger rows this turn is to be priced from.
#:
#: cost_usd is DERIVED, not reported. The SDK's `total_cost_usd` went with the
#: harness (ADR 0008) because it priced calls from a book nobody here controls,
#: so the turn's cost is now `sum(cost_usd(call))` over the `model_calls` rows
#: the recorder teed into `AgentTurn.calls`. Declaring the rows here is what
#: lets this file name the number the row must carry.
_LEDGER_CALLS = [
    ModelCall(
        purpose="agent_turn",
        provider="openai",
        requested_model="gpt-5.6-luna",
        served_model="gpt-5.6-luna",
        model_source=ModelSource.REPORTED,
        input_tokens=4000,
        output_tokens=1500,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
        tenant_id="11111111-1111-1111-1111-111111111111",
    ),
    ModelCall(
        purpose="agent_turn",
        provider="openai",
        requested_model="gpt-5.6-luna",
        served_model="gpt-5.6-luna",
        model_source=ModelSource.REPORTED,
        input_tokens=6000,
        output_tokens=800,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
        tenant_id="11111111-1111-1111-1111-111111111111",
    ),
]

#: What the price book says those two calls cost, together.
EXPECTED_TURN_COST = float(sum(cost_usd(call)[0] for call in _LEDGER_CALLS))


#: A call the price book cannot read. `cost_usd` raises UnknownPrice on it, which
#: degrades the turn's cost to unknown rather than to a number.
_UNPRICED_CALL = ModelCall(
    purpose="agent_turn",
    provider="openai",
    requested_model="gpt-5.6-nobody-priced",
    served_model="gpt-5.6-nobody-priced",
    model_source=ModelSource.REPORTED,
    input_tokens=4000,
    output_tokens=1500,
    cache_read_tokens=0,
    cache_creation_tokens=0,
    at=datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc),
    tenant_id="11111111-1111-1111-1111-111111111111",
)


def _seam_with(calls):
    """Stand-in for `build_agent_turn`, carrying the ledger rows a test declares.

    The real seam builds a provider client and a live tool server bound to the
    tenant connection string; neither belongs in a test about the metrics row.
    `calls` is what the task prices the turn from and `ledger` is where the task
    sends those rows afterwards, so both are here.
    """
    return lambda **_kwargs: SimpleNamespace(
        calls=list(calls), ledger=lambda call: None, bound=()
    )


def _seam(**kwargs):
    """The two priced rows of `_LEDGER_CALLS`."""
    return _seam_with(_LEDGER_CALLS)(**kwargs)


def _empty_seam(**kwargs):
    """A turn with NO ledger row. Its cost is unknown, and unknown is not zero."""
    return _seam_with([])(**kwargs)


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


# Canned loop result. `num_turns` and `stop_reason` ride in the dict; the cost
# does NOT, and that absence is the point of ADR 0008 (see _LEDGER_CALLS above).
_CANNED_RESULT_WITH_METRICS = canned_turn_result(
    "You can return items within 14 days.\n\n"
    "CITATIONS:\n"
    "- Document: FAQ.pdf | Section: 1\n",
    tool_calls_log=[
        {"tool_name": "retrieve", "input": {"query": "returns"}, "result": "..."},
    ],
    num_turns=3,
    stop_reason="stop",
)


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
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
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
    assert params[4] == EXPECTED_TURN_COST, (
        "the recorded cost is not what the price book says this turn's own "
        f"model_calls rows cost (expected {EXPECTED_TURN_COST}, got {params[4]}). "
        "A cost that does not move with the ledger is a number about something "
        "else, and it is what the provider-reported total_cost_usd was."
    )
    assert params[4] > 0, (
        "the turn priced at zero while carrying two ledger rows. Zero reports "
        "the turn as free, which is the claim #46 exists to stop making."
    )
    assert params[5] == 3               # num_turns from the loop result
    assert isinstance(params[6], int)   # latency_ms — wall-clock int
    assert params[6] >= 0
    assert params[7] is False           # escalated
    assert params[8] == 1               # tool_count == len(tool_calls_log)
    assert params[9] == "stop"          # stop_reason, the provider's finish_reason

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
        patch("app.worker.tasks.runtime.agent.psycopg2.connect"),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch(
            "app.worker.tasks.runtime.agent._write_turn_metrics",
            side_effect=RuntimeError("tenant DB unreachable"),
        ),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_empty_seam),
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


# ---------------------------------------------------------------------------
# (d) A cost that is UNKNOWN is written as NULL, never as zero
# ---------------------------------------------------------------------------

def _run_turn_and_read_the_cost(seam):
    """Drive one turn through `seam` and return (cost param, warning event names).

    The real INSERT is executed against a mocked cursor, so the value asserted is
    the one that reaches the `turn_metrics` row rather than a return value nobody
    writes.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
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
            return_value="aabbccdd-0000-0000-0000-000000000012",
        ),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=seam),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_METRICS),
        patch("app.worker.tasks.runtime.agent.emit"),
        patch("app.worker.tasks.runtime.agent.log") as mock_log,
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=str(agent.id),
            message="How do returns work?",
            conversation_id=None,
        )

    call = _find_turn_metrics_execute_call(mock_connect.return_value)
    assert call is not None, "Expected exactly one INSERT INTO turn_metrics call"
    warned = [c.args[0] for c in mock_log.warning.call_args_list if c.args]
    return call.args[1][4], warned


def test_a_turn_with_no_ledger_row_writes_null_and_says_so():
    """No row is not a free turn, and `sum([])` is 0.

    `AgentTurn.calls` is empty whenever the ledger hook skipped: a usage shape it
    could not read, a streamed body, any response >= 400, or an exception inside
    the hook, which fails open by design. The model was still asked, and the
    tenant was still billed by the provider. Writing 0.00 to turn_metrics reports
    that turn as free, which is exactly the claim #46 exists to stop making, and
    a NULL already reads as unknown to every consumer.
    """
    cost, warned = _run_turn_and_read_the_cost(_empty_seam)

    assert cost is None, (
        f"a turn with no model_calls row was priced at {cost!r}. Zero is a claim "
        "that the turn was free; the honest value is unknown."
    )
    assert "run_agent_turn.turn_cost_unrecorded" in warned, (
        "the turn recorded no ledger row and said nothing about it. A silent NULL "
        f"is indistinguishable from a turn nobody has priced yet. Warned: {warned}"
    )


def test_a_turn_the_price_book_cannot_read_writes_null_and_says_so():
    """The other unknown, and it must land in the same column state."""
    cost, warned = _run_turn_and_read_the_cost(_seam_with([_UNPRICED_CALL]))

    assert cost is None, (
        f"a turn whose model the price book does not carry was priced at {cost!r}"
    )
    assert "run_agent_turn.turn_cost_unpriced" in warned, (
        f"the unpriced call was not reported. Warned: {warned}"
    )
