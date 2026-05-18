"""
Unit tests for app.services.budget and the budget guard in POST /widget/{id}/chat.

TDD RED phase: Tests written before implementation.

Tests:
    1. test_check_and_increment_budget_allows_within_ceiling
       — redis.get returns "3.0" (below 5.0 ceiling); assert returns True;
         assert redis.incrbyfloat called with cost_usd=0.01
    2. test_check_and_increment_budget_blocks_at_ceiling
       — redis.get returns "5.0" (at ceiling); assert returns False;
         assert redis.incrbyfloat NOT called
    3. test_check_and_increment_budget_ttl_set_on_first_write
       — redis.get returns None (first write of day); assert redis.expire
         called with 86400
    4. test_widget_chat_returns_429_when_budget_exhausted
       — mock check_and_increment_budget to return False; POST /chat with
         valid JWT; assert 429 + Retry-After: "3600"

Security: F4 (CRITICAL) — T-04.1-03-01 — per-tenant daily budget ceiling.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import


# ---------------------------------------------------------------------------
# Test 1: allows spend within ceiling
# ---------------------------------------------------------------------------


class TestCheckAndIncrementBudgetAllowsWithinCeiling:
    @pytest.mark.anyio
    async def test_check_and_increment_budget_allows_within_ceiling(self):
        """check_and_increment_budget returns True when current spend < ceiling.

        Mocks redis.get to return "3.0" (below 5.0 ceiling).
        Asserts:
            - returns True (spend allowed)
            - redis.incrbyfloat called once (spend recorded)
        """
        from app.services.budget import check_and_increment_budget

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=b"3.0")
        mock_redis.incrbyfloat = AsyncMock()
        mock_redis.expire = AsyncMock()

        result = await check_and_increment_budget(
            tenant_id="tenant-abc",
            cost_usd=0.01,
            redis=mock_redis,
            ceiling_usd=5.0,
        )

        assert result is True
        mock_redis.incrbyfloat.assert_called_once()
        call_args = mock_redis.incrbyfloat.call_args
        # second positional arg is the increment amount
        assert call_args[0][1] == 0.01


# ---------------------------------------------------------------------------
# Test 2: blocks at ceiling
# ---------------------------------------------------------------------------


class TestCheckAndIncrementBudgetBlocksAtCeiling:
    @pytest.mark.anyio
    async def test_check_and_increment_budget_blocks_at_ceiling(self):
        """check_and_increment_budget returns False when current spend >= ceiling.

        Mocks redis.get to return "5.0" (at ceiling).
        Asserts:
            - returns False (spend blocked)
            - redis.incrbyfloat NOT called (no charge recorded)
        """
        from app.services.budget import check_and_increment_budget

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=b"5.0")
        mock_redis.incrbyfloat = AsyncMock()
        mock_redis.expire = AsyncMock()

        result = await check_and_increment_budget(
            tenant_id="tenant-abc",
            cost_usd=0.01,
            redis=mock_redis,
            ceiling_usd=5.0,
        )

        assert result is False
        mock_redis.incrbyfloat.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: TTL set on first write (redis.get returns None)
# ---------------------------------------------------------------------------


class TestCheckAndIncrementBudgetTTLOnFirstWrite:
    @pytest.mark.anyio
    async def test_check_and_increment_budget_ttl_set_on_first_write(self):
        """check_and_increment_budget sets a 86400s TTL on the first write of the day.

        Mocks redis.get to return None (no key yet — first request of the day).
        Asserts:
            - redis.expire is called with TTL=86400
        """
        from app.services.budget import check_and_increment_budget

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.incrbyfloat = AsyncMock()
        mock_redis.expire = AsyncMock()

        result = await check_and_increment_budget(
            tenant_id="tenant-abc",
            cost_usd=0.01,
            redis=mock_redis,
            ceiling_usd=5.0,
        )

        assert result is True
        # TTL must be 86400 (24 hours)
        mock_redis.expire.assert_called_once()
        expire_call_args = mock_redis.expire.call_args[0]
        assert expire_call_args[1] == 86400


# ---------------------------------------------------------------------------
# Test 4: Widget POST /chat returns 429 when budget exhausted
# ---------------------------------------------------------------------------


class TestWidgetChatReturns429WhenBudgetExhausted:
    @pytest.mark.anyio
    async def test_widget_chat_returns_429_when_budget_exhausted(self):
        """POST /widget/{id}/chat returns 429 with Retry-After: 3600 when budget exhausted.

        Patches app.api.v1.widget.check_and_increment_budget to return False,
        simulating a tenant that has reached the daily budget ceiling.

        Asserts:
            - response status code 429
            - Retry-After header equals "3600"
        Security: F4 — T-04.1-03-01 — budget guard fires BEFORE apply_async
        """
        from app.api.deps import get_async_db, get_async_redis
        from app.api.v1.widget import create_widget_jwt
        from app.main import app
        from app.models.agent import Agent

        # Build a ready agent
        agent = MagicMock(spec=Agent)
        agent.id = uuid4()
        agent.tenant_id = uuid4()
        agent.name = "Test Agent"
        agent.status = "ready"
        agent.deleted_at = None
        agent.neon_connection_string = b"fake-encrypted-bytes"

        # DB mock: return agent for lookup + handle job creation
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

        # Redis mock: incr returns 1 so rate limit is not triggered
        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()
        mock_redis.aclose = AsyncMock()
        mock_redis.ping.return_value = True

        token = create_widget_jwt(str(agent.id))

        app.dependency_overrides[get_async_db] = lambda: mock_session
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch(
                "app.api.v1.widget.check_and_increment_budget",
                new_callable=AsyncMock,
                return_value=False,
            ):
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

        assert response.status_code == 429
        assert response.headers.get("retry-after") == "3600"
