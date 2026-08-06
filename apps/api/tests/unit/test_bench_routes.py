"""Unit tests for the failure-triage bench (OPS-09/10).

Tests:
    Service layer (app/services/bench_service.py) — select with `-k service`:
        - list_failing_traces sources conversation_id from the agent.response
          event payload (control-DB job_events), NEVER from a `jobs` table
          (Pitfall 5 / must_haves prohibition).
        - grade_trace refuses to write a second grade once a trace is 'filed'
          (TERRARIUM law — irrevocable).
        - grade_trace validates the grade enum and the trace's agent ownership.
        - bench_tally treats 'filed' as terminal (never overwritten by a later row).

    Route layer (app/api/v1/traces.py):
        GET  /api/v1/agents/{agent_id}/traces?status=failing
        POST /api/v1/agents/{agent_id}/traces/{trace_id}/grade

PRE-EXISTING INFRA NOTE (not a regression introduced by this plan):
    `app.main` transitively imports app.api.v1.evals -> app.worker.tasks.runtime.eval
    -> app.services.eval_service -> ragas.metrics.collections -> ragas.llms.base ->
    langchain_community.chat_models.vertexai, which raises ModuleNotFoundError in
    this environment (confirmed present on HEAD before this plan's changes — e.g.
    `pytest tests/unit/test_eval_routes.py` fails to collect identically). Route
    tests below therefore build a minimal FastAPI app around ONLY
    app.api.v1.traces.router (a "targeted import" of just this plan's router
    module) instead of importing `app.main`, so these tests can actually run in
    this environment without touching the broken import chain.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_tenant
from app.api.v1 import traces as traces_module
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services import bench_service

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _Row:
    """Namespace object mimicking a SQLAlchemy Row's attribute access (row.col_name)."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _mock_result(rows: list | None = None, one=None) -> MagicMock:
    """Build a fake `Result` object returned by `await AsyncSession.execute(...)`."""
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows or [])
    result.fetchone = MagicMock(return_value=one)
    return result


def _make_fake_tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_ready_agent(tenant: Tenant) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = tenant.id
    agent.status = "ready"
    agent.deleted_at = None
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_mock_db_returning_agent(agent: Agent) -> AsyncMock:
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=agent)
    return mock_session


def _make_mock_db_returning_none() -> AsyncMock:
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)
    return mock_session


# ---------------------------------------------------------------------------
# Service tests (Task 1) — select via `-k service`
# ---------------------------------------------------------------------------


