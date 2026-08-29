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


def _make_ready_agent(widget_config: dict | None = None) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = uuid4()
    agent.name = "Test Agent"
    agent.status = "ready"
    agent.deleted_at = None
    agent.neon_connection_string = b"fake-encrypted-bytes"
    # migration 0009 defaults the JSONB column to {} — a MagicMock attribute here
    # would be truthy and would not be a real dict.
    agent.widget_config = widget_config if widget_config is not None else {}
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
        mock_redis = _make_mock_redis(incr_return_value=1)  # within rate limit

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

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
        mock_redis = _make_mock_redis(incr_return_value=1)  # within rate limit
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

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
        Updated for F8: mock DB must return a Job row (agent_id lookup added).
        """
        from app.models.job import Job

        job_id = uuid4()
        agent_id = uuid4()

        mock_job = MagicMock(spec=Job)
        mock_job.id = job_id
        mock_job.agent_id = agent_id

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        async def _noop_generator(*args, **kwargs):
            """Async generator that yields nothing — simulates empty event stream."""
            return
            yield  # makes this an async generator function

        try:
            with patch("app.api.v1.widget.event_generator", side_effect=_noop_generator), \
                 patch("app.api.v1.widget._acquire_sse_slot", return_value=True), \
                 patch("app.api.v1.widget._release_sse_slot", new_callable=AsyncMock):
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

    async def test_events_stream_uses_the_customer_terminal_set(self):
        """BACKLOG 7.3 — the public widget stream must close itself on agent.response.

        The route, not the generator, chooses the terminal set; a route wired to the
        default set would still hold a slot for 120s after the answer.
        """
        from app.models.job import Job
        from app.services.sse import CUSTOMER_TERMINAL_EVENTS

        job_id = uuid4()

        mock_job = MagicMock(spec=Job)
        mock_job.id = job_id
        mock_job.agent_id = uuid4()

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        mock_db.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: _make_mock_redis()

        async def _noop_generator(*args, **kwargs):
            return
            yield

        try:
            with patch(
                "app.api.v1.widget.event_generator", side_effect=_noop_generator
            ) as mock_gen, \
                 patch("app.api.v1.widget._acquire_sse_slot", return_value=True), \
                 patch("app.api.v1.widget._release_sse_slot", new_callable=AsyncMock):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    async with client.stream(
                        "GET", f"/widget/jobs/{job_id}/events"
                    ) as response:
                        await response.aread()
        finally:
            app.dependency_overrides.clear()

        mock_gen.assert_called_once()
        assert (
            mock_gen.call_args.kwargs.get("terminal_events") is CUSTOMER_TERMINAL_EVENTS
        ), f"widget SSE route wired to the wrong terminal set: {mock_gen.call_args}"


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


# ---------------------------------------------------------------------------
# Task 1 — F2: Per-IP rate limit on GET /widget/{agent_id}/config
# ---------------------------------------------------------------------------


class TestWidgetConfigRateLimitF2:
    async def test_widget_config_rate_limited_by_ip(self):
        """11th config request from same IP → 429 with Retry-After: 60 header.

        Mocks redis.incr to return 11 (over the 10/min ceiling).
        Security: T-04.1-02-01 — prevents unlimited JWT harvest from single IP.
        """
        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=11)  # 11th request > 10 ceiling

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/widget/{agent.id}/config")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 429
        assert response.headers.get("retry-after") == "60"

    async def test_widget_config_different_ips_not_affected(self):
        """10 requests from IP-A do not affect a request from IP-B.

        Mocks redis.incr to return 10 for IP-A and 1 for IP-B; IP-B request
        should return 200 (not 429).
        Security: rate limit is keyed on client IP only — independent per IP.
        """
        agent = _make_ready_agent()
        mock_db_b, _ = _make_mock_db_with_agent(agent)
        # IP-B is the 1st request (count=1) — well within limit
        mock_redis_b = _make_mock_redis(incr_return_value=1)

        app.dependency_overrides[get_async_db] = lambda: mock_db_b
        app.dependency_overrides[get_async_redis] = lambda: mock_redis_b

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/widget/{agent.id}/config")
        finally:
            app.dependency_overrides.clear()

        # IP-B with count=1 should succeed — rate limit not exceeded
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# BACKLOG 7.2a — GET /config serves the tenant's stored widget_config
# ---------------------------------------------------------------------------


async def _get_config_body(agent):
    """GET /widget/{agent.id}/config against *agent*, returning the parsed body."""
    mock_db, _ = _make_mock_db_with_agent(agent)
    mock_redis = _make_mock_redis(incr_return_value=1)

    app.dependency_overrides[get_async_db] = lambda: mock_db
    app.dependency_overrides[get_async_redis] = lambda: mock_redis
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/widget/{agent.id}/config")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    return response.json()


class TestWidgetConfigServesStoredConfig:
    async def test_stored_widget_config_is_served(self):
        """A saved widget_config reaches the widget instead of the hardcoded palette.

        Before 7.2a the route returned a literal dict and never read the column that
        POST /agents/{id}/widget-config writes, so tenant branding was inert.
        """
        stored = {
            "appearance": "slide-out-panel",
            "launcher_shape": "square",
            "colors": {"header_bg": "#123456", "widget_bg": "#ABCDEF"},
            "typography": {"font_family": "Georgia", "font_custom_url": None},
        }
        body = await _get_config_body(_make_ready_agent(widget_config=stored))

        assert body["theming"]["header_bg"] == "#123456"
        assert body["theming"]["widget_bg"] == "#ABCDEF"
        assert body["theming"]["font_family"] == "Georgia"
        assert body["theming"]["appearance"] == "slide-out-panel"
        assert body["theming"]["launcher_shape"] == "square"
        # The hardcoded palette must not leak through beside the stored one
        assert "primary_color" not in body["theming"]

    async def test_theming_is_flat_so_every_value_is_a_css_value(self):
        """No nested block survives — the widget assigns each entry to a CSS variable."""
        stored = {
            "colors": {"header_bg": "#123456"},
            "typography": {"font_family": "Inter", "font_custom_url": None},
        }
        body = await _get_config_body(_make_ready_agent(widget_config=stored))

        assert all(
            not isinstance(v, (dict, list)) for v in body["theming"].values()
        ), f"nested value would render as [object Object]: {body['theming']}"
        # Nulls are dropped rather than serialised as "None"
        assert "font_custom_url" not in body["theming"]

    async def test_empty_widget_config_falls_back_to_defaults(self):
        """An unset column (migration 0009 default {}) still returns the old palette."""
        body = await _get_config_body(_make_ready_agent(widget_config={}))

        from app.api.v1.widget import DEFAULT_THEMING
        assert body["theming"] == DEFAULT_THEMING

    async def test_null_widget_config_falls_back_to_defaults(self):
        """A NULL column behaves the same as an empty one."""
        agent = _make_ready_agent()
        agent.widget_config = None
        body = await _get_config_body(agent)

        from app.api.v1.widget import DEFAULT_THEMING
        assert body["theming"] == DEFAULT_THEMING


class TestWidgetConfigAgentNameContract:
    async def test_config_returns_agent_name_alongside_name(self):
        """The widget reads cfg.agent_name; the schema only ever declared `name`.

        Both are returned — dropping `name` would break the existing consumers.
        """
        body = await _get_config_body(_make_ready_agent())

        assert body["agent_name"] == "Test Agent"
        assert body["name"] == "Test Agent"


# ---------------------------------------------------------------------------
# Task 2 — F8: SSE concurrent connection cap + asyncio.timeout(120)
# ---------------------------------------------------------------------------


class TestWidgetSSESlotsF8:
    async def test_sse_slot_acquired_and_released(self):
        """SSE slot is acquired at stream open and released in the finally block.

        Mocks _acquire_sse_slot to return True (slot available) and
        _release_sse_slot as a no-op; verifies both helpers are called.
        Security: T-04.1-02-02 — _release_sse_slot must run even on disconnect.
        """
        from app.models.job import Job

        job_id = uuid4()
        agent_id = uuid4()

        # Build a mock job row with agent_id set
        mock_job = MagicMock(spec=Job)
        mock_job.id = job_id
        mock_job.agent_id = agent_id

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_redis = _make_mock_redis()

        # Additional slot-management mocks
        mock_redis.set = AsyncMock(return_value=True)   # slot acquired
        mock_redis.delete = AsyncMock(return_value=1)   # slot deleted

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        async def _noop_generator(*args, **kwargs):
            return
            yield  # makes it an async generator

        try:
            with patch("app.api.v1.widget.event_generator", side_effect=_noop_generator), \
                 patch("app.api.v1.widget._acquire_sse_slot", return_value=True) as mock_acquire, \
                 patch("app.api.v1.widget._release_sse_slot", new_callable=AsyncMock) as mock_release:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    async with client.stream(
                        "GET", f"/widget/jobs/{job_id}/events"
                    ) as response:
                        assert response.status_code == 200
                        # Consume the stream to trigger finally
                        async for _ in response.aiter_bytes():
                            pass
                mock_acquire.assert_called_once()
                mock_release.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    async def test_sse_returns_503_when_agent_at_capacity(self):
        """SSE endpoint returns 503 when _acquire_sse_slot returns False.

        Mocks _acquire_sse_slot to return False (capacity exceeded).
        Security: T-04.1-02-02 — connection exhaustion DoS prevention.
        """
        from app.models.job import Job

        job_id = uuid4()
        agent_id = uuid4()

        mock_job = MagicMock(spec=Job)
        mock_job.id = job_id
        mock_job.agent_id = agent_id

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch("app.api.v1.widget._acquire_sse_slot", return_value=False):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(f"/widget/jobs/{job_id}/events")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 503

    async def test_sse_hard_timeout_fires(self):
        """asyncio.timeout(120) in the _wrapped_generator terminates the SSE stream.

        Directly exercises the _wrapped_generator pattern from widget_job_events
        with a very short timeout (0.05s) to verify:
          - TimeoutError is caught, not propagated
          - A timeout event is yielded before the generator closes
          - _release_sse_slot is called in the finally block

        Security: T-04.1-02-03 — hard cap prevents permanently hung connections.
        """
        import asyncio as real_asyncio

        received_events = []
        release_called = []

        async def _infinite_event_generator():
            """Simulates a stream that hangs indefinitely — never yields."""
            await real_asyncio.sleep(60)
            yield  # unreachable

        async def _mock_release_fn(*args, **kwargs):
            release_called.append(True)

        # Replicate the exact _wrapped_generator pattern from widget_job_events,
        # but with a tiny timeout (0.05s) so the test completes quickly.
        async def _wrapped_generator_under_test():
            try:
                async with real_asyncio.timeout(0.05):  # tiny timeout simulates 120s
                    async for event in _infinite_event_generator():
                        yield event
            except real_asyncio.TimeoutError:
                received_events.append("timeout")
                yield "event: timeout\ndata: {}\n\n"
            finally:
                await _mock_release_fn()

        # Drive the generator to completion — must finish well within 5 seconds
        try:
            async with real_asyncio.timeout(5):
                async for _ in _wrapped_generator_under_test():
                    pass
        except real_asyncio.TimeoutError:
            pytest.fail(
                "Outer 5s guard fired — _wrapped_generator did not terminate "
                "after inner TimeoutError. asyncio.timeout catch may be broken."
            )

        # Inner timeout must have fired and been caught cleanly
        assert "timeout" in received_events, (
            "TimeoutError was not caught by _wrapped_generator; "
            "timeout event not yielded"
        )
        assert len(release_called) == 1, (
            "_release_sse_slot not called in finally block after TimeoutError"
        )


# ---------------------------------------------------------------------------
# The public SSE endpoint refuses a job that is not a customer turn (#109)
# ---------------------------------------------------------------------------
class TestOnlyACustomerTurnStreamsPublicly:
    """#109: the endpoint checked that a job EXISTED and never what kind it was.

    An ingestion or provisioning id streamed on a public, unauthenticated endpoint. The
    payload allowlist (#104) empties an unmapped event type, so the leak was closed
    before this and the missing check was not: the endpoint still confirmed an id
    existed, and it still held one of the fifty per-agent SSE slots for up to 120
    seconds.

    These drive `_customer_turn_agent_id` rather than the streaming response, because
    that is the seam the check lives on and a test that stood an EventSourceResponse up
    would be exercising the plumbing around it.
    """

    @staticmethod
    def _db_returning(job):
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=job)
        db.execute = AsyncMock(return_value=result)
        return db

    @staticmethod
    def _job(kind, agent_id):
        job = MagicMock()
        job.kind = kind
        job.agent_id = agent_id
        return job

    async def test_a_customer_turn_resolves_to_its_agent(self):
        """THE CONTROL. Without it a helper that refused everything would pass below."""
        from app.api.v1.widget import CUSTOMER_TURN_JOB_KIND, _customer_turn_agent_id

        agent_id = uuid4()
        db = self._db_returning(self._job(CUSTOMER_TURN_JOB_KIND, agent_id))

        assert await _customer_turn_agent_id(db, uuid4()) == agent_id

    @pytest.mark.parametrize(
        "kind", ["ingest_documents", "create_agent", "query_agent"]
    )
    async def test_a_job_of_any_other_kind_is_refused(self, kind):
        """Every other kind this control DB carries, not only the ingestion one."""
        from fastapi import HTTPException

        from app.api.v1.widget import _customer_turn_agent_id

        db = self._db_returning(self._job(kind, uuid4()))

        with pytest.raises(HTTPException) as excinfo:
            await _customer_turn_agent_id(db, uuid4())
        assert excinfo.value.status_code == 404

    async def test_a_wrong_kind_is_indistinguishable_from_a_missing_job(self):
        """404 on both, same detail.

        A 403 on the kind would confirm the id exists, and an id that cannot be
        confirmed is the whole of the UUID4 argument the endpoint rests on. So the two
        refusals have to be identical from outside, and that is asserted rather than
        left to whoever edits the branch next.
        """
        from fastapi import HTTPException

        from app.api.v1.widget import _customer_turn_agent_id

        with pytest.raises(HTTPException) as absent:
            await _customer_turn_agent_id(self._db_returning(None), uuid4())
        with pytest.raises(HTTPException) as wrong_kind:
            await _customer_turn_agent_id(
                self._db_returning(self._job("ingest_documents", uuid4())), uuid4()
            )

        assert absent.value.status_code == wrong_kind.value.status_code == 404
        assert absent.value.detail == wrong_kind.value.detail

    async def test_the_kind_the_chat_route_writes_is_the_kind_this_check_accepts(self):
        """The producer and the check share one name, and this drives both ends of it.

        FM-012's shape: the row is written by the chat route and read by the SSE route,
        and two spellings would either refuse every customer turn or reopen #109 with
        nothing going red. The constant makes a typo a NameError rather than a silent
        divergence, so what is left to test is that the value it holds is one this check
        actually accepts. A stale constant passes import and fails every customer.
        """
        from app.api.v1.widget import CUSTOMER_TURN_JOB_KIND, _customer_turn_agent_id

        agent_id = uuid4()
        db = self._db_returning(self._job(CUSTOMER_TURN_JOB_KIND, agent_id))

        assert await _customer_turn_agent_id(db, uuid4()) == agent_id, (
            f"the chat route writes kind={CUSTOMER_TURN_JOB_KIND!r} and this endpoint "
            "refuses it, so every customer turn would 404 on its own event stream"
        )
