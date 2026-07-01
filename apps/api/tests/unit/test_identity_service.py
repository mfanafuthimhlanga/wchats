"""
Unit tests for identity_service.py — Phase 17 OTP identity verification.

TDD:
  Task 1 RED/GREEN: crypto core + Redis challenge helpers
  Task 2 RED/GREEN: email SMTP + SMS provider abstraction
  Task 3 RED/GREEN: request_otp / verify_otp / check_verified_session

Database-touching integration assertions (verify_otp, check_verified_session)
use mock psycopg2.connect — live DB run deferred (no local PostgreSQL binaries
present on the 4 GB dev machine; mirrors the established deferral pattern).

All async tests work without decorators because asyncio_mode = "auto" is
configured in pyproject.toml [tool.pytest.ini_options].
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Task 1: Crypto core + Redis challenge helpers
# ---------------------------------------------------------------------------


def test_otp_code_format():
    """generate_otp_code returns a 6-digit, zero-padded decimal string."""
    from app.services.identity_service import generate_otp_code

    code = generate_otp_code()
    assert len(code) == 6, f"Expected 6 chars, got {len(code)}: {code!r}"
    assert code.isdigit(), f"Expected all digits, got {code!r}"


def test_otp_hash_not_plaintext():
    """hash_otp_code returns a 64-char hex string that differs from the input."""
    from app.services.identity_service import generate_otp_code, hash_otp_code

    code = generate_otp_code()
    stored = hash_otp_code(code)
    assert stored != code, "Hash must not equal the plaintext code (T-17-08)"
    assert len(stored) == 64, f"SHA-256 hex must be 64 chars, got {len(stored)}"


def test_verify_otp_code_constant_time():
    """verify_otp_code returns True for the matching code, False otherwise."""
    from app.services.identity_service import (
        generate_otp_code,
        hash_otp_code,
        verify_otp_code,
    )

    code = generate_otp_code()
    stored_hash = hash_otp_code(code)
    assert verify_otp_code(stored_hash, code) is True
    # A different code must not match (same-length to avoid length-based oracle)
    wrong = "000000" if code != "000000" else "000001"
    assert verify_otp_code(stored_hash, wrong) is False


def test_session_token_hashed():
    """generate_session_token returns ~43 chars; hash is 64 chars and differs."""
    from app.services.identity_service import generate_session_token, hash_session_token

    token = generate_session_token()
    token_hash = hash_session_token(token)
    assert len(token) >= 43, f"Token too short: {len(token)}"
    assert len(token_hash) == 64, f"Hash must be 64 chars, got {len(token_hash)}"
    assert token_hash != token, "Hash must differ from plaintext token (T-17-08)"


def test_otp_redis_key_lowercases():
    """_otp_redis_key lowercases the external_id and returns the correct pattern."""
    from app.services.identity_service import _otp_redis_key

    key = _otp_redis_key("agent-1", "USER@EXAMPLE.COM", "email")
    assert key == "otp:agent-1:user@example.com:email"


async def test_store_otp_challenge():
    """store_otp_challenge writes JSON {hash, attempts:0} with the given TTL."""
    from app.services.identity_service import store_otp_challenge

    redis = AsyncMock()
    fake_hash = "a" * 64  # 64-char simulated SHA-256 hex
    await store_otp_challenge(redis, "agent-1", "user@example.com", "email", fake_hash, 600)

    redis.set.assert_called_once()
    args, kwargs = redis.set.call_args
    # args[0] = key, args[1] = JSON payload
    payload = json.loads(args[1])
    assert payload["hash"] == fake_hash
    assert payload["attempts"] == 0
    assert kwargs.get("ex") == 600


# ---------------------------------------------------------------------------
# Task 2: Delivery seam — email SMTP + SMS provider abstraction
# ---------------------------------------------------------------------------


def test_send_otp_email_unconfigured_no_raise():
    """SMTP unset → warning logged, returns None, no exception raised."""
    with patch("app.services.identity_service.settings") as mock_settings:
        mock_settings.SMTP_HOST = None
        mock_settings.SMTP_FROM = None
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USER = None
        mock_settings.SMTP_PASSWORD = None
        mock_settings.OTP_EMAIL_TTL_SECONDS = 600

        from app.services.identity_service import send_otp_email

        # Must not raise regardless of SMTP being unconfigured
        result = send_otp_email("user@test.com", "123456")
        assert result is None


def test_sms_provider_selection_twilio():
    """SMS_PROVIDER=='twilio' with full creds returns a TwilioSmsProvider."""
    from app.services.identity_service import TwilioSmsProvider

    with patch("app.services.identity_service.settings") as mock_settings:
        mock_settings.SMS_PROVIDER = "twilio"
        mock_settings.TWILIO_ACCOUNT_SID = "ACtest123"
        mock_settings.TWILIO_AUTH_TOKEN = "authtoken456"
        mock_settings.TWILIO_FROM_NUMBER = "+15555550000"
        mock_settings.AT_API_KEY = None
        mock_settings.AT_USERNAME = None

        from app.services.identity_service import _get_sms_provider

        provider = _get_sms_provider()
        assert isinstance(provider, TwilioSmsProvider)


def test_sms_provider_called():
    """_deliver_otp(method='sms',...) calls the resolved provider's send with dest + body."""
    with patch("app.services.identity_service._get_sms_provider") as mock_get:
        mock_provider = MagicMock()
        mock_get.return_value = mock_provider

        with patch("app.services.identity_service.settings") as mock_settings:
            mock_settings.OTP_SMS_TTL_SECONDS = 300

            from app.services.identity_service import _deliver_otp

            _deliver_otp("sms", "+27123456789", "654321")

        mock_provider.send.assert_called_once()
        call_args = mock_provider.send.call_args
        # First positional arg is the destination
        to_arg = call_args[0][0]
        assert to_arg == "+27123456789"
        # Second positional arg is the body (must not be None)
        body_arg = call_args[0][1]
        assert body_arg is not None and len(body_arg) > 0