class TestListFailingTracesService:
    """Service-level tests for bench_service.list_failing_traces (select with -k service)."""

    async def test_list_failing_traces_service_sources_conversation_id_from_agent_response(self):
        """conversation_id + agent_turn text come from the agent.response event
        payload, NEVER from a `jobs` table query (Pitfall 5 / must_haves)."""
        job_id = str(uuid4())
        agent_id = str(uuid4())

        flagged_row = _Row(job_id=job_id, verdict="ungrounded", reason="missing citation", created_at=None)

        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(rows=[flagged_row]),  # _FLAGGED_EVENTS_SQL
                _mock_result(rows=[]),  # bench_tally's _ALL_GRADED_EVENTS_FOR_AGENT_SQL
                _mock_result(
                    one=_Row(payload={"conversation_id": "conv-123", "text": "agent answer"})
                ),  # _AGENT_RESPONSE_SQL
            ]
        )

        with patch(
            "app.services.bench_service.asyncio.to_thread",
            new=AsyncMock(return_value="customer question"),
        ) as mock_to_thread:
            result = await bench_service.list_failing_traces(
                control_db, "postgresql://fake/tenantdb", agent_id
            )

        assert len(result["traces"]) == 1
        trace = result["traces"][0]
        assert trace["conversation_id"] == "conv-123"
        assert trace["agent_turn"] == "agent answer"
        assert trace["customer_turn"] == "customer question"
        assert trace["verdict"] == "ungrounded"
        assert trace["judge_rationale"] == "missing citation"
        mock_to_thread.assert_called_once()

        # Source assertion (mirrors acceptance_criteria grep check): no query in
        # this call sequence ever references a `jobs` table.
        for call in control_db.execute.call_args_list:
            sql_text = str(call.args[0])
            assert "FROM jobs" not in sql_text

    async def test_list_failing_traces_service_skips_trace_without_agent_response_event(self):
        """A flagged job_id with no matching agent.response row is skipped, not
        surfaced with a hollow/empty turn."""
        job_id = str(uuid4())
        agent_id = str(uuid4())
        flagged_row = _Row(job_id=job_id, verdict="fail", reason="off-topic", created_at=None)

        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(rows=[flagged_row]),
                _mock_result(rows=[]),
                _mock_result(one=None),  # no agent.response row for this job_id
            ]
        )

        result = await bench_service.list_failing_traces(
            control_db, "postgresql://fake/tenantdb", agent_id
        )

        assert result["traces"] == []

    async def test_list_failing_traces_service_dedupes_by_job_id(self):
        """Gatekeeper + Auditor can both flag the same job_id — only one trace
        row is returned per job_id (most recent verdict wins)."""
        job_id = str(uuid4())
        agent_id = str(uuid4())
        rows = [
            _Row(job_id=job_id, verdict="fail", reason="gatekeeper reason", created_at=None),
            _Row(job_id=job_id, verdict="ungrounded", reason="auditor reason", created_at=None),
        ]

        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(rows=rows),
                _mock_result(rows=[]),
                _mock_result(one=_Row(payload={"conversation_id": "conv-1", "text": "answer"})),
            ]
        )

        with patch(
            "app.services.bench_service.asyncio.to_thread",
            new=AsyncMock(return_value="question"),
        ):
            result = await bench_service.list_failing_traces(
                control_db, "postgresql://fake/tenantdb", agent_id
            )

        assert len(result["traces"]) == 1
        # The first (most recent, since query orders DESC) verdict wins.
        assert result["traces"][0]["verdict"] == "fail"

    async def test_list_failing_traces_service_includes_tally(self):
        """Response includes a tally dict alongside the traces list."""
        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(rows=[]),  # no flagged events
                _mock_result(rows=[]),  # tally
            ]
        )

        result = await bench_service.list_failing_traces(
            control_db, "postgresql://fake/tenantdb", str(uuid4())
        )

        assert result["traces"] == []
        assert result["tally"] == {"filed": 0, "held": 0, "dismissed": 0}


class TestBenchTallyService:
    """Service-level tests for bench_service.bench_tally (select with -k service)."""

    async def test_bench_tally_service_treats_filed_as_terminal(self):
        """A later row for the same job_id must never overwrite a 'filed' grade
        (belt-and-suspenders — grade_trace() already refuses to write this)."""
        job_id = str(uuid4())
        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            return_value=_mock_result(
                rows=[
                    _Row(job_id=job_id, grade="filed"),
                    _Row(job_id=job_id, grade="held"),
                ]
            )
        )

        tally = await bench_service.bench_tally(control_db, str(uuid4()))

        assert tally["counts"]["filed"] == 1
        assert tally["counts"]["held"] == 0
        assert tally["graded_by_job"][job_id] == "filed"

    async def test_bench_tally_service_counts_distinct_job_ids(self):
        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            return_value=_mock_result(
                rows=[
                    _Row(job_id=str(uuid4()), grade="filed"),
                    _Row(job_id=str(uuid4()), grade="held"),
                    _Row(job_id=str(uuid4()), grade="dismissed"),
                ]
            )
        )

        tally = await bench_service.bench_tally(control_db, str(uuid4()))

        assert tally["counts"] == {"filed": 1, "held": 1, "dismissed": 1}


