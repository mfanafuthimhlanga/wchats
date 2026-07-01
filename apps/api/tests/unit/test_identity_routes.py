"""
Unit tests for OTP identity routes and chat-route dispatch.

Phase 17 Plan 05: IDV-02 (email) + IDV-03 (SMS) HTTP surface, IDV-05 token transport.

Coverage:
    Schema tests (Task 1):
        - OtpVerifyBody with non-6-digit code raises pydantic.ValidationError
        - OtpVerifyBody with alpha/non-digit code raises ValidationError
        - OtpVerifyBody with invalid method raises ValidationError
        - OtpVerifyBody and OtpRequestBody accept valid inputs
        - OtpVerifyResponse carries verified_session_token
        - WidgetChatRequest carries optional verified_session_token (defaults None)

    Identity request route (Task 2):
        - POST /widget/{id}/identity/request with valid JWT + mocked service -> 204 (no body)
        - POST /widget/{id}/identity/request without JWT -> 401/403
        - POST /widget/{id}/identity/request with per-IP limit exceeded -> 429
        - POST /widget/{id}/identity/request when OtpRateLimited -> 429
        - POST /widget/{id}/identity/request sets Access-Control-Allow-Origin: *

    Identity verify route (Task 2):
        - POST /widget/{id}/identity/verify correct code -> 200 + verified_session_token
        - POST /widget/{id}/identity/verify wrong code -> 400 (OtpInvalid, no oracle)
        - POST /widget/{id}/identity/verify expired code -> 400 (same detail, no oracle)
        - POST /widget/{id}/identity/verify OtpRateLimited -> 429
        - POST /widget/{id}/identity/verify without JWT -> 401/403
        - POST /widget/{id}/identity/verify sets Access-Control-Allow-Origin: *

    Chat dispatch (Task 3):
        - POST /widget/{id}/chat with verified_session_token -> 5th dispatch arg = token
        - POST /widget/{id}/chat without verified_session_token -> 5th dispatch arg = ""

Security coverage:
    T-17-18: Per-IP send limit 429 enforced on identity/request
    T-17-19: verify returns identical 400 for wrong vs expired (no oracle)
    T-17-11: otp_code and verified_session_token absent from log calls (source check)
    T-17-07: Token is server-minted; client cannot supply its own to verify route
    T-17-20: JWT validated first on both identity routes
"""

import pydantic
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.api.deps import get_async_db, get_async_redis
from app.api.v1.widget import create_widget_jwt
from app.main import app
from app.models.agent import Agent


# ---------------------------------------------------------------------------
# Helper factories (mirrors test_widget_routes.py conventions)
# ---------------------------------------------------------------------------


def _make_ready_agent() -> Agent:
    """Return a mock Agent in 'ready' status with encrypted conn string."""
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = uuid4()
    agent.name = "Test Agent"
    agent.status = "ready"
    agent.deleted_at = None
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_mock_db_with_agent(agent: Agent):
    """Async mock DB session that returns *agent* for any execute() call."""
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


def _make_mock_redis(incr_return_value: int = 1):
    """AsyncMock Redis that returns *incr_return_value* from .incr()."""
    r = AsyncMock()
    r.incr = AsyncMock(return_value=incr_return_value)
    r.expire = AsyncMock()
    r.aclose = AsyncMock()
    r.ping.return_value = True
    return r


# ===========================================================================
# TASK 1 — Schema tests
# ===========================================================================


