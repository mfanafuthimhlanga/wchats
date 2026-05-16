"""
Unit tests for PATCH /agents/{agent_id} soul update endpoint.

Tests:
    1. test_patch_agent_soul_full_update — PATCH with all 4 soul fields → 200, values updated
    2. test_patch_agent_soul_partial_update — PATCH with only soul_voice → only that field changed
    3. test_patch_agent_strips_empty_list_items — soul_do_list with "" → stripped before persist
    4. test_patch_agent_not_owned_returns_404 — agent belongs to different tenant → 404
    5. test_patch_agent_missing_api_key_returns_401 — no X-API-Key header → 401/403
    6. test_patch_agent_empty_name_returns_422 — name="" → 422 (min_length=1)

Uses ASGITransport + AsyncClient + dependency_overrides for db and tenant.
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
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_tenant() -> Tenant:
    """Return a mock Tenant for dependency override."""
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_mock_agent(tenant_id, agent_id=None) -> Agent:
    """Build a mock Agent ORM row with soul_* attributes set."""
    agent = MagicMock(spec=Agent)
    agent.id = agent_id or uuid4()
    agent.tenant_id = tenant_id
    agent.name = "OriginalBot"
    agent.soul_role = "support representative"
    agent.soul_voice = "calm and professional"
    agent.soul_do_list = ["answer FAQs"]
    agent.soul_donot_list = ["share PII"]
    agent.status = "ready"
    agent.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    agent.deleted_at = None
    return agent


def _make_mock_db_with_agent(agent):
    """Mock async DB session that returns the given agent on SELECT."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = agent
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    async def _refresh(obj):
        pass  # no-op; agent state already set via setattr in route

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
class TestPatchAgent:
    async def test_patch_agent_soul_full_update(self):
        """PATCH /agents/{id} with all four soul fields → 200, values updated."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        agent = _make_mock_agent(fake_tenant.id, agent_id)
        mock_db = _make_mock_db_with_agent(agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    f"/agents/{agent_id}",
                    json={
                        "name": "UpdatedBot",
                        "soul_role": "billing specialist",
                        "soul_voice": "empathetic and clear",
                        "soul_do_list": ["resolve billing issues", "verify account"],
                        "soul_donot_list": ["share card numbers", "promise refunds"],
                    },
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "UpdatedBot"
        assert body["soul_role"] == "billing specialist"
        assert body["soul_voice"] == "empathetic and clear"
        assert "resolve billing issues" in body["soul_do_list"]
        assert "share card numbers" in body["soul_donot_list"]

    async def test_patch_agent_soul_partial_update(self):
        """PATCH /agents/{id} with only soul_voice → only that field changed, others intact."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        agent = _make_mock_agent(fake_tenant.id, agent_id)
        original_role = agent.soul_role
        mock_db = _make_mock_db_with_agent(agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    f"/agents/{agent_id}",
                    json={"soul_voice": "warm and reassuring"},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        # soul_voice was patched
        assert body["soul_voice"] == "warm and reassuring"
        # soul_role was NOT in the body → must remain unchanged
        assert body["soul_role"] == original_role

    async def test_patch_agent_strips_empty_list_items(self):
        """Empty-string items in soul_do_list are stripped before persist."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        agent = _make_mock_agent(fake_tenant.id, agent_id)
        mock_db = _make_mock_db_with_agent(agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    f"/agents/{agent_id}",
                    json={
                        "soul_do_list": ["valid item", "", "  ", "another item"],
                    },
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        # Empty and whitespace-only items must be stripped
        assert "" not in body["soul_do_list"]
        assert "  " not in body["soul_do_list"]
        assert "valid item" in body["soul_do_list"]
        assert "another item" in body["soul_do_list"]

    async def test_patch_agent_not_owned_returns_404(self):
        """PATCH on agent belonging to a different tenant → 404."""
        fake_tenant = _make_fake_tenant()
        # DB returns None (agent not in this tenant's scope)
        mock_db = _make_mock_db_no_agent()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                other_tenants_agent_id = uuid4()
                response = await client.patch(
                    f"/agents/{other_tenants_agent_id}",
                    json={"soul_voice": "sneaky voice"},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_patch_agent_missing_api_key_returns_401(self):
        """PATCH /agents/{id} without X-API-Key header → 401 or 403.

        FastAPI's APIKeyHeader with auto_error=True raises HTTP 403 if the
        header is absent. The real get_current_tenant raises 401 for invalid
        keys. Either 401 or 403 is acceptable for a missing-header scenario.
        """
        # Do NOT override get_current_tenant — let real auth dependency run
        agent_id = uuid4()

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    f"/agents/{agent_id}",
                    json={"soul_voice": "irrelevant"},
                    # No X-API-Key header provided
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code in (401, 403)

    async def test_patch_agent_empty_name_returns_422(self):
        """PATCH with name='' → 422 (AgentSoulUpdate.name has min_length=1)."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.patch(
                    f"/agents/{agent_id}",
                    json={"name": ""},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422
