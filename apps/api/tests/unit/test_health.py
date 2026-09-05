"""
Unit tests for GET /health endpoint.

Tests:
    - Both DB and Redis healthy → 200 {"status":"ok","redis":"ok","db":"ok"}
    - Health endpoint requires no authentication (no X-API-Key)
    - DB failure → 200 with {"db":"error"} (graceful degradation)
    - Redis failure → 200 with {"redis":"error"} (graceful degradation)
    - A failed probe logs the exception; the body names the type, never the message

Uses FastAPI dependency overrides to inject mock DB and Redis clients,
avoiding any real database or Redis connections.

#142: an invalid credential, an unreachable host, a suspended endpoint and a TLS
mismatch all rendered as the same four characters, and nothing was logged, so
staging said only that something was wrong. The type goes in the body, which is
public; the message and the traceback go to the log, which is not, because a
connection message can carry a DSN.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.api.deps import get_async_redis
from app.core.database import get_async_db
from app.core.logging import configure_logging

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


class InvalidPasswordError(Exception):
    """Stands in for the asyncpg error #142 was chasing on staging."""


# The real message carried the role name; a real DSN would carry more.
SECRET_MESSAGE = "password authentication failed for user 'neondb_owner'"


async def _get_health(mock_db, mock_redis):
    """Call GET /health with both dependencies overridden."""
    app.dependency_overrides[get_async_db] = lambda: mock_db
    app.dependency_overrides[get_async_redis] = lambda: mock_redis
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get("/health")
    finally:
        app.dependency_overrides.clear()


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


# ---------------------------------------------------------------------------
# A failed probe is diagnosable (#142)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_db_failure_logs_the_type_the_message_and_the_traceback():
    """The log is what turns "error" into a diagnosis."""
    mock_db = _make_mock_db()
    mock_db.execute.side_effect = InvalidPasswordError(SECRET_MESSAGE)

    with capture_logs() as logs:
        await _get_health(mock_db, _make_mock_redis())

    entries = [e for e in logs if e["event"] == "health.db_probe_failed"]
    assert len(entries) == 1, f"expected one db failure log line, got {logs}"
    assert entries[0]["log_level"] == "warning"
    assert entries[0]["error_type"] == "InvalidPasswordError"
    assert entries[0]["error"] == SECRET_MESSAGE
    assert entries[0]["exc_info"] is True


@pytest.mark.asyncio
async def test_a_redis_failure_logs_the_type_the_message_and_the_traceback():
    mock_redis = _make_mock_redis()
    mock_redis.ping.side_effect = InvalidPasswordError(SECRET_MESSAGE)

    with capture_logs() as logs:
        await _get_health(_make_mock_db(), mock_redis)

    entries = [e for e in logs if e["event"] == "health.redis_probe_failed"]
    assert len(entries) == 1, f"expected one redis failure log line, got {logs}"
    assert entries[0]["log_level"] == "warning"
    assert entries[0]["error_type"] == "InvalidPasswordError"
    assert entries[0]["error"] == SECRET_MESSAGE
    assert entries[0]["exc_info"] is True


@pytest.mark.asyncio
async def test_a_healthy_probe_logs_nothing():
    with capture_logs() as logs:
        await _get_health(_make_mock_db(), _make_mock_redis())

    assert [e["event"] for e in logs if e["event"].startswith("health.")] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", ["db", "redis"])
async def test_the_body_names_the_exception_type_and_never_its_message(probe):
    """The type is a diagnosis; the message can carry a DSN, so it stays out."""
    mock_db = _make_mock_db()
    mock_redis = _make_mock_redis()
    if probe == "db":
        mock_db.execute.side_effect = InvalidPasswordError(SECRET_MESSAGE)
    else:
        mock_redis.ping.side_effect = InvalidPasswordError(SECRET_MESSAGE)

    response = await _get_health(mock_db, mock_redis)

    assert response.status_code == 200
    assert response.json()[probe] == "error"
    assert response.json()[f"{probe}_error"] == "InvalidPasswordError"
    assert SECRET_MESSAGE not in response.text
    assert "neondb_owner" not in response.text


@pytest.mark.asyncio
async def test_a_healthy_probe_carries_no_error_key():
    response = await _get_health(_make_mock_db(), _make_mock_redis())

    assert response.json() == {"status": "ok", "redis": "ok", "db": "ok"}


def test_the_log_pipeline_renders_a_traceback_for_exc_info():
    """exc_info is decoration unless format_exc_info is in the chain.

    The handler passes exc_info=True. Without the processor, JSONRenderer emits
    the literal `"exc_info": true` and the traceback is gone, so this pins the
    chain rather than the call site.
    """
    saved = structlog.get_config()
    try:
        configure_logging("INFO")
        processors = structlog.get_config()["processors"]

        assert structlog.processors.format_exc_info in processors
        assert processors.index(structlog.processors.format_exc_info) < len(
            processors
        ) - 1, "format_exc_info must run before the renderer"

        try:
            raise InvalidPasswordError(SECRET_MESSAGE)
        except InvalidPasswordError:
            rendered = structlog.processors.format_exc_info(
                None, "warning", {"event": "health.db_probe_failed", "exc_info": True}
            )

        assert "exc_info" not in rendered
        assert "Traceback (most recent call last)" in rendered["exception"]
        assert "InvalidPasswordError" in rendered["exception"]
    finally:
        structlog.configure(**saved)