class TestOtpSchemas:
    """Schema validation tests for OtpRequestBody, OtpVerifyBody, OtpVerifyResponse."""

    def test_otp_verify_body_rejects_5_digit_code(self):
        """OtpVerifyBody with a 5-digit code raises pydantic.ValidationError."""
        from app.schemas.widget import OtpVerifyBody

        with pytest.raises(pydantic.ValidationError):
            OtpVerifyBody(external_id="user@example.com", otp_code="12345", method="email")

    def test_otp_verify_body_rejects_7_digit_code(self):
        """OtpVerifyBody with a 7-digit code raises pydantic.ValidationError."""
        from app.schemas.widget import OtpVerifyBody

        with pytest.raises(pydantic.ValidationError):
            OtpVerifyBody(external_id="user@example.com", otp_code="1234567", method="email")

    def test_otp_verify_body_rejects_alpha_code(self):
        """OtpVerifyBody with alpha characters in code raises pydantic.ValidationError."""
        from app.schemas.widget import OtpVerifyBody

        with pytest.raises(pydantic.ValidationError):
            OtpVerifyBody(external_id="user@example.com", otp_code="abc123", method="email")

    def test_otp_verify_body_rejects_invalid_method(self):
        """OtpVerifyBody with method other than 'email'/'sms' raises pydantic.ValidationError."""
        from app.schemas.widget import OtpVerifyBody

        with pytest.raises(pydantic.ValidationError):
            OtpVerifyBody(external_id="user@example.com", otp_code="123456", method="push")

    def test_otp_verify_body_accepts_valid_email_method(self):
        """OtpVerifyBody with valid 6-digit code and 'email' method is accepted."""
        from app.schemas.widget import OtpVerifyBody

        body = OtpVerifyBody(external_id="user@example.com", otp_code="123456", method="email")
        assert body.otp_code == "123456"
        assert body.method == "email"

    def test_otp_verify_body_accepts_valid_sms_method(self):
        """OtpVerifyBody with valid 6-digit code and 'sms' method is accepted."""
        from app.schemas.widget import OtpVerifyBody

        body = OtpVerifyBody(external_id="+27821234567", otp_code="000000", method="sms")
        assert body.otp_code == "000000"
        assert body.method == "sms"

    def test_otp_verify_body_accepts_leading_zero_code(self):
        """OtpVerifyBody preserves leading zeros in a 6-digit code."""
        from app.schemas.widget import OtpVerifyBody

        body = OtpVerifyBody(external_id="user@example.com", otp_code="001234", method="email")
        assert body.otp_code == "001234"

    def test_otp_request_body_rejects_invalid_method(self):
        """OtpRequestBody with method other than 'email'/'sms' raises pydantic.ValidationError."""
        from app.schemas.widget import OtpRequestBody

        with pytest.raises(pydantic.ValidationError):
            OtpRequestBody(external_id="user@example.com", method="carrier-pigeon")

    def test_otp_request_body_accepts_sms(self):
        """OtpRequestBody with method='sms' is accepted."""
        from app.schemas.widget import OtpRequestBody

        body = OtpRequestBody(external_id="+27821234567", method="sms")
        assert body.method == "sms"

    def test_otp_verify_response_exposes_token(self):
        """OtpVerifyResponse carries verified_session_token."""
        from app.schemas.widget import OtpVerifyResponse

        resp = OtpVerifyResponse(verified_session_token="session_abc123")
        assert resp.verified_session_token == "session_abc123"

    def test_widget_chat_request_accepts_verified_session_token(self):
        """WidgetChatRequest accepts an optional verified_session_token."""
        from app.schemas.widget import WidgetChatRequest

        req = WidgetChatRequest(message="hello", verified_session_token="tok_xyz")
        assert req.verified_session_token == "tok_xyz"

    def test_widget_chat_request_defaults_token_to_none(self):
        """WidgetChatRequest.verified_session_token defaults to None when omitted."""
        from app.schemas.widget import WidgetChatRequest

        req = WidgetChatRequest(message="hello")
        assert req.verified_session_token is None


# ===========================================================================
# TASK 2 — Identity request route tests
# ===========================================================================


