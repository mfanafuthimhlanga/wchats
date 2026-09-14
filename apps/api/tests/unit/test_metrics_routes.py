"""Unit tests for OPS-03: metrics_service aggregation + GET /agents/{id}/metrics.

Tests:
    Service layer (app/services/metrics_service.py) — select with `-k service`:
        - _build_metrics_dict computes containment/escalation/p95/cost/csat from
          known aggregate rows.
        - A zero-row window returns NOT_TRACKED sentinels, never fabricated 0.0.
        - compute_agent_metrics wires a mocked psycopg2 cursor through to the
          same pure computation.

    Route layer (app/api/v1/metrics.py):
        GET /api/v1/agents/{agent_id}/metrics

PRE-EXISTING INFRA NOTE (not a regression introduced by this plan):
    `app.main` transitively imports app.api.v1.evals -> app.worker.tasks.runtime.eval
    -> app.services.eval_service -> ragas.metrics.collections -> ragas.llms.base ->
    langchain_community.chat_models.vertexai, which raises ModuleNotFoundError in
    this environment (confirmed present on HEAD before this plan's changes —
    `pytest tests/unit/test_widget_routes.py` fails to collect identically).
    Route tests below therefore build a minimal FastAPI app around ONLY
    app.api.v1.metrics.router (mirrors the targeted-import pattern already
    established in test_bench_routes.py, 21-05) instead of importing `app.main`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import psycopg2
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_tenant
from app.api.v1 import metrics as metrics_module
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services import metrics_service
from app.services.metrics_service import NOT_TRACKED, _build_metrics_dict, compute_agent_metrics

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


class TestBuildMetricsDictService:
    """Pure computation tests for _build_metrics_dict (select with -k service)."""

    def test_build_metrics_dict_service_computes_known_aggregates(self):
        """Known aggregate rows produce the expected containment/escalation/p95/cost."""
        # total_turns, total_conversations, escalated_conversations, conv_with_turns,
        # p95_latency_ms, latency_sample_count, total_cost, cost_conversations
        turn_row = (100, 20, 5, 20, 1850.0, 100, 12.50, 20)
        # total_feedback, csat_sample_count, csat_avg, thumbs_down_count
        feedback_row = (40, 30, 4.2, 6)

        result = _build_metrics_dict(turn_row, feedback_row, window_days=7)

        assert result["escalation_rate"] == pytest.approx(5 / 20)
        assert result["containment"] == pytest.approx(1 - 5 / 20)
        # deflection mirrors containment (no independent human-handoff signal in schema)
        assert result["deflection"] == result["containment"]
        assert result["p95_latency_ms"] == pytest.approx(1850.0)
        assert result["cost_per_session"] == pytest.approx(12.50 / 20)
        assert result["csat_avg"] == pytest.approx(4.2)
        assert result["thumbs_down_rate"] == pytest.approx(6 / 40)
        assert result["sample_size"] == 100
        assert result["window_days"] == 7

    def test_build_metrics_dict_service_zero_row_window_returns_not_tracked(self):
        """Zero underlying rows -> NOT_TRACKED sentinels, never fabricated 0.0."""
        turn_row = (0, 0, 0, 0, None, 0, None, 0)
        feedback_row = (0, 0, None, 0)

        result = _build_metrics_dict(turn_row, feedback_row, window_days=7)

        assert result["escalation_rate"] == NOT_TRACKED
        assert result["containment"] == NOT_TRACKED
        assert result["deflection"] == NOT_TRACKED
        assert result["p95_latency_ms"] == NOT_TRACKED
        assert result["cost_per_session"] == NOT_TRACKED
        assert result["csat_avg"] == NOT_TRACKED
        assert result["thumbs_down_rate"] == NOT_TRACKED
        # sample_size is a literal count — 0 turns happened is an honest fact,
        # not a fabricated ratio, so it is NOT sentinel'd.
        assert result["sample_size"] == 0

    def test_build_metrics_dict_service_partial_data_sentinels_only_missing_metrics(self):
        """Turns exist (containment/escalation/p95/cost real) but zero feedback rows
        (csat/thumbs sentinel'd) — sentinels are per-metric, not all-or-nothing."""
        turn_row = (10, 3, 0, 3, 500.0, 10, 1.0, 3)
        feedback_row = (0, 0, None, 0)

        result = _build_metrics_dict(turn_row, feedback_row, window_days=7)

        assert result["escalation_rate"] == pytest.approx(0.0)
        assert result["containment"] == pytest.approx(1.0)
        assert result["p95_latency_ms"] == pytest.approx(500.0)
        assert result["cost_per_session"] == pytest.approx(1.0 / 3)
        assert result["csat_avg"] == NOT_TRACKED
        assert result["thumbs_down_rate"] == NOT_TRACKED

    def test_compute_agent_metrics_service_wires_mocked_cursor_through(self):
        """compute_agent_metrics connects, runs both queries, and closes the connection."""
        turn_row = (5, 2, 1, 2, 900.0, 5, 2.0, 2)
        feedback_row = (2, 1, 5.0, 0)

        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.execute = MagicMock()
        mock_cursor.fetchone = MagicMock(side_effect=[turn_row, feedback_row])

        mock_conn = MagicMock()
        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        mock_conn.close = MagicMock()

        with patch.object(metrics_service.psycopg2, "connect", return_value=mock_conn) as mock_connect:
            result = compute_agent_metrics("postgresql://fake/tenantdb", window_days=14)

        mock_connect.assert_called_once_with("postgresql://fake/tenantdb", connect_timeout=10)
        mock_conn.close.assert_called_once()
        assert mock_cursor.execute.call_count == 2
        assert result["window_days"] == 14
        assert result["escalation_rate"] == pytest.approx(0.5)
        assert result["csat_avg"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Route tests (Task 2)
# ---------------------------------------------------------------------------

# Targeted import — a minimal FastAPI app wrapping ONLY the metrics router, so
# these tests never import app.main (see PRE-EXISTING INFRA NOTE above).
_test_app = FastAPI()
_test_app.include_router(metrics_module.router, prefix="/api/v1")


class TestGetAgentMetricsRoute:
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
                response = await client.get(f"/api/v1/agents/{foreign_agent.id}/metrics")
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
                response = await client.get(f"/api/v1/agents/{uuid4()}/metrics")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_404_when_neon_connection_string_absent(self):
        """404 (not 500) when agent.neon_connection_string is not provisioned."""
        fake_tenant = _make_fake_tenant()
        agent = _make_ready_agent(fake_tenant)
        agent.neon_connection_string = None
        mock_db = _make_mock_db_returning_agent(agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/agents/{agent.id}/metrics")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_200_with_metrics_shape_from_mocked_service(self):
        """Happy path: 200 with the metrics dict returned unmodified from the service."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        fake_metrics = {
            "containment": 0.9,
            "deflection": 0.9,
            "escalation_rate": 0.1,
            "csat_avg": 4.5,
            "thumbs_down_rate": 0.05,
            "p95_latency_ms": 1200.0,
            "cost_per_session": 0.03,
            "sample_size": 500,
            "window_days": 7,
        }

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.metrics.fernet_decrypt", return_value="postgresql://fake/tenantdb"),
                patch(
                    "app.api.v1.metrics.compute_agent_metrics",
                    return_value=fake_metrics,
                ) as mock_compute,
                patch(
                    "app.api.v1.metrics.read_agent_usage",
                    return_value={"turns": {"count": 5, "cost_per_turn_usd": 0.0042}},
                ) as mock_usage,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(f"/api/v1/agents/{ready_agent.id}/metrics")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json() == {**fake_metrics, "turns": {"count": 5, "cost_per_turn_usd": 0.0042}}
        mock_compute.assert_called_once()
        mock_usage.assert_called_once_with("postgresql://fake/tenantdb", str(ready_agent.id), 7)

    async def test_window_days_query_param_forwarded_to_service(self):
        """?window_days=30 is forwarded to compute_agent_metrics."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.metrics.fernet_decrypt", return_value="postgresql://fake/tenantdb"),
                patch(
                    "app.api.v1.metrics.compute_agent_metrics",
                    return_value={"window_days": 30},
                ) as mock_compute,
                patch("app.api.v1.metrics.read_agent_usage", return_value={"turns": {}}),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/metrics?window_days=30"
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        mock_compute.assert_called_once_with("postgresql://fake/tenantdb", 30)


class TestTheLedgerNeverTakesTheHeadlineDown:
    """A ledger read that fails costs the `turns` key and nothing else.

    The KPIs come from `turn_metrics`; the whole-turn cost comes from
    `model_calls` in the same database and enriches them. A 500 for the Live
    region because the enrichment failed is the tail wagging the dog.
    """

    async def _get_metrics(self, usage_side_effect):
        fake_tenant = _make_fake_tenant()
        agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(agent)
        fake_metrics = {"sample_size": 42, "window_days": 7, "cost_per_session": 0.03}

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            with (
                patch("app.api.v1.metrics.fernet_decrypt", return_value="postgresql://secret@host/db"),
                patch("app.api.v1.metrics.compute_agent_metrics", return_value=dict(fake_metrics)),
                patch("app.api.v1.metrics.read_agent_usage", side_effect=usage_side_effect),
                patch.object(metrics_module, "log") as mock_log,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(f"/api/v1/agents/{agent.id}/metrics")
        finally:
            _test_app.dependency_overrides.clear()
        return response, fake_metrics, mock_log, str(agent.id)

    async def test_a_broken_ledger_read_still_returns_200_with_the_metrics(self):
        response, fake_metrics, _, _ = await self._get_metrics(
            RuntimeError("tenant DB refused the connection")
        )

        assert response.status_code == 200
        assert response.json() == fake_metrics
        assert "turns" not in response.json()

    async def test_the_failure_line_names_the_agent_and_the_exception_and_no_dsn(self):
        response, _, mock_log, agent_id = await self._get_metrics(
            RuntimeError("could not connect to postgresql://secret@host/db")
        )

        assert response.status_code == 200
        event, fields = mock_log.error.call_args.args[0], mock_log.error.call_args.kwargs
        assert event == "agent_metrics.ledger_failed"
        assert fields["agent_id"] == agent_id
        assert fields["window_days"] == 7
        assert fields["error_type"] == "RuntimeError"
        assert fields["detail"] == "the whole-turn cost is omitted rather than reported as zero"
        assert "conn_str" not in fields

    async def test_a_tenant_db_without_the_ledger_table_warns_and_omits_turns(self):
        """A tenant DB older than alembic_tenant 0019 has no model_calls at all."""
        response, fake_metrics, mock_log, agent_id = await self._get_metrics(
            psycopg2.errors.UndefinedTable('relation "model_calls" does not exist')
        )

        assert response.status_code == 200
        assert response.json() == fake_metrics
        mock_log.error.assert_not_called()
        event, fields = mock_log.warning.call_args.args[0], mock_log.warning.call_args.kwargs
        assert event == "agent_metrics.ledger_absent"
        assert fields["agent_id"] == agent_id
        assert fields["window_days"] == 7
        assert fields["detail"] == (
            "tenant DB predates alembic_tenant 0019, so no turn cost is reported"
        )


# ---------------------------------------------------------------------------
# Route layer: GET /agents/{agent_id}/usage
# ---------------------------------------------------------------------------


class TestGetAgentUsageRoute:
    async def test_returns_404_on_cross_tenant_idor(self):
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        foreign_agent = _make_ready_agent(other_tenant)
        mock_db = _make_mock_db_returning_agent(foreign_agent)
        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            with patch("app.api.v1.metrics.read_agent_usage") as mock_usage:
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(f"/api/v1/agents/{foreign_agent.id}/usage")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404
        mock_usage.assert_not_called()

    async def test_returns_404_when_neon_connection_string_absent(self):
        fake_tenant = _make_fake_tenant()
        agent = _make_ready_agent(fake_tenant)
        agent.neon_connection_string = None
        mock_db = _make_mock_db_returning_agent(agent)
        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/agents/{agent.id}/usage")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404
        assert response.json()["detail"] == "Agent database not provisioned"

    async def test_returns_the_usage_dict_and_forwards_window_days(self):
        fake_tenant = _make_fake_tenant()
        agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(agent)
        fake_usage = {
            "window_days": 30,
            "price_version": "2026-08-23.1",
            "total": {"calls": 31, "cost_usd": 0.0209808, "cost_zar": 0.336, "unpriced_calls": 0},
            "turns": {"calls": 31, "cost_usd": 0.0209808, "cost_zar": 0.336, "unpriced_calls": 0,
                      "count": 5, "cost_per_turn_usd": 0.00419616},
            "by_purpose": [], "by_day": [], "by_conversation": [],
        }
        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            with (
                patch("app.api.v1.metrics.fernet_decrypt", return_value="postgresql://fake/tenantdb"),
                patch("app.api.v1.metrics.read_agent_usage", return_value=fake_usage) as mock_usage,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(f"/api/v1/agents/{agent.id}/usage?window_days=30")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json() == fake_usage
        mock_usage.assert_called_once_with("postgresql://fake/tenantdb", str(agent.id), 30)

    async def test_window_days_above_ninety_is_422(self):
        fake_tenant = _make_fake_tenant()
        agent = _make_ready_agent(fake_tenant)
        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: _make_mock_db_returning_agent(agent)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/agents/{agent.id}/usage?window_days=91")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 422