def test_null_sms_provider_raises():
    """NullSmsProvider.send raises ProviderNotConfiguredError."""
    from app.services.identity_service import NullSmsProvider, ProviderNotConfiguredError

    provider = NullSmsProvider()
    with pytest.raises(ProviderNotConfiguredError):
        provider.send("+27123456789", "Your code is 123456")


# ---------------------------------------------------------------------------
# Task 3: request_otp / verify_otp / check_verified_session orchestration
# ---------------------------------------------------------------------------


def _make_mock_psycopg2_conn(fetchone_return=None):
    """Build a MagicMock psycopg2 connection with a working cursor context manager."""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = fetchone_return
    mock_cursor_ctx = MagicMock()
    mock_cursor_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor_ctx.__exit__ = MagicMock(return_value=None)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor_ctx
    return mock_conn, mock_cursor


async def test_otp_verify_success():
    """Correct code: Redis key deleted FIRST, then UPSERT writes session_token_hash."""
    from app.services.identity_service import (
        hash_otp_code,
        hash_session_token,
        verify_otp,
    )

    # Prepare a challenge with the correct hash stored
    code = "247891"
    code_hash = hash_otp_code(code)
    challenge_json = json.dumps({"hash": code_hash, "attempts": 0})

    redis = AsyncMock()
    redis.get.return_value = challenge_json

    call_order: list[str] = []

    async def tracked_delete(*args, **kwargs):
        call_order.append("redis_delete")

    redis.delete.side_effect = tracked_delete

    # Capture execute args so we can verify hash (not raw token) was stored
    execute_calls: list[tuple] = []
    mock_conn, mock_cursor = _make_mock_psycopg2_conn()

    def tracked_execute(*args, **kwargs):
        call_order.append("psycopg2_execute")
        execute_calls.append(args)

    mock_cursor.execute = tracked_execute

    with patch("psycopg2.connect", return_value=mock_conn):
        raw_token = await verify_otp(
            redis, "agent-1", "user@example.com", code, "email", "postgresql://mock"
        )

    # Delete-first (T-17-05): Redis key deleted before UPSERT
    assert call_order == ["redis_delete", "psycopg2_execute"], (
        f"Expected delete before upsert, got: {call_order}"
    )

    # Raw token is returned
    assert isinstance(raw_token, str)
    assert len(raw_token) >= 43

    # session_token_hash (not raw_token) was stored in the DB params
    sql_params = execute_calls[0][1]  # (sql, params) → second element
    assert raw_token not in sql_params, "Raw token must not be stored in DB"
    expected_hash = hash_session_token(raw_token)
    assert expected_hash in sql_params, "session_token_hash must be in UPSERT params"