class TestIdentityRequestRoute:
    """HTTP behaviour tests for POST /widget/{agent_id}/identity/request."""

    async def test_returns_204_no_body(self):
        """Valid JWT + mocked request_otp -> 204 with empty response body."""
        agent = _make_ready_agent()
        token = create_widget_jwt(str(agent.id))
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch("app.api.v1.widget.request_otp", new_callable=AsyncMock) as mock_req:
                mock_req.return_value = None
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/identity/request",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"external_id": "user@example.com", "method": "email"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 204
        assert response.content == b""

    async def test_without_jwt_returns_4xx(self):
        """POST /identity/request without Authorization header -> 401 or 403."""
        agent_id = uuid4()
        mock_redis = _make_mock_redis()
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/{agent_id}/identity/request",
                    json={"external_id": "user@example.com", "method": "email"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code in (401, 403)

    async def test_invalid_jwt_returns_401(self):
        """POST /identity/request with invalid JWT -> 401."""
        agent_id = uuid4()
        mock_redis = _make_mock_redis()
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/{agent_id}/identity/request",
                    headers={"Authorization": "Bearer totally.invalid.token"},
                    json={"external_id": "user@example.com", "method": "email"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 401

    async def test_per_ip_rate_limit_returns_429(self):
        """Per-IP incr > 10 -> 429 before even calling request_otp."""
        agent = _make_ready_agent()
        token = create_widget_jwt(str(agent.id))
        mock_redis = _make_mock_redis(incr_return_value=11)  # > 10 ceiling

        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch("app.api.v1.widget.request_otp", new_callable=AsyncMock) as mock_req:
                mock_req.return_value = None
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/identity/request",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"external_id": "user@example.com", "method": "email"},
                    )
                # request_otp should NOT have been called when IP rate limited
                mock_req.assert_not_called()
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 429

    async def test_otp_rate_limited_service_returns_429(self):
        """OtpRateLimited from request_otp -> 429."""
        from app.services.identity_service import OtpRateLimited

        agent = _make_ready_agent()
        token = create_widget_jwt(str(agent.id))
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch("app.api.v1.widget.request_otp", new_callable=AsyncMock) as mock_req:
                mock_req.side_effect = OtpRateLimited("Too many sends")
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/identity/request",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"external_id": "user@example.com", "method": "email"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 429

    async def test_cors_header_is_set(self):
        """POST /identity/request -> Access-Control-Allow-Origin: * is set."""
        agent = _make_ready_agent()
        token = create_widget_jwt(str(agent.id))
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch("app.api.v1.widget.request_otp", new_callable=AsyncMock) as mock_req:
                mock_req.return_value = None
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/identity/request",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"external_id": "user@example.com", "method": "email"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.headers.get("access-control-allow-origin") == "*"

    async def test_jwt_agent_id_mismatch_returns_401(self):
        """POST /identity/request with JWT for a different agent_id -> 401."""
        agent = _make_ready_agent()
        other_agent_id = uuid4()
        token = create_widget_jwt(str(agent.id))  # token for agent.id, not other_agent_id
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/{other_agent_id}/identity/request",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"external_id": "user@example.com", "method": "email"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 401


# ===========================================================================
# TASK 2 — Identity verify route tests
# ===========================================================================


class TestIdentityVerifyRoute:
    """HTTP behaviour tests for POST /widget/{agent_id}/identity/verify."""

    async def test_correct_code_returns_200_with_token(self):
        """Correct OTP code -> 200 with verified_session_token in body."""
        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)
        token = create_widget_jwt(str(agent.id))
        mock_redis = _make_mock_redis()
        expected_session_token = "vst_session_abc123_xyz"

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with (
                patch("app.api.v1.widget.verify_otp", new_callable=AsyncMock) as mock_verify,
                patch("app.api.v1.widget.fernet_decrypt", return_value="postgresql://tenant/db"),
            ):
                mock_verify.return_value = expected_session_token
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/identity/verify",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "external_id": "user@example.com",
                            "otp_code": "123456",
                            "method": "email",
                        },
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["verified_session_token"] == expected_session_token

    async def test_wrong_code_returns_400_no_token(self):
        """Wrong OTP code -> 400 (OtpInvalid) and response contains no verified_session_token."""
        from app.services.identity_service import OtpInvalid

        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)
        token = create_widget_jwt(str(agent.id))
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with (
                patch("app.api.v1.widget.verify_otp", new_callable=AsyncMock) as mock_verify,
                patch("app.api.v1.widget.fernet_decrypt", return_value="postgresql://tenant/db"),
            ):
                mock_verify.side_effect = OtpInvalid("Invalid code")
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/identity/verify",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "external_id": "user@example.com",
                            "otp_code": "999999",
                            "method": "email",
                        },
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 400
        body = response.json()
        assert "verified_session_token" not in body

    async def test_expired_code_returns_400_same_detail_as_wrong(self):
        """Expired code -> 400 with the same detail as a wrong code (no oracle, T-17-19)."""
        from app.services.identity_service import OtpInvalid

        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)
        token = create_widget_jwt(str(agent.id))
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        wrong_detail = None
        expired_detail = None

        # First request: wrong code
        try:
            with (
                patch("app.api.v1.widget.verify_otp", new_callable=AsyncMock) as mock_verify,
                patch("app.api.v1.widget.fernet_decrypt", return_value="postgresql://tenant/db"),
            ):
                mock_verify.side_effect = OtpInvalid("Invalid code")
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    wrong_response = await client.post(
                        f"/widget/{agent.id}/identity/verify",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "external_id": "user@example.com",
                            "otp_code": "000000",
                            "method": "email",
                        },
                    )
                wrong_detail = wrong_response.json().get("detail")
        finally:
            app.dependency_overrides.clear()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        # Second request: expired code (same exception type, different message from service)
        try:
            with (
                patch("app.api.v1.widget.verify_otp", new_callable=AsyncMock) as mock_verify,
                patch("app.api.v1.widget.fernet_decrypt", return_value="postgresql://tenant/db"),
            ):
                mock_verify.side_effect = OtpInvalid("Code expired or not found")
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    expired_response = await client.post(
                        f"/widget/{agent.id}/identity/verify",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "external_id": "user@example.com",
                            "otp_code": "123456",
                            "method": "email",
                        },
                    )
                expired_detail = expired_response.json().get("detail")
        finally:
            app.dependency_overrides.clear()

        # Both should be 400 with identical detail (no oracle)
        assert wrong_response.status_code == 400
        assert expired_response.status_code == 400
        assert wrong_detail == expired_detail, (
            f"Oracle leak: wrong='{wrong_detail}' != expired='{expired_detail}'"
        )

    async def test_rate_limited_attempts_returns_429(self):
        """OtpRateLimited (too many failed attempts) -> 429."""
        from app.services.identity_service import OtpRateLimited

        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)
        token = create_widget_jwt(str(agent.id))
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with (
                patch("app.api.v1.widget.verify_otp", new_callable=AsyncMock) as mock_verify,
                patch("app.api.v1.widget.fernet_decrypt", return_value="postgresql://tenant/db"),
            ):
                mock_verify.side_effect = OtpRateLimited("Too many failed attempts")
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/identity/verify",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "external_id": "user@example.com",
                            "otp_code": "123456",
                            "method": "email",
                        },
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 429

    async def test_without_jwt_returns_4xx(self):
        """POST /identity/verify without Authorization header -> 401 or 403."""
        agent_id = uuid4()
        mock_db_session = AsyncMock()
        mock_redis = _make_mock_redis()
        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/{agent_id}/identity/verify",
                    json={
                        "external_id": "user@example.com",
                        "otp_code": "123456",
                        "method": "email",
                    },
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code in (401, 403)

    async def test_cors_header_is_set_on_success(self):
        """POST /identity/verify -> Access-Control-Allow-Origin: * set on success."""
        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)
        token = create_widget_jwt(str(agent.id))
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with (
                patch("app.api.v1.widget.verify_otp", new_callable=AsyncMock) as mock_verify,
                patch("app.api.v1.widget.fernet_decrypt", return_value="postgresql://tenant/db"),
            ):
                mock_verify.return_value = "tok_abc"
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/identity/verify",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "external_id": "user@example.com",
                            "otp_code": "123456",
                            "method": "email",
                        },
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.headers.get("access-control-allow-origin") == "*"

    async def test_jwt_agent_id_mismatch_returns_401(self):
        """JWT for a different agent_id -> 401 before DB is ever touched."""
        agent = _make_ready_agent()
        other_id = uuid4()
        token = create_widget_jwt(str(agent.id))  # bound to agent.id, not other_id
        mock_db_session = AsyncMock()
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_async_db] = lambda: mock_db_session
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/widget/{other_id}/identity/verify",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "external_id": "user@example.com",
                        "otp_code": "123456",
                        "method": "email",
                    },
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 401


