"""
Unit tests for FastAPI authentication dependencies.

Tests:
    - Valid X-API-Key → route returns 202 (tenant resolved successfully)
    - Invalid X-API-Key → 401 Unauthorized
    - Missing auth header → 401 (dual-auth dependency — neither Bearer nor X-API-Key present)
    - POST /api/v1/tenants without X-Admin-Key → 401 or 403 (header required)
    - POST /api/v1/tenants with wrong X-Admin-Key → 403 Forbidden

Authentication approach:
    - For valid-key tests: override get_current_tenant to return a fake Tenant
    - For invalid-key tests: let the real get_current_tenant run against an
      async mock DB that returns no results (simulates empty tenant table)

The real get_current_tenant tries Bearer (Clerk JWT) first, then X-API-Key.
With auto_error=False on both schemes, a missing Bearer falls through to
X-API-Key; if X-API-Key is also absent or invalid, the dependency raises 401.

Route prefix fix: all /agents and /tenants routes are registered with
prefix="/api/v1" in app.main. Tests must use /api/v1/... paths.
"""

import unittest.mock
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_async_redis, get_current_tenant
from app.core.database import get_async_db

# conftest.py sets all required env vars before app import
from app.main import app
from app.models.tenant import Tenant

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_tenant(name: str = "Test Tenant") -> Tenant:
    """Build a Tenant-like object without hitting the DB."""
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = name
    tenant.deleted_at = None
    return tenant


def _make_mock_db_empty():
    """Return an async mock DB session that returns no tenants on SELECT.

    get_current_tenant with auto_error=False on both HTTPBearer and
    APIKeyHeader: Bearer absent → bearer=None; X-API-Key present but no
    matching tenant row → falls through to 401.
    """
    mock_session = AsyncMock()
    # scalars().first() must return None to simulate no tenant found
    mock_scalars = MagicMock()
    mock_scalars.first = MagicMock(return_value=None)
    # scalars().__iter__ for the legacy fallback scan
    mock_scalars.__iter__ = MagicMock(return_value=iter([]))
    mock_result = MagicMock()
    mock_result.scalars = MagicMock(return_value=mock_scalars)
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _make_mock_db_with_agent_commit():
    """Return async mock DB that supports flush/commit/refresh for POST /api/v1/agents."""
    mock_session = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.add = MagicMock()

    async def _refresh(obj):
        """Inject UUIDs into ORM objects, simulating DB server_default."""
        from app.models.agent import Agent
        from app.models.job import Job
        if isinstance(obj, Agent):
            obj.id = uuid4()
        elif isinstance(obj, Job):
            obj.id = uuid4()

    mock_session.refresh = AsyncMock(side_effect=_refresh)
    return mock_session


def _make_mock_redis():
    """Return async mock Redis."""
    r = AsyncMock()
    r.ping.return_value = True
    r.aclose = AsyncMock()
    return r


_VALID_AGENT_PAYLOAD = {
    "name": "TestAgent",
    "soul": {"voice": "Friendly", "do": ["help"], "do_not": ["lie"]},
    "role": "support",
}


# ---------------------------------------------------------------------------
# Test: valid API key (dependency override — skips real auth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestValidApiKey:
    async def test_valid_api_key_returns_202(self):
        """POST /api/v1/agents with overridden get_current_tenant → 202.

        Route registered at /api/v1/agents (prefix="/api/v1" in app.main).
        Patch provision_neon.apply_async (not `chain` — chain was removed;
        the route now calls provision_neon.apply_async directly).
        """
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_with_agent_commit()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                with unittest.mock.patch(
                    "app.api.v1.agents.provision_neon"
                ) as mock_pn:
                    mock_pn.apply_async = MagicMock()
                    response = await client.post(
                        "/api/v1/agents",
                        json=_VALID_AGENT_PAYLOAD,
                        headers={"X-API-Key": "vrd_live_somekey"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202


# ---------------------------------------------------------------------------
# Test: invalid / missing API key (real get_current_tenant, empty DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInvalidApiKey:
    async def test_invalid_api_key_returns_401(self):
        """POST /api/v1/agents with invalid X-API-Key → 401.

        get_current_tenant is NOT overridden. Bearer is absent (no Authorization
        header) so bearer=None. X-API-Key is present but DB returns no matching
        tenant → falls through to `raise HTTPException(status_code=401)`.
        """
        mock_db = _make_mock_db_empty()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/agents",
                    json=_VALID_AGENT_PAYLOAD,
                    headers={"X-API-Key": "vrd_live_wrong_key"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 401

    async def test_missing_auth_returns_401(self):
        """POST /api/v1/agents with no auth header at all → 401.

        Both HTTPBearer(auto_error=False) and APIKeyHeader(auto_error=False)
        return None when their headers are absent. The dependency then raises
        HTTPException(status_code=401, detail="Authentication required").
        """
        mock_db = _make_mock_db_empty()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/agents", json=_VALID_AGENT_PAYLOAD
                )
        finally:
            app.dependency_overrides.clear()

        # Both schemes have auto_error=False; missing headers → 401 from
        # the final `raise HTTPException(status_code=401)` in get_current_tenant.
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Test: admin key for POST /api/v1/tenants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAdminKey:
    async def test_admin_key_required_for_tenants(self):
        """POST /api/v1/tenants without X-Admin-Key → 401 or 403.

        Route registered at /api/v1/tenants (prefix="/api/v1" in app.main).
        _admin_key_header has auto_error=True → raises 401/403 when header absent.
        """
        mock_db = _make_mock_db_with_agent_commit()

        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/tenants",
                    json={"name": "Test Corp"},
                    # No X-Admin-Key header
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code in (401, 403)

    async def test_wrong_admin_key_returns_403(self):
        """POST /api/v1/tenants with wrong X-Admin-Key → 403."""
        mock_db = _make_mock_db_with_agent_commit()

        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/tenants",
                    json={"name": "Test Corp"},
                    headers={"X-Admin-Key": "wrong_admin_key"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 403
