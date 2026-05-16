"""
Unit tests for widget routes in app.api.v1.widget.

Tests the HTTP contract for widget endpoints:
    1. GET /widget/{id}/config returns 200 with theming + valid JWT + CORS header
    2. GET /widget/{id}/config returns 404 for unknown agent
    3. POST /widget/{id}/chat with valid Bearer JWT returns 202
    4. POST /widget/{id}/chat with missing/malformed Authorization returns 401
    5. POST /widget/{id}/chat with JWT agent_id claim mismatch returns 401
    6. POST /widget/{id}/chat hits rate limit when redis.incr returns 61 → 429
    7. GET /widget/jobs/{job_id}/events returns 200 with text/event-stream + CORS header
    8. OPTIONS /widget/{id}/config returns 204 with CORS headers

Security coverage:
    T-04-04-01: JWT in config response (sub/agent_id/exp claims)
    T-04-04-02: agent_id claim mismatch → 401
    T-04-04-04: Access-Control-Allow-Origin: * on all widget responses
    T-04-04-06: rate limit 429 on 61st request
    T-04-04-07: SSE endpoint returns event-stream content type
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.api.deps import get_async_db, get_async_redis
from app.api.v1.widget import create_widget_jwt
from app.main import app
from app.models.agent import Agent


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_ready_agent() -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = uuid4()
    agent.name = "Test Agent"
    agent.status = "ready"
    agent.deleted_at = None
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_mock_db_with_agent(agent: Agent):
    """Mock async DB returning *agent* for any lookup, handling job creation."""
    from app.models.job import Job

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = agent
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    _job_id = uuid4()

    async def _refresh(obj):
        if isinstance(obj, Job):
            obj.id = _job_id

    mock_session.refresh = AsyncMock(side_effect=_refresh)
    return mock_session, _job_id


def _make_mock_db_no_agent():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _make_mock_redis(incr_return_value: int = 1):
    """AsyncMock Redis that returns *incr_return_value* from .incr()."""
    r = AsyncMock()
    r.incr = AsyncMock(return_value=incr_return_value)
    r.expire = AsyncMock()
    r.aclose = AsyncMock()
    r.ping.return_value = True
    return r


# ---------------------------------------------------------------------------
# Test 1: GET /config returns 200 with theming, JWT, and CORS header
# ---------------------------------------------------------------------------


class TestWidgetConfig200:
    async def test_get_config_returns_200_with_theming_jwt_and_cors(self):
        """GET /widget/{id}/config → 200 with theming dict, valid JWT, and CORS header."""
        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)

        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/widget/{agent.id}/config")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert "theming" in body
        assert "primary_color" in body["theming"]
        assert "jwt" in body
        assert "agent_id" in body
        assert "name" in body

        # Validate JWT claim
        from jose import jwt as jose_jwt
        from app.core.config import settings
        claims = jose_jwt.decode(body["jwt"], settings.JWT_SECRET, algorithms=["HS256"])
        assert claims["agent_id"] == str(agent.id)
        assert claims["sub"] == "widget"

        # CORS header
        assert response.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Test 2: GET /config returns 404 for unknown agent
# ---------------------------------------------------------------------------


class TestWidgetConfig404:
    async def test_get_config_returns_404_for_unknown_agent(self):
        """GET /widget/{id}/config with nonexistent agent → 404."""
        mock_db = _make_mock_db_no_agent()
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/widget/{uuid4()}/config")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test 3: POST /chat with valid Bearer JWT returns 202
# ---------------------------------------------------------------------------


class TestWidgetChatPost202:
    async def test_post_chat_with_valid_jwt_returns_202(self):
        """POST /widget/{id}/chat with valid Bearer JWT → 202."""
        agent = _make_ready_agent()
        mock_db, job_id = _make_mock_db_with_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=1)

        token = create_widget_jwt(str(agent.id))

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch(
                "app.api.v1.widget.run_agent_turn.apply_async"
            ) as mock_dispatch:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/chat",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"message": "Hello from widget"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        body = response.json()
        assert "job_id" in body
        assert body["status"] == "pending"
        assert body["events_url"].startswith("/widget/jobs/")
        assert response.headers.get("access-control-allow-origin") == "*"
        mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: POST /chat with missing or malformed Authorization returns 401
# ---------------------------------------------------------------------------


class TestWidgetChatPost401MissingAuth:
    async def test_post_chat_without_authorization_header_returns_403(self):
        """POST /widget/{id}/chat without Authorization header → 403 (HTTPBearer auto_error)."""
        mock_db = _make_mock_db_no_agent()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/{uuid4()}/chat",
                    json={"message": "Hello"},
                )
        finally:
            app.dependency_overrides.clear()

        # HTTPBearer with auto_error=True returns 403 on missing header
        assert response.status_code in (401, 403)

    async def test_post_chat_with_malformed_token_returns_401(self):
        """POST /widget/{id}/chat with malformed Bearer token → 401."""
        agent_id = uuid4()
        mock_db = _make_mock_db_no_agent()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/{agent_id}/chat",
                    headers={"Authorization": "Bearer totally.not.valid"},
                    json={"message": "Hello"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Test 5: POST /chat with JWT agent_id mismatch returns 401
# ---------------------------------------------------------------------------


class TestWidgetChatPost401AgentIdMismatch:
    async def test_post_chat_with_jwt_agent_id_mismatch_returns_401(self):
        """POST /widget/{id}/chat with token for different agent → 401."""
        agent_id_url = uuid4()
        agent_id_token = uuid4()  # different — mismatch

        token_for_other_agent = create_widget_jwt(str(agent_id_token))
        mock_db = _make_mock_db_no_agent()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/{agent_id_url}/chat",
                    headers={"Authorization": f"Bearer {token_for_other_agent}"},
                    json={"message": "Hello"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 401
        assert "mismatch" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Test 6: Rate limit — redis.incr returning 61 → 429
# ---------------------------------------------------------------------------


class TestWidgetChatPost429RateLimit:
    async def test_post_chat_returns_429_when_rate_limit_exceeded(self):
        """POST /widget/{id}/chat when redis.incr returns 61 → 429."""
        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)
        # Simulate 61st request in the current minute window
        mock_redis = _make_mock_redis(incr_return_value=61)

        token = create_widget_jwt(str(agent.id))

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/{agent.id}/chat",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"message": "Exceeds rate limit"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 429
        assert response.headers.get("retry-after") == "60"


# ---------------------------------------------------------------------------
# Test 7: GET /widget/jobs/{job_id}/events returns 200 + text/event-stream + CORS
# ---------------------------------------------------------------------------


class TestWidgetJobEvents:
    async def test_get_events_returns_200_with_event_stream_and_cors(self):
        """GET /widget/jobs/{id}/events → 200, text/event-stream, CORS header.

        Patches event_generator to an async generator that yields nothing,
        so the SSE response opens and closes immediately without blocking.
        """
        job_id = uuid4()
        mock_db = AsyncMock()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        async def _noop_generator(*args, **kwargs):
            """Async generator that yields nothing — simulates empty event stream."""
            return
            yield  # makes this an async generator function

        try:
            with patch("app.api.v1.widget.event_generator", side_effect=_noop_generator):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    async with client.stream(
                        "GET", f"/widget/jobs/{job_id}/events"
                    ) as response:
                        assert response.status_code == 200
                        content_type = response.headers.get("content-type", "")
                        assert "text/event-stream" in content_type
                        assert response.headers.get("access-control-allow-origin") == "*"
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test 8: OPTIONS /widget/{id}/config returns 204 with CORS headers
# ---------------------------------------------------------------------------


class TestWidgetOptionsPreflight:
    async def test_options_config_returns_204_with_cors_headers(self):
        """OPTIONS /widget/{id}/config → 204 with permissive CORS headers."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(f"/widget/{uuid4()}/config")

        assert response.status_code == 204
        assert response.headers.get("access-control-allow-origin") == "*"
        assert "GET" in response.headers.get("access-control-allow-methods", "")
        assert "Content-Type" in response.headers.get(
            "access-control-allow-headers", ""
        )

    async def test_options_chat_returns_204_with_cors_headers(self):
        """OPTIONS /widget/{id}/chat → 204 with permissive CORS headers."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(f"/widget/{uuid4()}/chat")

        assert response.status_code == 204
        assert response.headers.get("access-control-allow-origin") == "*"

    async def test_options_events_returns_204_with_cors_headers(self):
        """OPTIONS /widget/jobs/{id}/events → 204 with permissive CORS headers."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(f"/widget/jobs/{uuid4()}/events")

        assert response.status_code == 204
        assert response.headers.get("access-control-allow-origin") == "*"