class TestGradeTraceService:
    """Service-level tests for bench_service.grade_trace (select with -k service)."""

    async def test_grade_trace_service_raises_on_second_filed_grade(self):
        """TERRARIUM law: a trace already graded 'filed' cannot be re-graded."""
        agent_id = str(uuid4())
        trace_id = str(uuid4())

        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(one=_Row()),  # owner check passes
                _mock_result(rows=[_Row(grade="filed")]),  # already filed
            ]
        )
        control_db.add = MagicMock()
        control_db.commit = AsyncMock()

        with pytest.raises(bench_service.TraceAlreadyFiledError):
            await bench_service.grade_trace(control_db, agent_id, trace_id, "held")

        control_db.add.assert_not_called()
        control_db.commit.assert_not_called()

    async def test_grade_trace_service_validates_grade_enum(self):
        """An invalid grade value raises InvalidGradeError before any DB call."""
        control_db = AsyncMock()

        with pytest.raises(bench_service.InvalidGradeError):
            await bench_service.grade_trace(control_db, str(uuid4()), str(uuid4()), "banana")

        control_db.execute.assert_not_called()

    async def test_grade_trace_service_raises_not_found_for_foreign_agent_trace(self):
        """T-21-05-01: a trace_id with no flagged event owned by agent_id is refused."""
        control_db = AsyncMock()
        control_db.execute = AsyncMock(side_effect=[_mock_result(one=None)])  # owner check fails

        with pytest.raises(bench_service.TraceNotFoundError):
            await bench_service.grade_trace(control_db, str(uuid4()), str(uuid4()), "held")

    async def test_grade_trace_service_writes_job_event_and_returns_tally(self):
        """Happy path: inserts a 'trace.graded' job_events row and returns the tally."""
        agent_id = str(uuid4())
        trace_id = str(uuid4())

        control_db = AsyncMock()
        control_db.execute = AsyncMock(
            side_effect=[
                _mock_result(one=_Row()),  # owner check passes
                _mock_result(rows=[]),  # no existing grades
                _mock_result(rows=[_Row(job_id=trace_id, grade="held")]),  # post-commit tally read
            ]
        )
        control_db.add = MagicMock()
        control_db.commit = AsyncMock()

        result = await bench_service.grade_trace(control_db, agent_id, trace_id, "held")

        assert result["trace_id"] == trace_id
        assert result["grade"] == "held"
        assert result["tally"]["held"] == 1
        control_db.add.assert_called_once()
        control_db.commit.assert_awaited_once()

        added_event = control_db.add.call_args.args[0]
        assert added_event.event_type == "trace.graded"
        assert added_event.payload["grade"] == "held"
        assert added_event.payload["agent_id"] == agent_id


# ---------------------------------------------------------------------------
# Route tests (Task 2)
# ---------------------------------------------------------------------------

# Targeted import — a minimal FastAPI app wrapping ONLY the traces router, so
# these tests never import app.main (see PRE-EXISTING INFRA NOTE above).
_test_app = FastAPI()
_test_app.include_router(traces_module.router, prefix="/api/v1")


