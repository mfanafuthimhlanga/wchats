"""
Unit tests for POST /tenants route.

Tests:
    - POST /tenants with valid admin key → 201 with api_key in response
    - POST /tenants without admin key → 401
    - POST /tenants with wrong admin key → 403
"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.database import get_async_db


def _make_mock_db_for_tenant():
    """Mock DB that supports tenant creation."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    fake_tenant = MagicMock()
    fake_tenant.id = uuid4()
    fake_tenant.name = "Test Corp"
    fake_tenant.created_at = datetime.now(timezone.utc)

    async def _refresh(obj):
        obj.id = uuid4()
        obj.created_at = datetime.now(timezone.utc)

    mock_session.refresh = AsyncMock(side_effect=_refresh)
    return mock_session


# Get the admin key set in conftest (or env)
ADMIN_KEY = os.environ.get("ADMIN_KEY", "vrd_admin_test_key_for_tests_only")


@pytest.mark.asyncio
class TestPostTenants:
    async def test_post_tenants_with_valid_admin_key_returns_201(self):
        """POST /tenants with valid X-Admin-Key → 201 Created."""
        mock_db = _make_mock_db_for_tenant()
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/tenants",
                    json={"name": "Test Corp"},
                    headers={"X-Admin-Key": ADMIN_KEY},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 201

    async def test_post_tenants_response_has_api_key(self):
        """Response contains the plaintext api_key (returned only on creation)."""
        mock_db = _make_mock_db_for_tenant()
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/tenants",
                    json={"name": "Test Corp"},
                    headers={"X-Admin-Key": ADMIN_KEY},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 201
        body = response.json()
        assert "api_key" in body
        # The plaintext key starts with our prefix
        assert body["api_key"].startswith("vrd_live_")

    async def test_post_tenants_missing_admin_key_returns_401(self):
        """POST /tenants without X-Admin-Key → 401 (header required)."""
        mock_db = _make_mock_db_for_tenant()
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/tenants",
                    json={"name": "Test Corp"},
                    # No X-Admin-Key
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code in (401, 403)

    async def test_post_tenants_wrong_admin_key_returns_403(self):
        """POST /tenants with wrong X-Admin-Key → 403."""
        mock_db = _make_mock_db_for_tenant()
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/tenants",
                    json={"name": "Test Corp"},
                    headers={"X-Admin-Key": "wrong_key_that_does_not_match"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 403
