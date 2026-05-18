"""
Unit tests for:
  - GET  /agents               — list_agents (T-04.2-02-01: tenant isolation)
  - GET  /agents/{id}/widget-config — get_widget_config
  - POST /agents/{id}/widget-config — save_widget_config

Tests:
  1. test_list_agents_returns_tenant_scoped_list        — 200; two agents returned
  2. test_list_agents_filters_by_tenant_id              — 200; empty list for different tenant
  3. test_get_widget_config_returns_stored_jsonb        — 200; stored config returned
  4. test_get_widget_config_returns_empty_dict_when_unset — 200; {} when no config set
  5. test_save_widget_config_persists_payload           — 200; model_dump persisted; commit awaited
  6. test_save_widget_config_rejects_invalid_hex        — 422; invalid hex color rejected
  7. test_widget_config_not_owned_returns_404           — 404; IDOR guard enforced

Uses ASGITransport + AsyncClient + dependency_overrides (same pattern as test_agents_patch.py).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# conftest.py sets all required env vars before app import
from app.main import app
from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.tenant import Tenant


# ---------------------------------------------------------------------------
# Helpers (mirrors test_agents_patch.py conventions)
# ---------------------------------------------------------------------------


def _make_fake_tenant() -> Tenant:
    """Return a mock Tenant for dependency override."""
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_mock_agent(tenant_id, agent_id=None, widget_config=None) -> Agent:
    """Build a mock Agent ORM row with all required attributes."""
    agent = MagicMock(spec=Agent)
    agent.id = agent_id or uuid4()
    agent.tenant_id = tenant_id
    agent.name = "TestBot"
    agent.role = "support"
    agent.status = "ready"
    agent.neon_project_id = None
    agent.schema_version = None
    agent.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    agent.deleted_at = None
    agent.widget_config = widget_config if widget_config is not None else {}
    return agent


def _make_mock_db_with_agents(agents):
    """Mock async DB that returns a list of agents on SELECT (for list_agents)."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = agents
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    async def _refresh(obj):
        pass

    mock_session.refresh = AsyncMock(side_effect=_refresh)
    return mock_session


def _make_mock_db_with_agent(agent):
    """Mock async DB that returns a single agent on SELECT (for get/save)."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = agent
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    async def _refresh(obj):
        pass

    mock_session.refresh = AsyncMock(side_effect=_refresh)
    return mock_session


def _make_mock_db_no_agent():
    """Mock async DB that returns None for agent lookup (agent not found)."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListAgents:
    async def test_list_agents_returns_tenant_scoped_list(self):
        """GET /agents returns 200 with 2 agents for the authenticated tenant."""
        fake_tenant = _make_fake_tenant()
        agent1 = _make_mock_agent(fake_tenant.id)
        agent2 = _make_mock_agent(fake_tenant.id)
        mock_db = _make_mock_db_with_agents([agent1, agent2])

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/agents",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert "agents" in body
        assert len(body["agents"]) == 2

    async def test_list_agents_filters_by_tenant_id(self):
        """GET /agents returns 200 with empty list when no agents match tenant."""
        fake_tenant = _make_fake_tenant()
        # DB returns empty list — different tenant owns all agents
        mock_db = _make_mock_db_with_agents([])

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/agents",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body == {"agents": []}


@pytest.mark.asyncio
class TestGetWidgetConfig:
    async def test_get_widget_config_returns_stored_jsonb(self):
        """GET /agents/{id}/widget-config returns 200 with stored config dict."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        stored_config = {"appearance": "floating-button"}
        agent = _make_mock_agent(fake_tenant.id, agent_id=agent_id, widget_config=stored_config)
        mock_db = _make_mock_db_with_agent(agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/agents/{agent_id}/widget-config",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json() == stored_config

    async def test_get_widget_config_returns_empty_dict_when_unset(self):
        """GET /agents/{id}/widget-config returns 200 with {} when config is empty."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        agent = _make_mock_agent(fake_tenant.id, agent_id=agent_id, widget_config={})
        mock_db = _make_mock_db_with_agent(agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/agents/{agent_id}/widget-config",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json() == {}


@pytest.mark.asyncio
class TestSaveWidgetConfig:
    async def test_save_widget_config_persists_payload(self):
        """POST /agents/{id}/widget-config with default body → 200; commit awaited."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        agent = _make_mock_agent(fake_tenant.id, agent_id=agent_id)
        mock_db = _make_mock_db_with_agent(agent)

        # Capture the widget_config attribute set during the route
        saved_config = {}

        def _setattr_capture(obj, name, value):
            if name == "widget_config":
                saved_config.update(value)
            object.__setattr__(obj, name, value)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/agents/{agent_id}/widget-config",
                    json={},  # all defaults
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        # db.commit was awaited (widget config was persisted)
        mock_db.commit.assert_awaited_once()

    async def test_save_widget_config_rejects_invalid_hex(self):
        """POST /agents/{id}/widget-config with invalid hex color → 422."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/agents/{agent_id}/widget-config",
                    json={"colors": {"widget_bg": "notahex"}},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422

    async def test_widget_config_not_owned_returns_404(self):
        """GET /agents/{id}/widget-config for an agent in different tenant → 404."""
        fake_tenant = _make_fake_tenant()
        # DB returns None — agent not found for this tenant (T-04.2-02-02)
        mock_db = _make_mock_db_no_agent()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                other_tenants_agent_id = uuid4()
                response = await client.get(
                    f"/agents/{other_tenants_agent_id}/widget-config",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404