class TestListTracesRoute:
    async def test_returns_404_on_cross_tenant_idor(self):
        """404 (not 403) when the agent belongs to a different tenant."""
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        foreign_agent = _make_ready_agent(other_tenant)
        mock_db = _make_mock_db_returning_agent(foreign_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/agents/{foreign_agent.id}/traces")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_404_when_agent_not_found(self):
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_returning_none()

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/agents/{uuid4()}/traces")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_200_with_traces_and_tally_shape(self):
        """Happy path: 200 with the {traces, tally} response shape."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        fake_result = {
            "traces": [
                {
                    "trace_id": str(uuid4()),
                    "verdict": "ungrounded",
                    "judge_rationale": "no citation",
                    "customer_turn": "What's your return policy?",
                    "agent_turn": "I don't know",
                    "conversation_id": "conv-1",
                    "graded_status": "ungraded",
                }
            ],
            "tally": {"filed": 0, "held": 0, "dismissed": 0},
        }

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch(
                    "app.api.v1.traces.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.traces.bench_service.list_failing_traces",
                    new=AsyncMock(return_value=fake_result),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/traces?status=failing"
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json() == fake_result

    async def test_returns_400_for_unsupported_status_value(self):
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{ready_agent.id}/traces?status=resolved"
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 400


class TestGradeTraceRoute:
    async def test_returns_404_on_cross_tenant_idor(self):
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        foreign_agent = _make_ready_agent(other_tenant)
        mock_db = _make_mock_db_returning_agent(foreign_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{foreign_agent.id}/traces/{uuid4()}/grade",
                    json={"grade": "held"},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_422_on_invalid_grade_enum(self):
        """T-21-05-03: grade enum injection — Pydantic Literal returns 422."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{ready_agent.id}/traces/{uuid4()}/grade",
                    json={"grade": "not-a-real-grade"},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 422

    async def test_returns_409_when_service_raises_already_filed(self):
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch(
                    "app.api.v1.traces.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.traces.bench_service.grade_trace",
                    new=AsyncMock(
                        side_effect=bench_service.TraceAlreadyFiledError("already filed")
                    ),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/traces/{uuid4()}/grade",
                        json={"grade": "filed"},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 409

    async def test_returns_404_when_service_raises_not_found(self):
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch(
                    "app.api.v1.traces.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.traces.bench_service.grade_trace",
                    new=AsyncMock(
                        side_effect=bench_service.TraceNotFoundError("not found")
                    ),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/traces/{uuid4()}/grade",
                        json={"grade": "held"},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_200_happy_path(self):
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)
        trace_id = uuid4()

        fake_result = {
            "trace_id": str(trace_id),
            "grade": "held",
            "tally": {"filed": 0, "held": 1, "dismissed": 0},
        }

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch(
                    "app.api.v1.traces.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.traces.bench_service.grade_trace",
                    new=AsyncMock(return_value=fake_result),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/traces/{trace_id}/grade",
                        json={"grade": "held"},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json() == fake_result

    async def test_second_filed_grade_returns_409(self):
        """POST grade='filed' twice on the same trace: first succeeds, second 409s."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)
        trace_id = uuid4()

        call_count = {"n": 0}

        async def _fake_grade_trace(control_db, agent_id, tid, grade):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"trace_id": tid, "grade": grade, "tally": {"filed": 1, "held": 0, "dismissed": 0}}
            raise bench_service.TraceAlreadyFiledError("already filed")

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch(
                    "app.api.v1.traces.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.traces.bench_service.grade_trace",
                    new=AsyncMock(side_effect=_fake_grade_trace),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    first = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/traces/{trace_id}/grade",
                        json={"grade": "filed"},
                    )
                    second = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/traces/{trace_id}/grade",
                        json={"grade": "filed"},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert first.status_code == 200
        assert second.status_code == 409

    async def test_grade_filed_dispatches_promote_trace_to_scenario(self):
        """OPS-11 seam: filing a trace MUST dispatch the promotion task.

        Regression guard for the Phase-21 verification gap where
        promote_trace_to_scenario existed, was idempotent, and passed its own
        tests -- but nothing in application code ever called it, silently
        breaking the flywheel's write side.
        """
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)
        trace_id = str(uuid4())

        graded = {
            "trace_id": trace_id,
            "grade": "filed",
            "tally": {"filed": 1, "held": 0, "dismissed": 0},
        }

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch(
                    "app.api.v1.traces.bench_service.grade_trace",
                    new=AsyncMock(return_value=graded),
                ),
                patch(
                    "app.worker.tasks.runtime.bench.promote_trace_to_scenario.apply_async"
                ) as mock_dispatch,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/traces/{trace_id}/grade",
                        json={"grade": "filed"},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        mock_dispatch.assert_called_once()
        # Only IDs cross the task boundary -- never a conn_str (CLAUDE.md rule 4).
        assert mock_dispatch.call_args.kwargs["args"] == [str(ready_agent.id), trace_id]

    async def test_grade_held_does_not_dispatch_promote_trace_to_scenario(self):
        """Only 'filed' promotes -- 'held'/'dismissed' must not fire the task."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)
        trace_id = str(uuid4())

        graded = {
            "trace_id": trace_id,
            "grade": "held",
            "tally": {"filed": 0, "held": 1, "dismissed": 0},
        }

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch(
                    "app.api.v1.traces.bench_service.grade_trace",
                    new=AsyncMock(return_value=graded),
                ),
                patch(
                    "app.worker.tasks.runtime.bench.promote_trace_to_scenario.apply_async"
                ) as mock_dispatch,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/traces/{trace_id}/grade",
                        json={"grade": "held"},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        mock_dispatch.assert_not_called()
