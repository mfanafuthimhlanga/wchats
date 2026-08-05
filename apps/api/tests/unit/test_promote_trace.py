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

import pytest

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

    assert result == {
        "status": "promoted",
        "trace_id": "trace-1",
        "scenario_id": "new-scenario-id",
        "reference_answer_stored": False,
    }
    assert len(insert_calls) == 1
    call = insert_calls[0]
    assert call["source"] == "production"
    assert call["origin_trace_id"] == "trace-1"
    assert call["provenance"] == "trace-1"
    assert call["question"] == "the question"
    # D5: the agent's FAILING answer is not the ground truth for its own
    # question. See TestFiledTraceCarriesNoGroundTruth below.
    assert call["reference_answer"] == mod.NO_GROUND_TRUTH
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
# Audit D5: the human label is stored as its own opposite
#
# traces.py lists FAILING traces. Grading one 'filed' used to write the agent's
# own failing answer into reference_answer — the column that means "the correct
# answer", and the column the verified_qa promotion path reads before serving an
# answer to a real customer via retrieval_service.verified_qa_lookup (0.93
# cosine, ahead of hybrid search).
#
# This was inert only because eval results were being written to a Neon branch
# that the eval task deleted. This branch makes those writes durable, so the
# label fix and the persistence fix have to land together — these tests are the
# half that keeps a repaired write-back from becoming a delivery mechanism.
# ---------------------------------------------------------------------------


def _wire_promotion(monkeypatch, agent_turn: str, question: str = "the question"):
    """Wire promote_trace_to_scenario with every boundary doubled.

    Returns (mock_db, insert_calls) — mock_db is both the agent/response lookup
    session and the session the trace note is appended through.
    """
    agent = MagicMock()
    agent.neon_connection_string = b"encrypted"

    mock_db = MagicMock()
    mock_db.get.return_value = agent

    response_row = MagicMock()
    response_row.payload = {"conversation_id": "conv-1", "text": agent_turn}
    mock_db.execute.return_value.fetchone.return_value = response_row

    monkeypatch.setattr(mod, "get_sync_db", _make_sync_db_context(mock_db))
    monkeypatch.setattr(mod, "fernet_decrypt", lambda _e: "postgresql://fake/tenant")
    monkeypatch.setattr(mod, "_fetch_customer_turn", lambda *a, **kw: question)

    cursor = _Cursor(fetchone_result=None)
    conn = _make_conn(cursor)
    monkeypatch.setattr(mod.psycopg2, "connect", lambda *a, **kw: conn)

    insert_calls = []

    def _fake_insert(conn_arg, **kwargs):
        insert_calls.append(kwargs)
        return "new-scenario-id"

    monkeypatch.setattr(mod, "insert_provenance_scenario", _fake_insert)
    return mock_db, insert_calls


class TestFiledTraceCarriesNoGroundTruth:

    @pytest.mark.parametrize(
        "agent_turn",
        [
            "I'm sorry, your refund of R4,500 was processed yesterday.",
            "Yes, we ship to Antarctica for free.",
            "   ",
            "0",
            "the answer",
            "a" * 5000,
        ],
    )
    def test_the_flagged_answer_is_never_written_as_the_reference_answer(
        self, monkeypatch, agent_turn
    ):
        """The core D5 assertion, over answers a hallucinating agent plausibly
        emits — including whitespace and '0', which a truthiness-based guard
        would let through.

        An empty agent_turn is deliberately not in this list: NO_GROUND_TRUTH is
        itself the empty string, so equality there is vacuous rather than a leak,
        and the positive assertion below is the one that carries the meaning.
        """
        _mock_db, insert_calls = _wire_promotion(monkeypatch, agent_turn)

        mod.promote_trace_to_scenario.run("agent-1", "trace-1")

        assert len(insert_calls) == 1
        stored = insert_calls[0]["reference_answer"]
        assert stored == mod.NO_GROUND_TRUTH
        assert stored != agent_turn, (
            "the agent's flagged answer was stored as the ground truth for its "
            "own question — this row can reach verified_qa and be served to a "
            "customer as a verified answer"
        )

    def test_source_contains_no_call_passing_the_agent_turn_as_a_label(self):
        """Absence pin on the defect's exact shape, so a revert is caught even
        if someone adds a new call site the behaviour tests do not reach."""
        source = inspect.getsource(mod)
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        assert "reference_answer=agent_turn" not in code
        assert "reference_answer=NO_GROUND_TRUTH" in code

    def test_the_scenario_is_inert_to_the_eval_selector_by_construction(
        self, monkeypatch
    ):
        """reference_answer='' matches the `mined` convention, and run_eval_suite
        selects WHERE reference_answer != '' — so an unlabelled row cannot be
        scored even if some future caller forgets it has no ground truth.

        Read out of the task's own SQL rather than restated, because the two
        halves of this guarantee live in different modules.
        """
        from app.worker.tasks.runtime import eval as eval_task

        selector = inspect.getsource(eval_task.run_eval_suite)
        assert "reference_answer != ''" in selector, (
            "run_eval_suite no longer filters out unlabelled scenarios — a "
            "filed trace with no ground truth would now be scored against "
            "whatever happens to be in reference_answer"
        )
        assert mod.NO_GROUND_TRUTH == "", (
            "NO_GROUND_TRUTH must be the empty string for the selector above to "
            "exclude it"
        )

    def test_the_missing_label_is_recorded_on_the_trace(self, monkeypatch):
        """The operator filed this expecting it to be evaluated. They are owed a
        straight answer about why it will not be, on the trace they filed."""
        mock_db, _ = _wire_promotion(monkeypatch, "the failing answer")

        result = mod.promote_trace_to_scenario.run("agent-1", "trace-1")

        assert result["reference_answer_stored"] is False

        added = [c.args[0] for c in mock_db.add.call_args_list]
        notes = [e for e in added if e.event_type == "trace.promoted_to_scenario"]
        assert len(notes) == 1, (
            "no 'trace.promoted_to_scenario' event was appended — the run stored "
            "an unscorable scenario and said nothing about it"
        )
        note = notes[0]
        assert note.job_id == "trace-1"
        assert note.payload["reference_answer_stored"] is False
        assert note.payload["reason"]
        # The flagged answer is preserved, in a field that claims nothing about
        # correctness. It is not a second copy of the label; the durable copy
        # lives in this trace's own 'agent.response' event.
        assert note.payload["flagged_agent_answer"] == "the failing answer"
        assert "reference_answer" not in note.payload

    def test_a_failed_trace_note_does_not_undo_a_committed_promotion(
        self, monkeypatch
    ):
        """The scenario insert is already committed and idempotency-guarded.
        Retrying it for the sake of a note would be worse than losing the note,
        and the flagged answer is not lost either way — it lives in the trace's
        'agent.response' event, which is where this task read it from."""
        mock_db, insert_calls = _wire_promotion(monkeypatch, "the failing answer")
        mock_db.commit.side_effect = [None, RuntimeError("control DB unreachable")]

        result = mod.promote_trace_to_scenario.run("agent-1", "trace-1")

        assert result["status"] == "promoted"
        assert len(insert_calls) == 1


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
