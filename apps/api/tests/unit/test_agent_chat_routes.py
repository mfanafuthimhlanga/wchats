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

Security coverage:
    T-02-06-01: cross-tenant 404 (agent lookup validates tenant_id)
    T-04-04-05: conversation ownership 403
    T-04-04-09: message max_length 422
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.api.deps import get_async_db, get_current_tenant
from app.main import app
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant


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
