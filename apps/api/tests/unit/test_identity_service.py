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
