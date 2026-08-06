"""Unit tests for OPS-02: POST /widget/agents/{agent_id}/feedback.

Tests:
    1. Valid JWT + valid body inserts a message_feedback row (mocked cursor) -> 204
    2. Missing Authorization header -> 403 (HTTPBearer auto_error)
    3. Invalid/expired JWT -> 401
    4. JWT agent_id claim mismatch -> 401
    5. rating outside {'up','down'} -> 422
    6. csat_score=6 (out of 1-5 bound) -> 422
    7. csat_score=0 (out of 1-5 bound) -> 422
    8. Rate limit exceeded (61st request in the same 60s bucket) -> 429
    9. Unknown/deleted agent -> 404
    10. Agent with no neon_connection_string -> 404
    11. csat_score omitted (optional) still inserts -> 204

Security coverage:
    T-21-02-02: feedback route requires the same Bearer widget JWT as /chat —
                NEVER unauthenticated like /config.
    T-21-02-03: 60/min per-agent_id Redis INCR rate limit, own bucket key.
    T-21-02-04: rating Literal + csat_score 1-5 bound enforced by Pydantic (422).

PRE-EXISTING INFRA NOTE (not a regression introduced by this plan):
    `app.main` transitively imports app.api.v1.evals -> app.worker.tasks.runtime.eval
    -> app.services.eval_service -> ragas.metrics.collections -> ragas.llms.base ->
    langchain_community.chat_models.vertexai, which raises ModuleNotFoundError in
    this environment (confirmed present on HEAD before this plan's changes —
    `pytest tests/unit/test_widget_routes.py` fails to collect identically).
    Tests below build a minimal FastAPI app around ONLY app.api.v1.widget.router
    (mirrors the targeted-import pattern already established in
    test_bench_routes.py, 21-05) instead of importing `app.main`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_async_db, get_async_redis
from app.api.v1 import widget as widget_module
from app.api.v1.widget import create_widget_jwt
from app.models.agent import Agent

# ---------------------------------------------------------------------------
# Targeted import — a minimal FastAPI app wrapping ONLY the widget router, so
# these tests never import app.main (see PRE-EXISTING INFRA NOTE above).
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(widget_module.router)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_ready_agent(agent_id=None) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = agent_id or uuid4()
    agent.tenant_id = uuid4()
    agent.status = "ready"
    agent.deleted_at = None
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_mock_db_returning_agent(agent: Agent) -> AsyncMock:
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = agent
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _make_mock_db_returning_none() -> AsyncMock:
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _make_mock_redis(incr_return_value: int = 1) -> AsyncMock:
    r = AsyncMock()
    r.set = AsyncMock(return_value=True)
    r.incr = AsyncMock(return_value=incr_return_value)
    return r


def _feedback_body(**overrides) -> dict:
    body = {
        "message_id": str(uuid4()),
        "conversation_id": str(uuid4()),
        "rating": "up",
        "csat_score": 5,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# 1. Valid JWT + valid body -> 204, INSERT called with correct args
# ---------------------------------------------------------------------------


class TestFeedbackHappyPath:
    async def test_valid_jwt_and_body_inserts_row_and_returns_204(self):
        agent = _make_ready_agent()
        mock_db = _make_mock_db_returning_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=1)
        token = create_widget_jwt(str(agent.id))

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        body = _feedback_body()

        try:
            with (
                patch(
                    "app.api.v1.widget.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.widget._insert_message_feedback_sync"
                ) as mock_insert,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/agents/{agent.id}/feedback",
                        json=body,
                        headers={"Authorization": f"Bearer {token}"},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 204
        assert response.headers.get("access-control-allow-origin") == "*"
        mock_insert.assert_called_once()
        call_args = mock_insert.call_args.args
        assert call_args[0] == "postgresql://fake/tenantdb"
        assert str(call_args[1]) == body["message_id"]
        assert str(call_args[2]) == body["conversation_id"]
        assert call_args[3] == "up"
        assert call_args[4] == 5

    async def test_csat_score_omitted_still_inserts_204(self):
        """csat_score is optional — thumbs-only feedback is valid."""
        agent = _make_ready_agent()
        mock_db = _make_mock_db_returning_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=1)
        token = create_widget_jwt(str(agent.id))

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        body = _feedback_body(csat_score=None, rating="down")

        try:
            with (
                patch(
                    "app.api.v1.widget.fernet_decrypt",
                    return_value="postgresql://fake/tenantdb",
                ),
                patch(
                    "app.api.v1.widget._insert_message_feedback_sync"
                ) as mock_insert,
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=_test_app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/agents/{agent.id}/feedback",
                        json=body,
                        headers={"Authorization": f"Bearer {token}"},
                    )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 204
        mock_insert.assert_called_once()
        assert mock_insert.call_args.args[3] == "down"
        assert mock_insert.call_args.args[4] is None


# ---------------------------------------------------------------------------
# 2-4. Auth failures — unauthenticated / invalid / mismatched
# ---------------------------------------------------------------------------


class TestFeedbackAuthFailures:
    async def test_missing_authorization_header_rejected(self):
        """No Bearer token at all -> rejected (403, HTTPBearer auto_error).

        T-21-02-02: the feedback route must NOT be unauthenticated.
        """
        agent = _make_ready_agent()
        mock_db = _make_mock_db_returning_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=1)

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/agents/{agent.id}/feedback",
                    json=_feedback_body(),
                )
        finally:
            _test_app.dependency_overrides.clear()

        # Not accepted — HTTPBearer(auto_error=True) raises 403 when the header
        # is absent entirely; an invalid/expired token raises 401 (test below).
        assert response.status_code in (401, 403)

    async def test_invalid_jwt_returns_401(self):
        agent = _make_ready_agent()
        mock_db = _make_mock_db_returning_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=1)

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/agents/{agent.id}/feedback",
                    json=_feedback_body(),
                    headers={"Authorization": "Bearer not-a-real-token"},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 401

    async def test_jwt_agent_id_mismatch_returns_401(self):
        agent = _make_ready_agent()
        mock_db = _make_mock_db_returning_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=1)
        # Token minted for a DIFFERENT agent_id than the URL path
        token = create_widget_jwt(str(uuid4()))

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/agents/{agent.id}/feedback",
                    json=_feedback_body(),
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 5-7. Input validation — rating enum + csat_score bounds (422)
# ---------------------------------------------------------------------------


class TestFeedbackInputValidation:
    async def test_invalid_rating_returns_422(self):
        agent = _make_ready_agent()
        mock_db = _make_mock_db_returning_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=1)
        token = create_widget_jwt(str(agent.id))

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/agents/{agent.id}/feedback",
                    json=_feedback_body(rating="sideways"),
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 422

    async def test_csat_score_above_bound_returns_422(self):
        agent = _make_ready_agent()
        mock_db = _make_mock_db_returning_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=1)
        token = create_widget_jwt(str(agent.id))

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/agents/{agent.id}/feedback",
                    json=_feedback_body(csat_score=6),
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 422

    async def test_csat_score_below_bound_returns_422(self):
        agent = _make_ready_agent()
        mock_db = _make_mock_db_returning_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=1)
        token = create_widget_jwt(str(agent.id))

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/agents/{agent.id}/feedback",
                    json=_feedback_body(csat_score=0),
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 8. Rate limit exceeded -> 429
# ---------------------------------------------------------------------------


class TestFeedbackRateLimit:
    async def test_rate_limit_exceeded_returns_429(self):
        """redis.incr returning 61 (past the 60/min ceiling) -> 429."""
        agent = _make_ready_agent()
        mock_db = _make_mock_db_returning_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=61)
        token = create_widget_jwt(str(agent.id))

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/agents/{agent.id}/feedback",
                    json=_feedback_body(),
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 429
        assert response.headers.get("retry-after") == "60"


# ---------------------------------------------------------------------------
# 9-10. Agent lookup failures
# ---------------------------------------------------------------------------


class TestFeedbackAgentLookup:
    async def test_unknown_agent_returns_404(self):
        agent_id = uuid4()
        mock_db = _make_mock_db_returning_none()
        mock_redis = _make_mock_redis(incr_return_value=1)
        token = create_widget_jwt(str(agent_id))

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/agents/{agent_id}/feedback",
                    json=_feedback_body(),
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_agent_without_neon_connection_string_returns_404(self):
        agent = _make_ready_agent()
        agent.neon_connection_string = None
        mock_db = _make_mock_db_returning_agent(agent)
        mock_redis = _make_mock_redis(incr_return_value=1)
        token = create_widget_jwt(str(agent.id))

        _test_app.dependency_overrides[get_async_db] = lambda: mock_db
        _test_app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=_test_app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/agents/{agent.id}/feedback",
                    json=_feedback_body(),
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            _test_app.dependency_overrides.clear()

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Source assertion — mirrors acceptance_criteria grep check
# ---------------------------------------------------------------------------


class TestFeedbackSourceAssertions:
    def test_insert_into_message_feedback_present_in_source(self):
        import inspect

        source = inspect.getsource(widget_module)
        assert "INSERT INTO message_feedback" in source
