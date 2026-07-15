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
import sys
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.worker.tasks.runtime import bench as mod

# ---------------------------------------------------------------------------
# PRE-EXISTING INFRA NOTE (not a regression introduced by this plan):
#   app.api.v1.evals transitively imports app.worker.tasks.runtime.eval ->
#   app.services.eval_service -> ragas.metrics.collections -> ragas.llms.base ->
#   langchain_community.chat_models.vertexai, which raises ModuleNotFoundError
#   in this environment (confirmed present on HEAD before this plan's changes
#   — e.g. `pytest tests/unit/test_eval_routes.py` fails to collect
#   identically; see test_bench_routes.py's identical note for the traces
#   module). Unlike traces.py, evals.py itself (not just app.main) sits on
#   this import chain, so a "targeted import of just the router" isn't enough
#   here — a minimal stub module is installed in sys.modules for ONLY that
#   missing leaf import so evals.py can be imported directly in the ledger
#   tests below without touching app.main or attempting to fix the broader
#   ragas/langchain-community version mismatch (out of scope for this plan).
# ---------------------------------------------------------------------------
if "langchain_community.chat_models.vertexai" not in sys.modules:
    _vertexai_stub = types.ModuleType("langchain_community.chat_models.vertexai")
    _vertexai_stub.ChatVertexAI = MagicMock()
    sys.modules["langchain_community.chat_models.vertexai"] = _vertexai_stub

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api.deps import get_current_tenant  # noqa: E402
from app.api.v1 import evals as evals_module  # noqa: E402
from app.core.database import get_async_db  # noqa: E402
from app.models.agent import Agent  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402


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


# ---------------------------------------------------------------------------
# Task 3 (OPS-12): GET /eval-runs ORRERY ledger — select with `-k ledger`
# ---------------------------------------------------------------------------

# Targeted import — a minimal FastAPI app wrapping ONLY the evals router (see
# PRE-EXISTING INFRA NOTE above for why the vertexai stub is required first).
_ledger_test_app = FastAPI()
_ledger_test_app.include_router(evals_module.router, prefix="/api/v1")


def _make_fake_tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    return tenant


def _make_ready_agent(tenant: Tenant) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = tenant.id
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_mock_db_returning_agent(agent: Agent) -> AsyncMock:
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=agent)
    return mock_session


class TestEvalRunsLedgerQuery:
    """Source-level assertions on the ledger SQL itself (mirrors the
    acceptance_criteria grep check: provenance IS NULL folds into authored)."""

    def test_ledger_sql_folds_null_provenance_into_authored(self):
        assert "provenance IS NULL" in evals_module._LEDGER_SQL
        assert "authored_count" in evals_module._LEDGER_SQL
        assert "born_in_production_count" in evals_module._LEDGER_SQL
        assert "red_team_count" in evals_module._LEDGER_SQL


class TestEvalRunsLedgerRoute:
    """OPS-12: GET /agents/{id}/eval-runs surfaces the ORRERY ledger block."""

    async def test_ledger_reports_born_in_production_and_authored_counts(self):
        """Mocked cursor returns known source counts -> ledger block matches exactly."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        def _fake_query(conn_str, sql, params):
            if "born_in_production_count" in sql:
                # 3 production, 1 red_team, 5 authored (includes NULL-provenance legacy rows)
                return [(3, 1, 5)]
            return []  # _LIST_EVAL_RUNS_SQL — empty; ledger is what's under test here

        _ledger_test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _ledger_test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/tenant"
            ), patch(
                "app.api.v1.evals._query_tenant_db_sync", side_effect=_fake_query
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_ledger_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs"
                    )
        finally:
            _ledger_test_app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["ledger"] == {
            "born_in_production_count": 3,
            "red_team_count": 1,
            "authored_count": 5,
        }

    async def test_ledger_treats_null_provenance_legacy_rows_as_authored(self):
        """provenance IS NULL rows fold into authored_count — never an error state."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        def _fake_query(conn_str, sql, params):
            if "born_in_production_count" in sql:
                # No production/red_team rows at all -- everything present is
                # legacy (provenance IS NULL) -- all folded into authored_count.
                return [(0, 0, 12)]
            return []

        _ledger_test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _ledger_test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/tenant"
            ), patch(
                "app.api.v1.evals._query_tenant_db_sync", side_effect=_fake_query
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_ledger_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs"
                    )
        finally:
            _ledger_test_app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["ledger"]["authored_count"] == 12
        assert body["ledger"]["born_in_production_count"] == 0
        assert body["ledger"]["red_team_count"] == 0

    async def test_ledger_null_row_defaults_to_zero_counts(self):
        """No eval_scenarios rows at all -> ledger reports all-zero, not an error."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        def _fake_query(conn_str, sql, params):
            return []  # both queries return no rows

        _ledger_test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _ledger_test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/tenant"
            ), patch(
                "app.api.v1.evals._query_tenant_db_sync", side_effect=_fake_query
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_ledger_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs"
                    )
        finally:
            _ledger_test_app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["ledger"] == {
            "born_in_production_count": 0,
            "red_team_count": 0,
            "authored_count": 0,
        }
