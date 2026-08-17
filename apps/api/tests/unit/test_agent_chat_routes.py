"""
Unit tests for POST /agents/{agent_id}/chat and GET /agents/{agent_id}/conversations.

Tests the HTTP contract of agent_chat.py:
    1. POST returns 202 with job_id and events_url for valid request
    2. POST returns 404 when agent does not exist
    3. POST returns 409 when agent.status != 'ready'
    4. POST returns 422 when message exceeds 2000 characters
    5. POST returns 422 when message is empty string
    6. POST returns 403 when conversation_id doesn't belong to agent
    7. GET /conversations returns ConversationListResponse with mocked rows
    8. All routes return 401/403 when X-API-Key header is missing
    9. POST returns 429 when the agent's 60/min turn ceiling is exceeded (7.4)
   10. POST returns 429 and dispatches nothing when the tenant's daily budget
       is exhausted (7.4)
   11. POST with a foreign agent_id charges nothing to that agent's rate-limit
       bucket — the ownership check runs first (F1)

Security coverage:
    T-02-06-01: cross-tenant 404 (agent lookup validates tenant_id)
    T-04-04-05: conversation ownership 403
    T-04-04-09: message max_length 422
    BACKLOG 7.4: rate limit + budget guard parity with POST /widget/{id}/chat
    F1:          cross-tenant rate-limit starvation via the public agent_id
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.api.deps import get_async_db, get_async_redis, get_current_tenant
from app.main import app
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant
from app.services.budget import ESTIMATED_TURN_COST_USD

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_fake_tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_ready_agent(tenant: Tenant) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = tenant.id
    agent.status = "ready"
    agent.deleted_at = None
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_non_ready_agent(tenant: Tenant, status: str = "building") -> Agent:
    agent = _make_ready_agent(tenant)
    agent.status = status
    return agent


def _make_mock_db_with_agent(agent: Agent):
    """Mock async DB that returns *agent* for agent lookup and handles job creation."""
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
    """Mock async DB that returns None for any agent lookup."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _make_mock_redis(incr_return_value: int = 1):
    """AsyncMock Redis whose .incr() returns *incr_return_value*.

    Mirrors tests/unit/test_widget_routes.py — the route reads only the INCR result
    to decide the rate limit, and get() returning an AsyncMock coerces to 0.0 spend
    in check_and_increment_budget, i.e. budget available.
    """
    r = AsyncMock()
    r.incr = AsyncMock(return_value=incr_return_value)
    r.get = AsyncMock(return_value=None)
    r.expire = AsyncMock()
    r.incrbyfloat = AsyncMock()
    r.aclose = AsyncMock()
    return r


# ---------------------------------------------------------------------------
# Test 1: Valid POST returns 202 with job_id and events_url
# ---------------------------------------------------------------------------


