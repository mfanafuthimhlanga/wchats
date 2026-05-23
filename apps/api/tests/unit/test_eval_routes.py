"""
Unit tests for the eval routes in apps/api/app/api/v1/evals.py.

Tests:
    GET  /api/v1/agents/{agent_id}/eval-runs
    GET  /api/v1/agents/{agent_id}/eval-runs/{run_id}/results
    POST /api/v1/agents/{agent_id}/eval-runs/trigger

Coverage:
    - Happy-path response shapes (RESEARCH.md §9)
    - IDOR prevention: 404 on agent not found, 404 on cross-tenant access
    - POST trigger: 202 on ready agent, 400 on non-ready agent, 404 on unknown agent
    - POST trigger: only agent_id dispatched to Celery (CTL-08)
    - GET routes: asyncio.to_thread + psycopg2 path mocked correctly
    - Auth: 401/403 when X-API-Key header is missing (no dependency overrides)
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.api.deps import get_async_db, get_current_tenant
from app.main import app
from app.models.agent import Agent
from app.models.tenant import Tenant


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_fake_tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_ready_agent(tenant: Tenant) -> Agent:
    """Agent in 'ready' state with a fake encrypted neon_connection_string."""
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = tenant.id
    agent.status = "ready"
    agent.deleted_at = None
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_mock_db_returning_agent(agent: Agent) -> AsyncMock:
    """Async DB mock that returns *agent* on db.get(Agent, agent_id)."""
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=agent)
    return mock_session


def _make_mock_db_returning_none() -> AsyncMock:
    """Async DB mock that returns None on db.get() — simulates missing agent."""
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)
    return mock_session


def _fake_eval_runs_rows() -> list[tuple]:
    """Two fake eval_run rows: one complete, one failed."""
    return [
        (
            str(uuid4()),                               # id
            datetime(2026, 5, 23, 2, 0, 0, tzinfo=timezone.utc),   # started_at
            datetime(2026, 5, 23, 2, 4, 0, tzinfo=timezone.utc),   # finished_at
            "complete",                                  # status
            20,                                         # scenario_count
            0.87,                                       # faithfulness
            0.91,                                       # answer_relevancy
            0.83,                                       # context_precision
            0.79,                                       # context_recall
        ),
        (
            str(uuid4()),
            datetime(2026, 5, 22, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 22, 2, 1, 0, tzinfo=timezone.utc),
            "failed",
            0,
            None,  # NULL scores on failed run
            None,
            None,
            None,
        ),
    ]


def _fake_eval_results_rows(run_id: str, scenario_id: str) -> list[tuple]:
    """Rows for a single scenario with all four metrics."""
    return [
        (scenario_id, "What is your return policy?", "generated", "faithfulness", 0.95),
        (scenario_id, "What is your return policy?", "generated", "answer_relevancy", 0.88),
        (scenario_id, "What is your return policy?", "generated", "context_precision", 0.90),
        (scenario_id, "What is your return policy?", "generated", "context_recall", 0.85),
    ]


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/eval-runs — list eval runs
# ---------------------------------------------------------------------------


class TestListEvalRuns:
    """Tests for GET /api/v1/agents/{agent_id}/eval-runs."""

    async def test_returns_200_with_eval_runs_shape(self):
        """Happy path: returns 200 with eval_runs list and aggregate_scores dicts."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        fake_rows = _fake_eval_runs_rows()

        try:
            with (
                patch(
                    "app.api.v1.evals.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.evals.asyncio.to_thread",
                    new=AsyncMock(return_value=fake_rows),
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert "eval_runs" in body
        assert len(body["eval_runs"]) == 2

        first = body["eval_runs"][0]
        assert "id" in first
        assert "started_at" in first
        assert "finished_at" in first
        assert "status" in first
        assert "scenario_count" in first
        assert "aggregate_scores" in first
        scores = first["aggregate_scores"]
        assert "faithfulness" in scores
        assert "answer_relevancy" in scores
        assert "context_precision" in scores
        assert "context_recall" in scores
        assert first["status"] == "complete"
        assert first["scenario_count"] == 20
        assert abs(scores["faithfulness"] - 0.87) < 0.001

    async def test_null_scores_map_to_zero(self):
        """NULL metric scores (failed run) map to 0.0 in aggregate_scores."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        fake_rows = _fake_eval_runs_rows()  # second row has NULL scores

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=fake_rows)),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        body = response.json()
        failed_run = body["eval_runs"][1]
        assert failed_run["status"] == "failed"
        assert failed_run["aggregate_scores"]["faithfulness"] == 0.0

    async def test_returns_404_when_agent_not_found(self):
        """404 when agent doesn't exist in control DB."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_returning_none()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{uuid4()}/eval-runs",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_404_on_cross_tenant_idor(self):
        """404 on IDOR attempt — agent exists but belongs to a different tenant."""
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        # Agent belongs to other_tenant, not fake_tenant
        foreign_agent = _make_ready_agent(other_tenant)
        mock_db = _make_mock_db_returning_agent(foreign_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{foreign_agent.id}/eval-runs",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        # Must return 404 — not 403 — to prevent tenant enumeration
        assert response.status_code == 404

    async def test_requires_api_key(self):
        """401/403 when X-API-Key header is missing."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/agents/{uuid4()}/eval-runs")

        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/eval-runs/{run_id}/results — per-scenario results
# ---------------------------------------------------------------------------


class TestGetEvalRunResults:
    """Tests for GET /api/v1/agents/{agent_id}/eval-runs/{run_id}/results."""

    async def test_returns_200_with_results_shape(self):
        """Happy path: returns 200 with results list grouped by scenario."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        run_id = uuid4()
        scenario_id = str(uuid4())
        fake_rows = _fake_eval_results_rows(str(run_id), scenario_id)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=fake_rows)),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/{run_id}/results",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert "results" in body
        assert len(body["results"]) == 1

        result = body["results"][0]
        assert result["scenario_id"] == scenario_id
        assert result["question"] == "What is your return policy?"
        assert result["source"] == "generated"
        assert "scores" in result
        assert "passed" in result
        scores = result["scores"]
        assert abs(scores["faithfulness"] - 0.95) < 0.001
        assert abs(scores["answer_relevancy"] - 0.88) < 0.001

    async def test_passed_flag_true_when_all_scores_above_threshold(self):
        """passed=True when all four scores >= EVAL_FAITHFULNESS_THRESHOLD (0.90)."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        run_id = uuid4()
        scenario_id = str(uuid4())
        # All scores >= 0.90
        passing_rows = [
            (scenario_id, "Q?", "generated", "faithfulness", 0.95),
            (scenario_id, "Q?", "generated", "answer_relevancy", 0.92),
            (scenario_id, "Q?", "generated", "context_precision", 0.91),
            (scenario_id, "Q?", "generated", "context_recall", 0.90),
        ]

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=passing_rows)),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/{run_id}/results",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        body = response.json()
        assert body["results"][0]["passed"] is True

    async def test_passed_flag_false_when_any_score_below_threshold(self):
        """passed=False when any metric score < EVAL_FAITHFULNESS_THRESHOLD (0.90)."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        run_id = uuid4()
        scenario_id = str(uuid4())
        # context_recall = 0.79 — below threshold
        failing_rows = [
            (scenario_id, "Q?", "generated", "faithfulness", 0.95),
            (scenario_id, "Q?", "generated", "answer_relevancy", 0.92),
            (scenario_id, "Q?", "generated", "context_precision", 0.91),
            (scenario_id, "Q?", "generated", "context_recall", 0.79),
        ]

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=failing_rows)),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/{run_id}/results",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        body = response.json()
        assert body["results"][0]["passed"] is False

    async def test_returns_empty_results_when_no_rows(self):
        """Empty results list when no eval_results rows exist for the run."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.evals.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.evals.asyncio.to_thread", new=AsyncMock(return_value=[])),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/{uuid4()}/results",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["results"] == []

    async def test_returns_404_on_cross_tenant_idor(self):
        """404 on IDOR: agent belongs to a different tenant."""
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        foreign_agent = _make_ready_agent(other_tenant)
        mock_db = _make_mock_db_returning_agent(foreign_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{foreign_agent.id}/eval-runs/{uuid4()}/results",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_requires_api_key(self):
        """401/403 when X-API-Key header is missing."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/agents/{uuid4()}/eval-runs/{uuid4()}/results"
            )

        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/eval-runs/trigger — manual dispatch
