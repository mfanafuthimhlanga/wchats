"""
Unit tests for PROD-05: single pooled tenant-DB connection per run_agent_turn.

Plan 13-03 refactored the four per-turn psycopg2.connect() calls
(_create_conversation_row, _validate_conversation_owner, _read_turn_history,
_persist_messages) into a single connection opened in run_agent_turn and closed
in a finally block.  These tests assert the 4→1 reduction.

Mock strategy:
  - patch app.worker.tasks.runtime.agent.psycopg2.connect with a MagicMock;
    its return_value is the shared mock connection.
  - patch asyncio.run at the asyncio.run() boundary (same convention as
    test_agent_task.py, and do NOT use AsyncMock for the turn).
  - patch all four helpers at module level so they exercise the CALL SIGNATURE
    (conn object, not conn_str) without touching any real DB.
  - patch build_agent_turn, because the real seam builds a provider client and
    a live tool server bound to the tenant connection string. This file is about
    connection lifecycle, not about what the turn contains.
  - Control-DB session mocked via get_sync_db.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seam(**_kwargs):
    """Stand-in for `build_agent_turn`, and the one boundary these tests replace.

    The real seam builds a provider client and a live tool server bound to the
    tenant connection string; neither belongs in a test about the task body. The
    only field the task reads off the turn is `calls`, the ledger rows the loop
    accumulates, so that is what this carries. An empty list prices the turn at
    zero, which is correct for a turn that made no model call.
    """
    return SimpleNamespace(calls=[])


def _make_agent() -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.name = "Batch Test Agent"
    agent.soul_role = "assistant"
    agent.soul_voice = "friendly"
    agent.soul_do_list = []
    agent.soul_donot_list = []
    agent.retrieval_strategy = {}
    agent.neon_connection_string = b"encrypted-pooled-bytes"
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


_CANNED_TURN_RESULT = {
    "response_text": (
        "Here is the answer.\n\n"
        "CITATIONS:\n"
        "- Document: Policy.pdf | Section: 2\n"
    ),
    "tool_calls_log": [],
    "escalated": False,
    "escalation_reason": None,
    "escalation_context": None,
}


# ---------------------------------------------------------------------------
# Test 1: First-turn path — exactly ONE psycopg2.connect per turn
# ---------------------------------------------------------------------------

def test_first_turn_opens_exactly_one_tenant_connection():
    """PROD-05: run_agent_turn (first turn) must call psycopg2.connect exactly once."""
    from app.core.config import settings
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    job = _make_job(job_id)
    local_conv_id = "aabbccdd-0000-0000-0000-000000000001"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, job]

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://pooled-host/tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect") as mock_connect,
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_TURN_RESULT),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="Hello",
            conversation_id=None,
        )

    # Core assertion: exactly ONE tenant-DB connect per turn (PROD-05 goal)
    assert mock_connect.call_count == 1, (
        f"PROD-05 violation: expected 1 psycopg2.connect call, got {mock_connect.call_count}. "
        f"run_agent_turn must open ONE shared connection per turn, not one per helper."
    )
    # Must use the pooled connection string (not a direct string)
    assert mock_connect.call_args == call(
        "postgresql://pooled-host/tenant",
        connect_timeout=settings.TENANT_DB_CONNECT_TIMEOUT_S,
    ), f"Unexpected connect args: {mock_connect.call_args}"

    # Connection must be closed in the finally block
    mock_connect.return_value.close.assert_called_once_with()


# ---------------------------------------------------------------------------
# Test 2: Subsequent-turn path — exactly ONE psycopg2.connect per turn
# ---------------------------------------------------------------------------

def test_subsequent_turn_opens_exactly_one_tenant_connection():
    """PROD-05: run_agent_turn (subsequent turn) must call psycopg2.connect exactly once."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    job = _make_job(job_id)
    existing_conv_id = str(uuid.uuid4())

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://pooled-host/tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect") as mock_connect,
        patch(
            "app.worker.tasks.runtime.agent._validate_conversation_owner",
            return_value={"id": existing_conv_id, "metadata": {}},
        ),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_TURN_RESULT),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="Follow-up",
            conversation_id=existing_conv_id,
        )

    # Core assertion: exactly ONE connect on the subsequent-turn path too
    assert mock_connect.call_count == 1, (
        f"PROD-05 violation: subsequent turn must also open exactly 1 connection, "
        f"got {mock_connect.call_count}"
    )
    mock_connect.return_value.close.assert_called_once_with()