async def test_otp_wrong_code():
    """Wrong code: OtpInvalid raised, attempts incremented, key NOT deleted."""
    from app.services.identity_service import OtpInvalid, hash_otp_code, verify_otp

    code_hash = hash_otp_code("123456")
    challenge_json = json.dumps({"hash": code_hash, "attempts": 0})

    redis = AsyncMock()
    redis.get.return_value = challenge_json

    with pytest.raises(OtpInvalid):
        await verify_otp(
            redis, "agent-1", "user@test.com", "999999", "email", "postgresql://mock"
        )

    # Key NOT deleted (T-17-05 — single-use only on correct code)
    redis.delete.assert_not_called()
    # Attempts counter persisted back with TTL preserved (CR-01)
    redis.set.assert_called_once()
    _, set_kwargs = redis.set.call_args
    assert set_kwargs.get("keepttl") is True, (
        "keepttl=True must be passed to preserve the OTP expiry window (CR-01)"
    )


async def test_otp_expired():
    """Absent/expired challenge returns OtpInvalid (no-oracle: same 400 as wrong code)."""
    from app.services.identity_service import OtpInvalid, verify_otp

    redis = AsyncMock()
    redis.get.return_value = None  # key expired / never set

    with pytest.raises(OtpInvalid):
        await verify_otp(
            redis, "agent-1", "user@test.com", "123456", "email", "postgresql://mock"
        )


async def test_otp_lockout():
    """5th wrong attempt (attempts reaches OTP_MAX_ATTEMPTS=5) → OtpRateLimited."""
    from app.services.identity_service import OtpRateLimited, hash_otp_code, verify_otp

    # Challenge already has 4 failed attempts
    code_hash = hash_otp_code("123456")
    challenge_json = json.dumps({"hash": code_hash, "attempts": 4})

    redis = AsyncMock()
    redis.get.return_value = challenge_json

    with pytest.raises(OtpRateLimited):
        await verify_otp(
            redis, "agent-1", "user@test.com", "999999", "email", "postgresql://mock"
        )

    # Key NOT deleted (T-17-05 — only deleted on correct code)
    redis.delete.assert_not_called()


async def test_check_verified_session():
    """Valid token hash present in DB with future expiry → returns True."""
    from app.services.identity_service import check_verified_session

    mock_conn, mock_cursor = _make_mock_psycopg2_conn(fetchone_return=(1,))

    with patch("psycopg2.connect", return_value=mock_conn):
        result = await check_verified_session(
            "agent-1", "valid_raw_session_token", "postgresql://mock"
        )

    assert result is True

    # Verify agent_id is NOT in the SQL WHERE clause (OD-1 per-tenant, T-17-01)
    sql = mock_cursor.execute.call_args[0][0]
    assert "agent_id" not in sql.lower()


async def test_session_expiry():
    """No matching/non-expired row in DB → check_verified_session returns False."""
    from app.services.identity_service import check_verified_session

    mock_conn, mock_cursor = _make_mock_psycopg2_conn(fetchone_return=None)

    with patch("psycopg2.connect", return_value=mock_conn):
        result = await check_verified_session(
            "agent-1", "expired_or_absent_token", "postgresql://mock"
        )

    assert result is False


async def test_request_otp_send_limit():
    """Exceeding OTP_SEND_MAX_PER_WINDOW raises OtpRateLimited."""
    from app.services.identity_service import OtpRateLimited, request_otp
    from app.core.config import settings

    redis = AsyncMock()
    # incr returns a count above the limit
    redis.incr.return_value = settings.OTP_SEND_MAX_PER_WINDOW + 1

    with pytest.raises(OtpRateLimited):
        await request_otp(redis, "agent-1", "user@test.com", "email")
