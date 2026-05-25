"""Unit tests for app.api.v1.observability — M10 alerts routes (OPS-04).

De-xfailed in Phase 10-05. Tests cover:
    test_get_alerts_returns_list    — GET /api/v1/agents/{id}/alerts returns 200 with a list
    test_get_alerts_idor_guard      — wrong tenant key returns 401 or 403
"""

import os
import base64

# Safety: ensure required env vars are present even if conftest is not loaded
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")

import pytest


@pytest.mark.xfail(strict=True, reason="observability routes not yet implemented — de-xfail in 10-05")
@pytest.mark.asyncio
async def test_get_alerts_returns_list():
    """GET /api/v1/agents/{id}/alerts returns 200 with a JSON list."""
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4
    from httpx import ASGITransport, AsyncClient
    from app.api.deps import get_async_db, get_current_tenant
    from app.main import app
    from app.models.agent import Agent
    from app.models.tenant import Tenant

    agent_id = uuid4()
    tenant_id = uuid4()

    mock_tenant = MagicMock(spec=Tenant)
    mock_tenant.id = tenant_id

    mock_agent = MagicMock(spec=Agent)
    mock_agent.id = agent_id
    mock_agent.tenant_id = tenant_id

    mock_db = AsyncMock()
    mock_db.get.return_value = mock_agent
    mock_db.execute.return_value.scalars.return_value.all.return_value = []

    app.dependency_overrides[get_current_tenant] = lambda: mock_tenant
    app.dependency_overrides[get_async_db] = lambda: mock_db

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/agents/{agent_id}/alerts",
                headers={"X-API-Key": "test-key"},
            )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.xfail(strict=True, reason="observability routes not yet implemented — de-xfail in 10-05")
@pytest.mark.asyncio
async def test_get_alerts_idor_guard():
    """Wrong tenant returns 401 or 403 — IDOR guard."""
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4
    from httpx import ASGITransport, AsyncClient
    from app.api.deps import get_async_db, get_current_tenant
    from app.main import app
    from app.models.agent import Agent
    from app.models.tenant import Tenant

    agent_id = uuid4()
    owner_tenant_id = uuid4()
    attacker_tenant_id = uuid4()

    mock_tenant = MagicMock(spec=Tenant)
    mock_tenant.id = attacker_tenant_id  # different from agent's owner

    mock_agent = MagicMock(spec=Agent)
    mock_agent.id = agent_id
    mock_agent.tenant_id = owner_tenant_id  # owned by a different tenant

    mock_db = AsyncMock()
    mock_db.get.return_value = mock_agent

    app.dependency_overrides[get_current_tenant] = lambda: mock_tenant
    app.dependency_overrides[get_async_db] = lambda: mock_db

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/agents/{agent_id}/alerts",
                headers={"X-API-Key": "attacker-key"},
            )
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides.clear()
