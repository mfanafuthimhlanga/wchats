"""
Unit tests for GET /health endpoint.

Tests:
    - Both DB and Redis healthy → 200 {"status":"ok","redis":"ok","db":"ok"}
    - Health endpoint requires no authentication (no X-API-Key)
    - DB failure → 200 with {"db":"error"} (graceful degradation)
    - Redis failure → 200 with {"redis":"error"} (graceful degradation)

Uses FastAPI dependency overrides to inject mock DB and Redis clients,
avoiding any real database or Redis connections.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_redis
from app.core.database import get_async_db

# conftest.py sets all required env vars before app import
from app.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_db(raise_on_execute: bool = False):
    """Return an async mock DB session."""
    mock_session = AsyncMock(spec=AsyncSession)
    if raise_on_execute:
        mock_session.execute.side_effect = Exception("DB connection refused")
    else:
        mock_session.execute.return_value = MagicMock()
    return mock_session


def _make_mock_redis(raise_on_ping: bool = False):
    """Return an async mock Redis client."""
    mock_redis = AsyncMock()
    if raise_on_ping:
        mock_redis.ping.side_effect = Exception("Redis connection refused")
    else:
        mock_redis.ping.return_value = True
    return mock_redis


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHealthEndpoint:
    async def test_health_both_ok(self):
        """Both DB and Redis healthy: status=ok, redis=ok, db=ok."""
        mock_db = _make_mock_db()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/health")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"
        assert body["redis"] == "ok"

    async def test_health_no_auth_required(self):
        """GET /health must return 200 even without X-API-Key header."""
        mock_db = _make_mock_db()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # No headers at all
                response = await client.get("/health")
        finally:
            app.dependency_overrides.clear()

        # Health is a public endpoint — must not require API key
        assert response.status_code == 200

    async def test_health_db_failure_returns_200(self):
        """DB failure → still returns HTTP 200 with db:error (graceful degradation)."""
        mock_db = _make_mock_db(raise_on_execute=True)
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/health")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["db"] == "error"
        # Redis should still show ok
        assert body["redis"] == "ok"

    async def test_health_redis_failure_returns_200(self):
        """Redis failure → still returns HTTP 200 with redis:error."""
        mock_db = _make_mock_db()
        mock_redis = _make_mock_redis(raise_on_ping=True)

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/health")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["redis"] == "error"
        # DB should still show ok
        assert body["db"] == "ok"

    async def test_health_both_fail_returns_200(self):
        """Both DB and Redis failing → HTTP 200 with both showing error."""
        mock_db = _make_mock_db(raise_on_execute=True)
        mock_redis = _make_mock_redis(raise_on_ping=True)

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/health")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["db"] == "error"
        assert body["redis"] == "error"

    async def test_health_response_has_status_key(self):
        """Response body always has a 'status' key."""
        mock_db = _make_mock_db()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/health")
        finally:
            app.dependency_overrides.clear()

        assert "status" in response.json()