# ---------------------------------------------------------------------------
# Test 3: Helpers receive the shared connection object (not conn_str string)
# ---------------------------------------------------------------------------

def test_helpers_receive_shared_connection_not_conn_str():
    """PROD-05: every per-turn helper must receive the shared conn object."""
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    job = _make_job(job_id)
    local_conv_id = "aabbccdd-0000-0000-0000-000000000002"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    mock_create = MagicMock(return_value=local_conv_id)
    mock_history = MagicMock(return_value=[])
    mock_persist = MagicMock()

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://pooled-host/tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect") as mock_connect,
        patch("app.worker.tasks.runtime.agent._create_conversation_row", mock_create),
        patch("app.worker.tasks.runtime.agent._read_turn_history", mock_history),
        patch("app.worker.tasks.runtime.agent._persist_messages", mock_persist),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_TURN_RESULT),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        run_agent_turn.run(
            job_id=job_id,
            agent_id=agent_id,
            message="Any question",
            conversation_id=None,
        )

    shared_conn = mock_connect.return_value

    # _create_conversation_row called with (conn_object, agent_id)
    assert mock_create.call_args[0][0] is shared_conn, (
        f"_create_conversation_row must receive the shared conn object, "
        f"got: {mock_create.call_args[0][0]!r}"
    )

    # _read_turn_history is the read half of the same rule. It runs only on a
    # subsequent turn, which this fixture is not, so the assertion is that it was
    # NOT called rather than that it took the shared connection: a first turn has
    # no history and a query for one is a round trip nobody needs.
    assert mock_history.call_count == 0, (
        "a first turn read conversation history. There is none, because the "
        "conversation row was created seconds earlier in this same task."
    )

    # _persist_messages called with conn=conn_object
    persist_kwargs = mock_persist.call_args[1]
    assert persist_kwargs.get("conn") is shared_conn, (
        f"_persist_messages must receive conn=shared_conn, "
        f"got conn={persist_kwargs.get('conn')!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: Connection closed in finally even when the turn raises
# ---------------------------------------------------------------------------

def test_connection_closed_in_finally_when_the_turn_raises():
    """PROD-05 (T-13-03-03): tenant_conn.close() must run even when the turn raises.

    When asyncio.run() raises, Celery calls self.retry() which re-raises the
    original exception out of run_agent_turn.  The finally block must still
    close the connection before the exception propagates.
    """
    from app.worker.tasks.runtime.agent import run_agent_turn

    job_id = str(uuid.uuid4())
    agent = _make_agent()
    agent_id = str(agent.id)
    job = _make_job(job_id)
    local_conv_id = "aabbccdd-0000-0000-0000-000000000003"

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    # Simulate the turn raising an exception
    turn_error = RuntimeError("the provider call never returned")

    mock_connect = MagicMock()

    with (
        patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
        patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://pooled-host/tenant"),
        patch("app.worker.tasks.runtime.agent.psycopg2.connect", mock_connect),
        patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
        # asyncio.run raises, standing in for a run_agent_loop failure
        patch("app.worker.tasks.runtime.agent.asyncio.run", side_effect=turn_error),
        patch("app.worker.tasks.runtime.agent.emit"),
    ):
        # Celery's self.retry() re-raises the original exception.
        # Catch it here; we only care that close() ran in the finally block.
        try:
            run_agent_turn.run(
                job_id=job_id,
                agent_id=agent_id,
                message="Will this crash?",
                conversation_id=None,
            )
        except Exception:
            pass  # expected — retry propagates the original RuntimeError

    # The finally block MUST have closed the connection even though an exception
    # propagated out of run_agent_turn (T-13-03-03: no connection leak on exception).
    mock_connect.return_value.close.assert_called_once_with()
