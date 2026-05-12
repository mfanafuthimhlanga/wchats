"""
Unit tests for FastAPI authentication dependencies.

Tests:
    - Valid X-API-Key → route returns 202 (tenant resolved successfully)
    - Invalid X-API-Key → 401 Unauthorized
    - Missing X-API-Key → 403 Forbidden (header required by APIKeyHeader)
    - POST /tenants without X-Admin-Key → 403 Forbidden
    - POST /tenants with wrong X-Admin-Key → 403 Forbidden

Authentication approach:
    - For valid-key tests: override get_current_tenant to return a fake Tenant
    - For invalid-key tests: let the real get_current_tenant run against an
      async mock DB that returns no results (simulates empty tenant table)

The real get_current_tenant queries all non-deleted tenants and calls
verify_api_key() on each. An empty query result means no match → 401.
"""

import os
from datetime import datetime, timezone
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


def _make_fake_tenant(name: str = "Test Tenant") -> Tenant:
    """Build a Tenant-like object without hitting the DB."""
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = name
    tenant.deleted_at = None
    return tenant


def _make_mock_db_empty():
    """Return an async mock DB session that returns no tenants on SELECT."""
    mock_session = AsyncMock()
    # scalars() → empty iterable (no tenants found)
    mock_scalars = MagicMock()
    mock_scalars.__iter__ = MagicMock(return_value=iter([]))
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _make_mock_db_with_agent_commit():
    """Return async mock DB that supports flush/commit/refresh for POST /agents."""
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
# Test: valid API key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestValidApiKey:
    async def test_valid_api_key_returns_202(self):
        """POST /agents with overridden get_current_tenant → 202."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_with_agent_commit()
        mock_redis = _make_mock_redis()

        # Override dependency to return a known tenant (skips real auth)
        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                with (
                    MagicMock() as mock_chain_patch
                ):
                    # Patch Celery chain so no real Celery calls happen
                    import unittest.mock
                    with unittest.mock.patch("app.api.v1.agents.chain") as mock_chain:
                        mock_chain.return_value.apply_async = MagicMock()
                        response = await client.post(
                            "/agents",
                            json=_VALID_AGENT_PAYLOAD,
                            headers={"X-API-Key": "vrd_live_somekey"},
                        )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202


# ---------------------------------------------------------------------------
# Test: invalid / missing API key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInvalidApiKey:
    async def test_invalid_api_key_returns_401(self):
        """POST /agents with invalid key → 401 (no matching tenant)."""
        mock_db = _make_mock_db_empty()
        mock_redis = _make_mock_redis()

        # Do NOT override get_current_tenant — let it run with empty DB
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/agents",
                    json=_VALID_AGENT_PAYLOAD,
                    headers={"X-API-Key": "vrd_live_wrong_key"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 401

    async def test_missing_api_key_returns_403(self):
        """POST /agents without X-API-Key header → 403 (APIKeyHeader auto_error=True)."""
        mock_db = _make_mock_db_empty()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/agents", json=_VALID_AGENT_PAYLOAD)
        finally:
            app.dependency_overrides.clear()

        # FastAPI's APIKeyHeader with auto_error=True raises HTTP 403 if header absent
        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Test: admin key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAdminKey:
    async def test_admin_key_required_for_tenants(self):
        """POST /tenants without X-Admin-Key → 401 or 403 (header required)."""
        mock_db = _make_mock_db_with_agent_commit()

        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/tenants",
                    json={"name": "Test Corp"},
                    # No X-Admin-Key header
                )
        finally:
            app.dependency_overrides.clear()

        # FastAPI's APIKeyHeader returns 401 when header is absent (auto_error=True)
        assert response.status_code in (401, 403)

    async def test_wrong_admin_key_returns_403(self):
        """POST /tenants with wrong X-Admin-Key → 403."""
        mock_db = _make_mock_db_with_agent_commit()

        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/tenants",
                    json={"name": "Test Corp"},
                    headers={"X-Admin-Key": "wrong_admin_key"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 403