class TestAgentChatPost202:
    async def test_valid_post_returns_202_with_job_id_and_events_url(self):
        """Valid POST /agents/{id}/chat → 202 with job_id and events_url."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db, job_id = _make_mock_db_with_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        # incr=1 → under the 60/min ceiling; get=None → no spend recorded today
        app.dependency_overrides[get_async_redis] = lambda: _make_mock_redis()

        try:
            with patch(
                "app.api.v1.agent_chat.run_agent_turn.apply_async"
            ) as mock_dispatch:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/chat",
                        headers={"X-API-Key": "vrd_live_test"},
                        json={"message": "Hello, agent!"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        body = response.json()
        assert "job_id" in body
        assert "events_url" in body
        assert body["status"] == "pending"
        assert body["events_url"].startswith("/widget/jobs/")
        mock_dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: POST returns 404 when agent does not exist
# ---------------------------------------------------------------------------


class TestAgentChatPost404:
    async def test_post_returns_404_when_agent_not_found(self):
        """POST /agents/{id}/chat with nonexistent agent → 404."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_no_agent()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        # incr=1 → under the 60/min ceiling; get=None → no spend recorded today
        app.dependency_overrides[get_async_redis] = lambda: _make_mock_redis()

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{uuid4()}/chat",
                    headers={"X-API-Key": "vrd_live_test"},
                    json={"message": "Hello"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test 3: POST returns 409 when agent.status != 'ready'
# ---------------------------------------------------------------------------


class TestAgentChatPost409:
    async def test_post_returns_409_when_agent_not_ready(self):
        """POST /agents/{id}/chat with status='building' → 409."""
        fake_tenant = _make_fake_tenant()
        building_agent = _make_non_ready_agent(fake_tenant, status="building")
        mock_db, _ = _make_mock_db_with_agent(building_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        # incr=1 → under the 60/min ceiling; get=None → no spend recorded today
        app.dependency_overrides[get_async_redis] = lambda: _make_mock_redis()

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{building_agent.id}/chat",
                    headers={"X-API-Key": "vrd_live_test"},
                    json={"message": "Hello"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 409


# ---------------------------------------------------------------------------
# Test 4: POST returns 422 when message exceeds 2000 characters
# ---------------------------------------------------------------------------


class TestAgentChatPost422TooLong:
    async def test_post_returns_422_when_message_exceeds_2000_chars(self):
        """POST /agents/{id}/chat with message >2000 chars → 422."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_no_agent()  # DB irrelevant — Pydantic rejects first

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        # incr=1 → under the 60/min ceiling; get=None → no spend recorded today
        app.dependency_overrides[get_async_redis] = lambda: _make_mock_redis()

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{uuid4()}/chat",
                    headers={"X-API-Key": "vrd_live_test"},
                    json={"message": "x" * 2001},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Test 5: POST returns 422 when message is empty string
# ---------------------------------------------------------------------------


class TestAgentChatPost422Empty:
    async def test_post_returns_422_when_message_is_empty_string(self):
        """POST /agents/{id}/chat with message='' → 422."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_no_agent()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        # incr=1 → under the 60/min ceiling; get=None → no spend recorded today
        app.dependency_overrides[get_async_redis] = lambda: _make_mock_redis()

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{uuid4()}/chat",
                    headers={"X-API-Key": "vrd_live_test"},
                    json={"message": ""},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Test 6: POST returns 403 when conversation_id doesn't belong to agent
# ---------------------------------------------------------------------------


class TestAgentChatPost403ConversationOwnership:
    async def test_post_returns_403_when_conversation_not_owned(self):
        """POST /agents/{id}/chat with foreign conversation_id → 403."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db, _ = _make_mock_db_with_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        # incr=1 → under the 60/min ceiling; get=None → no spend recorded today
        app.dependency_overrides[get_async_redis] = lambda: _make_mock_redis()

        try:
            with patch(
                "app.api.v1.agent_chat._validate_conv_owner",
                return_value=False,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/chat",
                        headers={"X-API-Key": "vrd_live_test"},
                        json={
                            "message": "Hello",
                            "conversation_id": str(uuid4()),
                        },
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 403
        assert "Conversation not found" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Test 7: GET /conversations returns ConversationListResponse
# ---------------------------------------------------------------------------


class TestGetAgentConversations:
    async def test_get_conversations_returns_conversation_list(self):
        """GET /agents/{id}/conversations → 200 with conversations array."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db, _ = _make_mock_db_with_agent(ready_agent)

        # Mock psycopg2 conversation rows
        from datetime import datetime, timezone
        fake_rows = [
            (str(uuid4()), datetime.now(timezone.utc), False, 3),
            (str(uuid4()), datetime.now(timezone.utc), True, 7),
        ]

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        # incr=1 → under the 60/min ceiling; get=None → no spend recorded today
        app.dependency_overrides[get_async_redis] = lambda: _make_mock_redis()

        try:
            with (
                patch(
                    "app.api.v1.agent_chat.fernet_decrypt",
                    return_value="postgresql://fake/db",
                ),
                patch("app.api.v1.agent_chat.psycopg2.connect") as mock_connect,
            ):
                mock_conn = MagicMock()
                mock_connect.return_value = mock_conn
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__ = MagicMock(
                    return_value=mock_cursor
                )
                mock_conn.cursor.return_value.__exit__ = MagicMock(
                    return_value=False
                )
                mock_cursor.fetchall.return_value = fake_rows

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/api/v1/agents/{ready_agent.id}/conversations",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert "conversations" in body
        assert len(body["conversations"]) == 2
        assert "id" in body["conversations"][0]
        assert "escalated" in body["conversations"][0]
        assert "message_count" in body["conversations"][0]


# ---------------------------------------------------------------------------
# Test 8: Missing X-API-Key returns 401/403
# ---------------------------------------------------------------------------


class TestAgentChatRequiresApiKey:
    async def test_post_without_api_key_returns_401_or_403(self):
        """POST /agents/{id}/chat with no X-API-Key header → 401 or 403."""
        # No dependency overrides — let the real get_current_tenant run
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/agents/{uuid4()}/chat",
                json={"message": "Hello"},
            )

        assert response.status_code in (401, 403)

    async def test_get_conversations_without_api_key_returns_401_or_403(self):
        """GET /agents/{id}/conversations with no X-API-Key → 401 or 403."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/agents/{uuid4()}/conversations")

        assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Test 9 — BACKLOG 7.4: 60/min per-agent turn ceiling
# ---------------------------------------------------------------------------


class TestAgentChatPost429RateLimit:
    async def test_post_returns_429_when_rate_limit_exceeded(self):
        """POST /agents/{id}/chat when redis.incr returns 61 → 429, nothing dispatched.

        The authenticated route spends the tenant's Anthropic key exactly as the widget
        route does; before 7.4 it enforced no ceiling at all.
        """
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db, _ = _make_mock_db_with_agent(ready_agent)
        # 61st request inside the current 60-second window
        mock_redis = _make_mock_redis(incr_return_value=61)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch(
                "app.api.v1.agent_chat.run_agent_turn.apply_async"
            ) as mock_dispatch:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/chat",
                        headers={"X-API-Key": "vrd_live_test"},
                        json={"message": "Exceeds rate limit"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 429
        assert response.headers.get("retry-after") == "60"
        mock_dispatch.assert_not_called()

    async def test_rate_limit_bucket_is_separate_from_the_widget_route(self):
        """The API route's Redis key must not collide with the widget route's.

        A shared bucket would let an integration starve the tenant's live widget
        customers out of their own 60/min, and vice versa.
        """
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db, _ = _make_mock_db_with_agent(ready_agent)
        mock_redis = _make_mock_redis(incr_return_value=1)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch("app.api.v1.agent_chat.run_agent_turn.apply_async"):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/chat",
                        headers={"X-API-Key": "vrd_live_test"},
                        json={"message": "Hello"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        rate_keys = [
            call.args[0]
            for call in mock_redis.incr.call_args_list
            if str(call.args[0]).startswith("rate")
        ]
        assert rate_keys, "route recorded no rate-limit INCR at all"
        widget_key = f"rate:{ready_agent.id}"
        assert all(not k.startswith(widget_key) for k in rate_keys), (
            f"API chat shares the widget route's bucket: {rate_keys}"
        )

    async def test_foreign_api_key_cannot_consume_another_tenants_agent_bucket(self):
        """Tenant A's API key aimed at tenant B's agent_id must charge nothing.

        agent_id is public — it is in the embed snippet and in the unauthenticated
        GET /widget/{agent_id}/config. If the bucket were charged before ownership
        was established, any valid key could send 61 requests at a victim's
        agent_id, take 61 404s, and leave the victim's own integration 429'd for
        the rest of the window. The 404 is not enough on its own: the victim's
        bucket must be untouched.
        """
        attacker_tenant = _make_fake_tenant()
        victim_agent_id = uuid4()
        # The ownership SELECT is scoped by tenant_id, so a foreign agent_id
        # returns no row for this key.
        mock_db = _make_mock_db_no_agent()
        mock_redis = _make_mock_redis(incr_return_value=1)

        app.dependency_overrides[get_current_tenant] = lambda: attacker_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch(
                "app.api.v1.agent_chat.run_agent_turn.apply_async"
            ) as mock_dispatch:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{victim_agent_id}/chat",
                        headers={"X-API-Key": "vrd_live_attacker"},
                        json={"message": "burn the victim's window"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404
        mock_dispatch.assert_not_called()

        touched = [
            str(call.args[0])
            for call in (
                *mock_redis.incr.call_args_list,
                *mock_redis.set.call_args_list,
            )
        ]
        assert all(str(victim_agent_id) not in key for key in touched), (
            "a foreign API key charged the victim agent's rate-limit bucket: "
            f"{touched}"
        )


# ---------------------------------------------------------------------------
# Test 10 — BACKLOG 7.4: tenant daily budget ceiling
# ---------------------------------------------------------------------------


class TestAgentChatPost429DailyBudget:
    async def test_post_returns_429_when_daily_budget_exhausted(self):
        """POST /agents/{id}/chat with the tenant ceiling reached → 429, no dispatch."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db, _ = _make_mock_db_with_agent(ready_agent)
        mock_redis = _make_mock_redis(incr_return_value=1)  # under the rate ceiling

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with (
                patch(
                    "app.api.v1.agent_chat.check_and_increment_budget",
                    new=AsyncMock(return_value=False),
                ),
                patch(
                    "app.api.v1.agent_chat.run_agent_turn.apply_async"
                ) as mock_dispatch,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/chat",
                        headers={"X-API-Key": "vrd_live_test"},
                        json={"message": "One turn past the ceiling"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 429
        assert "Daily usage limit reached" in response.json()["detail"]
        assert response.headers.get("retry-after") == "3600"
        mock_dispatch.assert_not_called()

    async def test_budget_is_charged_against_the_authenticated_tenant(self):
        """The daily ceiling is charged to the tenant that owns the API key.

        Charging the wrong id would give every tenant an unmetered budget of its own.
        """
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db, _ = _make_mock_db_with_agent(ready_agent)
        mock_redis = _make_mock_redis(incr_return_value=1)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        budget_spy = AsyncMock(return_value=True)

        try:
            with (
                patch(
                    "app.api.v1.agent_chat.check_and_increment_budget", new=budget_spy
                ),
                patch("app.api.v1.agent_chat.run_agent_turn.apply_async"),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{ready_agent.id}/chat",
                        headers={"X-API-Key": "vrd_live_test"},
                        json={"message": "Hello"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        budget_spy.assert_awaited_once()
        assert budget_spy.await_args.args[0] == str(fake_tenant.id)
        assert budget_spy.await_args.args[1] == ESTIMATED_TURN_COST_USD
