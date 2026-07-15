"""
Unit tests for OPS-11/OPS-12: promote_trace_to_scenario Celery task +
insert_provenance_scenario shared helper + eval-runs ORRERY ledger.

Tests (Task 2 — promote_trace_to_scenario, TDD RED/GREEN):
    1. Signature — promote_trace_to_scenario takes only agent_id/trace_id, no conn_str.
    2. acks_late=True, max_retries=2, queue="runtime".
    3. Idempotency guard — a pre-existing origin_trace_id row skips the insert (zero rows).
    4. Happy path — inserts source='production', origin_trace_id=trace_id, provenance=trace_id.
    5. No agent.response event -> "no_response_event", no insert attempted.

Tests (Task 3 — GET /eval-runs ORRERY ledger, run with -k ledger):
    6. Ledger reports correct born_in_production/authored/red_team counts.
    7. provenance IS NULL legacy rows count as authored (never an error).

Patch targets are symbols imported into app.worker.tasks.runtime.bench:
    - app.worker.tasks.runtime.bench.psycopg2.connect
    - app.worker.tasks.runtime.bench.get_sync_db
    - app.worker.tasks.runtime.bench.fernet_decrypt
    - app.worker.tasks.runtime.bench._fetch_customer_turn
    - app.worker.tasks.runtime.bench.insert_provenance_scenario
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from unittest.mock import MagicMock

from app.worker.tasks.runtime import bench as mod


def _make_sync_db_context(mock_db):
    @contextmanager
    def _ctx():
        yield mock_db

    return _ctx


class _Cursor:
    def __init__(self, fetchone_result=None):
        self.fetchone_result = fetchone_result
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _make_conn(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.close = MagicMock()
    conn.commit = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Test 1/2: signature + acks_late + queue
# ---------------------------------------------------------------------------


def test_promote_trace_signature_has_no_conn_str():
    params = set(inspect.signature(mod.promote_trace_to_scenario.run).parameters)
    assert "conn_str" not in params
    assert "agent_id" in params
    assert "trace_id" in params


def test_promote_trace_acks_late_and_queue():
    assert mod.promote_trace_to_scenario.acks_late is True
    assert mod.promote_trace_to_scenario.max_retries == 2

    source = inspect.getsource(mod)
    assert 'queue="runtime"' in source


# ---------------------------------------------------------------------------
# Test 3: idempotency — second run inserts zero rows
# ---------------------------------------------------------------------------


def test_promote_trace_idempotent_skip_when_already_promoted(monkeypatch):
    agent = MagicMock()
    agent.neon_connection_string = b"encrypted"

    mock_db = MagicMock()
    mock_db.get.return_value = agent

    response_row = MagicMock()
    response_row.payload = {"conversation_id": "conv-1", "text": "the answer"}
    mock_db.execute.return_value.fetchone.return_value = response_row

    monkeypatch.setattr(mod, "get_sync_db", _make_sync_db_context(mock_db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _e: "postgresql://fake/tenant")
    monkeypatch.setattr(mod, "_fetch_customer_turn", lambda *a, **kw: "the question")

    # fetchone() returns a row -> already promoted
    cursor = _Cursor(fetchone_result=(1,))
    conn = _make_conn(cursor)
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

    insert_calls = []
    monkeypatch.setattr(
        mod,
        "insert_provenance_scenario",
        lambda *a, **kw: insert_calls.append((a, kw)),
    )

    result = mod.promote_trace_to_scenario.run("agent-1", "trace-1")

    assert result == {"status": "already_promoted", "trace_id": "trace-1"}
    assert insert_calls == [], "insert_provenance_scenario must NOT be called on idempotent skip"
    assert not conn.commit.called, "no commit should happen on an idempotent skip"


# ---------------------------------------------------------------------------
# Test 4: happy path — inserts source='production', origin_trace_id=trace_id
# ---------------------------------------------------------------------------


def test_promote_trace_happy_path_inserts_production_scenario(monkeypatch):
    agent = MagicMock()
    agent.neon_connection_string = b"encrypted"

    mock_db = MagicMock()
    mock_db.get.return_value = agent

    response_row = MagicMock()
    response_row.payload = {"conversation_id": "conv-1", "text": "the answer"}
    mock_db.execute.return_value.fetchone.return_value = response_row

    monkeypatch.setattr(mod, "get_sync_db", _make_sync_db_context(mock_db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _e: "postgresql://fake/tenant")
    monkeypatch.setattr(mod, "_fetch_customer_turn", lambda *a, **kw: "the question")

    cursor = _Cursor(fetchone_result=None)  # not yet promoted
    conn = _make_conn(cursor)
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

    insert_calls = []

    def _fake_insert(conn_arg, **kwargs):
        insert_calls.append(kwargs)
        return "new-scenario-id"

    monkeypatch.setattr(mod, "insert_provenance_scenario", _fake_insert)

    result = mod.promote_trace_to_scenario.run("agent-1", "trace-1")

    assert result == {"status": "promoted", "trace_id": "trace-1"}
    assert len(insert_calls) == 1
    call = insert_calls[0]
    assert call["source"] == "production"
    assert call["origin_trace_id"] == "trace-1"
    assert call["provenance"] == "trace-1"
    assert call["question"] == "the question"
    assert call["reference_answer"] == "the answer"
    assert conn.commit.called


# ---------------------------------------------------------------------------
# Test 5: no agent.response event -> no_response_event, no insert
# ---------------------------------------------------------------------------


def test_promote_trace_no_response_event_skips_insert(monkeypatch):
    agent = MagicMock()
    agent.neon_connection_string = b"encrypted"

    mock_db = MagicMock()
    mock_db.get.return_value = agent
    mock_db.execute.return_value.fetchone.return_value = None

    monkeypatch.setattr(mod, "get_sync_db", _make_sync_db_context(mock_db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _e: "postgresql://fake/tenant")

    insert_calls = []
    monkeypatch.setattr(
        mod,
        "insert_provenance_scenario",
        lambda *a, **kw: insert_calls.append((a, kw)),
    )

    result = mod.promote_trace_to_scenario.run("agent-1", "trace-1")

    assert result == {"status": "no_response_event", "trace_id": "trace-1"}
    assert insert_calls == []
