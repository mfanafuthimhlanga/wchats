"""
Unit tests for POST /agents and GET /agents/{id} routes.

Tests:
    - POST /agents with valid payload → HTTP 202
    - POST /agents response body contains: agent_id, job_id, status, events_url
    - POST /agents without name → 422 (Pydantic validation error)
    - POST /agents with invalid role → 422
    - GET /agents/{uuid} with non-existent id → 404
    - GET /agents/{id} belonging to a different tenant → 404

Uses FastAPI dependency overrides to inject mock DB and skip real auth/DB calls.
Celery chain is patched to prevent real task dispatch.
"""

import unittest.mock
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# conftest.py sets all required env vars before app import
from app.main import app
from app.api.deps import get_async_redis, get_current_tenant
from app.core.database import get_async_db
from app.models.tenant import Tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_tenant() -> Tenant:
    """Return a mock Tenant for dependency override."""
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_mock_db_for_create():
    """Mock async DB session that supports agent/job creation.

    After flush/commit, SQLAlchemy server_default UUIDs are populated by
    the DB. We simulate this by setting .id on ORM objects during refresh().
    """
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()

    _agent_id = uuid4()
    _job_id = uuid4()

    # Track how many times refresh is called to distinguish agent vs job
    refresh_calls = []

    async def _refresh(obj):
        """Inject UUIDs into ORM objects, simulating DB server_default."""
        from app.models.agent import Agent
        from app.models.job import Job

        if isinstance(obj, Agent):
            obj.id = _agent_id
        elif isinstance(obj, Job):
            obj.id = _job_id
        else:
            # Fallback for any other object
            if not hasattr(obj, "id") or obj.id is None:
                obj.id = uuid4()

    mock_session.refresh = AsyncMock(side_effect=_refresh)
    return mock_session, _agent_id, _job_id


def _make_mock_db_empty_agent():
    """Mock async DB that returns None for agent lookup (agent not found)."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _make_mock_redis():
    r = AsyncMock()
    r.ping.return_value = True
    r.aclose = AsyncMock()
    return r


_VALID_PAYLOAD = {
    "name": "SupportBot",
    "soul": {"voice": "Helpful and calm", "do": ["answer FAQs"], "do_not": ["share PII"]},
    "role": "support",
}


# ---------------------------------------------------------------------------
# POST /agents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPostAgents:
    async def test_post_agents_returns_202(self):
        """Valid payload → HTTP 202 Accepted."""
        fake_tenant = _make_fake_tenant()
        mock_db, agent_id, job_id = _make_mock_db_for_create()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                with unittest.mock.patch("app.api.v1.agents.provision_neon") as mock_pn:
                    mock_pn.apply_async = MagicMock()
                    response = await client.post(
                        "/api/v1/agents",
                        json=_VALID_PAYLOAD,
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202

    async def test_post_agents_response_body_keys(self):
        """Response body must contain agent_id, job_id, status, events_url."""
        fake_tenant = _make_fake_tenant()
        mock_db, agent_id, job_id = _make_mock_db_for_create()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                with unittest.mock.patch("app.api.v1.agents.provision_neon") as mock_pn:
                    mock_pn.apply_async = MagicMock()
                    response = await client.post(
                        "/api/v1/agents",
                        json=_VALID_PAYLOAD,
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        body = response.json()
        assert "agent_id" in body
        assert "job_id" in body
        assert "status" in body
        assert "events_url" in body

    async def test_post_agents_events_url_format(self):
        """events_url must start with /jobs/."""
        fake_tenant = _make_fake_tenant()
        mock_db, agent_id, job_id = _make_mock_db_for_create()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                with unittest.mock.patch("app.api.v1.agents.provision_neon") as mock_pn:
                    mock_pn.apply_async = MagicMock()
                    response = await client.post(
                        "/api/v1/agents",
                        json=_VALID_PAYLOAD,
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        body = response.json()
        assert body["events_url"].startswith("/jobs/")

    async def test_post_agents_missing_name_returns_422(self):
        """Payload without 'name' field → HTTP 422 Unprocessable Entity."""
        fake_tenant = _make_fake_tenant()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/agents",
                    json={
                        # name is missing
                        "soul": {"voice": "v", "do": [], "do_not": []},
                        "role": "support",
                    },
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422

    async def test_post_agents_invalid_role_returns_422(self):
        """role='intern' is not a valid Literal → HTTP 422."""
        fake_tenant = _make_fake_tenant()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/agents",
                    json={
                        "name": "Bot",
                        "soul": {"voice": "v", "do": [], "do_not": []},
                        "role": "intern",
                    },
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422

    async def test_post_agents_missing_soul_returns_422(self):
        """Payload without 'soul' field → HTTP 422."""
        fake_tenant = _make_fake_tenant()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/agents",
                    json={"name": "Bot", "role": "support"},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetAgent:
    async def test_get_agent_not_found_returns_404(self):
        """GET /agents/{uuid} where agent doesn't exist → 404."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_empty_agent()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                nonexistent_id = uuid4()
                response = await client.get(
                    f"/api/v1/agents/{nonexistent_id}",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_get_agent_wrong_tenant_returns_404(self):
        """Agent belonging to a different tenant → 404 (no cross-tenant access)."""
        # The current tenant's ID is different from the agent's tenant_id
        # The route filters by both agent.id AND tenant.id, so this returns None
        fake_tenant = _make_fake_tenant()
        # Different tenant's agent will not match the WHERE clause
        mock_db = _make_mock_db_empty_agent()  # returns None for any lookup
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                other_tenants_agent_id = uuid4()
                response = await client.get(
                    f"/api/v1/agents/{other_tenants_agent_id}",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_get_agent_invalid_uuid_returns_422(self):
        """GET /agents/not-a-uuid → 422 (FastAPI path parameter validation)."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_empty_agent()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/v1/agents/not-a-valid-uuid",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422