# ---------------------------------------------------------------------------


class TestTriggerEvalRun:
    """Tests for POST /api/v1/agents/{agent_id}/eval-runs/trigger."""

    async def test_returns_202_with_queued_status_and_task_id(self):
        """Happy path: 202 with status='queued', task_id, agent_id."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        fake_task_id = str(uuid4())
        mock_async_result = MagicMock()
        mock_async_result.id = fake_task_id

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.api.v1.evals.run_eval_suite.apply_async",
                return_value=mock_async_result,
            ) as mock_dispatch:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/trigger",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["task_id"] == fake_task_id
        assert body["agent_id"] == str(ready_agent.id)

    async def test_dispatches_celery_with_agent_id_only_not_conn_str(self):
        """CTL-08: Celery task must receive only agent_id, never conn_str."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        mock_async_result = MagicMock()
        mock_async_result.id = str(uuid4())

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with patch(
                "app.api.v1.evals.run_eval_suite.apply_async",
                return_value=mock_async_result,
            ) as mock_dispatch:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    await client.post(
                        f"/api/v1/agents/{ready_agent.id}/eval-runs/trigger",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args
        # Must use kwargs= with agent_id only, and queue="runtime"
        assert call_kwargs.kwargs["queue"] == "runtime"
        task_kwargs = call_kwargs.kwargs["kwargs"]
        assert "agent_id" in task_kwargs
        assert task_kwargs["agent_id"] == str(ready_agent.id)
        # Must NOT include conn_str or neon_connection_string
        assert "conn_str" not in task_kwargs
        assert "neon_connection_string" not in task_kwargs

    async def test_returns_400_when_agent_not_ready(self):
        """400 when agent.status != 'ready' (e.g. still building)."""
        fake_tenant = _make_fake_tenant()
        building_agent = _make_ready_agent(fake_tenant)
        building_agent.status = "building"
        mock_db = _make_mock_db_returning_agent(building_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{building_agent.id}/eval-runs/trigger",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 400
        assert "ready" in response.json()["detail"].lower()

    async def test_returns_404_when_agent_not_found(self):
        """404 when agent doesn't exist in control DB."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_returning_none()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{uuid4()}/eval-runs/trigger",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_404_on_cross_tenant_idor(self):
        """404 on IDOR: agent exists but belongs to a different tenant."""
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        foreign_agent = _make_ready_agent(other_tenant)
        mock_db = _make_mock_db_returning_agent(foreign_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{foreign_agent.id}/eval-runs/trigger",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_requires_api_key(self):
        """401/403 when X-API-Key header is missing."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/agents/{uuid4()}/eval-runs/trigger",
            )

        assert response.status_code in (401, 403)
