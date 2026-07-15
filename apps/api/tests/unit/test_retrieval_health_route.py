"""Unit tests for OPS-08 (21-04): GET /agents/{id}/retrieval-health.

PRE-EXISTING INFRA NOTE (not a regression introduced by this plan):
    `app.main` transitively imports app.api.v1.evals -> app.worker.tasks.runtime.eval
    -> app.services.eval_service -> ragas.metrics.collections -> ragas.llms.base ->
    langchain_community.chat_models.vertexai, which raises ModuleNotFoundError in
    this environment (confirmed present on HEAD before this plan's changes — see
    test_metrics_routes.py's identical note). This test module therefore builds a
    minimal FastAPI app around ONLY app.api.v1.metrics.router (mirrors
    test_metrics_routes.py / test_bench_routes.py's targeted-import pattern)
    instead of importing app.main.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_tenant
from app.api.v1 import metrics as metrics_module
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.tenant import Tenant


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


_NOT_TRACKED_HEALTH = {
    "sample_count": 0,
    "avg_bm25_top_score": "not tracked yet",
    "avg_vector_top_score": "not tracked yet",
    "avg_rrf_top_score": "not tracked yet",
    "avg_rerank_top_score": "not tracked yet",
    "avg_reranker_lift": "not tracked yet",
    "avg_recall_at_k": "not tracked yet",
    "avg_ndcg_at_10": "not tracked yet",
    "avg_mrr": "not tracked yet",
    "avg_cited_chunk_rank": "not tracked yet",
    "avg_retrieved_tokens": "not tracked yet",
    "avg_ctx_window_utilization": "not tracked yet",
    "avg_carried_never_cited_tokens": "not tracked yet",
    "avg_compaction_ratio": "not tracked yet",
    "avg_citation_coverage": "not tracked yet",
    "avg_faithfulness": "not tracked yet",
}

_ZERO_STALENESS = {
    "stale_count": 0,
    "stale_document_ids": [],
    "drift_detected": False,
    "drift_model_counts": {},
    "current_embedding_model": "amazon.titan-embed-text-v2:0",
}

_HEALTHY_WINDOW = {
    "sample_count": 42,
    "avg_bm25_top_score": 0.6,
    "avg_vector_top_score": 0.8,
    "avg_rrf_top_score": 0.9,
    "avg_rerank_top_score": 0.95,
    "avg_reranker_lift": 0.35,
    "avg_recall_at_k": 0.7,
    "avg_ndcg_at_10": 0.85,
    "avg_mrr": 0.5,
    "avg_cited_chunk_rank": 2.0,
    "avg_retrieved_tokens": 300.0,
    "avg_ctx_window_utilization": 0.0015,
    "avg_carried_never_cited_tokens": 100.0,
    "avg_compaction_ratio": 0.75,
    "avg_citation_coverage": 0.6,
    "avg_faithfulness": 0.88,
}


# ---------------------------------------------------------------------------
# Targeted import — a minimal FastAPI app wrapping ONLY the metrics router
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(metrics_module.router, prefix="/api/v1")


class TestGetAgentRetrievalHealthRoute:
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
                response = await client.get(f"/api/v1/agents/{foreign_agent.id}/retrieval-health")
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
                response = await client.get(f"/api/v1/agents/{uuid4()}/retrieval-health")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

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
                response = await client.get(f"/api/v1/agents/{agent.id}/retrieval-health")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_returns_200_with_health_and_staleness_shape(self):
        """Happy path: 200 combining read_retrieval_health + compute_index_staleness_summary."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.metrics.fernet_decrypt", return_value="postgresql://fake/tenantdb"),
                patch(
                    "app.api.v1.metrics.read_retrieval_health",
                    return_value=_HEALTHY_WINDOW,
                ) as mock_health,
                patch(
                    "app.api.v1.metrics.compute_index_staleness_summary",
                    return_value=_ZERO_STALENESS,
                ) as mock_staleness,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(f"/api/v1/agents/{ready_agent.id}/retrieval-health")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["sample_count"] == 42
        assert body["avg_reranker_lift"] == pytest.approx(0.35)
        assert body["avg_citation_coverage"] == pytest.approx(0.6)
        assert body["avg_faithfulness"] == pytest.approx(0.88)
        assert body["index_staleness"] == _ZERO_STALENESS
        mock_health.assert_called_once_with("postgresql://fake/tenantdb", 7)
        mock_staleness.assert_called_once_with("postgresql://fake/tenantdb")

    async def test_empty_window_returns_not_tracked_sentinels_not_fabricated_numbers(self):
        """Zero-row window -> honest "not tracked yet" sentinels, never fabricated numbers."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.metrics.fernet_decrypt", return_value="postgresql://fake/tenantdb"),
                patch(
                    "app.api.v1.metrics.read_retrieval_health",
                    return_value=_NOT_TRACKED_HEALTH,
                ),
                patch(
                    "app.api.v1.metrics.compute_index_staleness_summary",
                    return_value=_ZERO_STALENESS,
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(f"/api/v1/agents/{ready_agent.id}/retrieval-health")
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["sample_count"] == 0
        for key in (
            "avg_bm25_top_score", "avg_reranker_lift", "avg_recall_at_k",
            "avg_ndcg_at_10", "avg_ctx_window_utilization", "avg_compaction_ratio",
            "avg_citation_coverage", "avg_faithfulness",
        ):
            assert body[key] == "not tracked yet", f"{key} was fabricated: {body[key]!r}"
        # A genuinely empty/healthy corpus still reports real (non-sentinel)
        # staleness facts -- zero stale docs is an honest fact, not a sentinel.
        assert body["index_staleness"]["stale_count"] == 0
        assert body["index_staleness"]["drift_detected"] is False

    async def test_window_days_query_param_forwarded_to_health_reader(self):
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db = _make_mock_db_returning_agent(ready_agent)

        _test_app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        _test_app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.metrics.fernet_decrypt", return_value="postgresql://fake/tenantdb"),
                patch(
                    "app.api.v1.metrics.read_retrieval_health",
                    return_value={**_NOT_TRACKED_HEALTH, "sample_count": 0, "window_days": 30},
                ) as mock_health,
                patch(
                    "app.api.v1.metrics.compute_index_staleness_summary",
                    return_value=_ZERO_STALENESS,
                ),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/retrieval-health?window_days=30"
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 200
        mock_health.assert_called_once_with("postgresql://fake/tenantdb", 30)