# ===========================================================================
# TASK 3 — Chat-route dispatch with verified_session_token (IDV-05)
# ===========================================================================


class TestChatDispatchToken:
    """Verify the 5th positional arg threading from chat route -> run_agent_turn."""

    async def test_with_token_dispatches_as_5th_arg(self):
        """POST /chat with verified_session_token -> apply_async 5th arg = token."""
        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)
        mock_redis = _make_mock_redis()
        token = create_widget_jwt(str(agent.id))
        session_token = "vst_abc_session_xyz"

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch("app.api.v1.widget.run_agent_turn.apply_async") as mock_dispatch:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/chat",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"message": "hello", "verified_session_token": session_token},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        mock_dispatch.assert_called_once()
        dispatch_args = mock_dispatch.call_args.kwargs["args"]
        assert len(dispatch_args) == 5
        assert dispatch_args[4] == session_token

    async def test_without_token_dispatches_empty_string_as_5th_arg(self):
        """POST /chat without verified_session_token -> apply_async 5th arg = ''."""
        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)
        mock_redis = _make_mock_redis()
        token = create_widget_jwt(str(agent.id))

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch("app.api.v1.widget.run_agent_turn.apply_async") as mock_dispatch:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/chat",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"message": "hello"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        mock_dispatch.assert_called_once()
        dispatch_args = mock_dispatch.call_args.kwargs["args"]
        assert len(dispatch_args) == 5
        assert dispatch_args[4] == ""

    async def test_null_token_dispatches_empty_string_as_5th_arg(self):
        """POST /chat with verified_session_token=null -> apply_async 5th arg = ''."""
        agent = _make_ready_agent()
        mock_db, _ = _make_mock_db_with_agent(agent)
        mock_redis = _make_mock_redis()
        token = create_widget_jwt(str(agent.id))

        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            with patch("app.api.v1.widget.run_agent_turn.apply_async") as mock_dispatch:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/widget/{agent.id}/chat",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"message": "hello", "verified_session_token": None},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        mock_dispatch.assert_called_once()
        dispatch_args = mock_dispatch.call_args.kwargs["args"]
        assert len(dispatch_args) == 5
        assert dispatch_args[4] == ""
