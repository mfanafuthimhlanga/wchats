"""
Unit tests for JWT helper functions in app.api.v1.widget.

Tests:
    1. create_widget_jwt returns a JWT that decodes with the correct agent_id claim
    2. JWT expiry is approximately 900 seconds in the future (±60s window)
    3. Tampered token (flipped signature character) raises 401
    4. Expired token raises 401
    5. Mismatched agent_id between token and expected raises 401 with correct detail

Security coverage:
    T-04-04-01: 15-min expiry caps blast radius
    T-04-04-02: agent_id claim mismatch → 401
    T-04-04-03: signature manipulation → 401
"""

import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jose import jwt as jose_jwt

# conftest.py sets required env vars before any app import
from app.api.v1.widget import create_widget_jwt, validate_widget_jwt
from app.core.config import settings

# ---------------------------------------------------------------------------
# Test 1: Decoded JWT contains the correct agent_id claim
# ---------------------------------------------------------------------------


def test_create_widget_jwt_contains_correct_agent_id():
    """JWT decoded with the correct secret must contain the expected agent_id."""
    agent_id = str(uuid4())
    token = create_widget_jwt(agent_id)

    claims = jose_jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    assert claims["agent_id"] == agent_id
    assert claims["sub"] == "widget"


# ---------------------------------------------------------------------------
# Test 2: JWT expiry is approximately 900 seconds from now
# ---------------------------------------------------------------------------


def test_create_widget_jwt_expiry_is_approximately_900_seconds():
    """exp claim must be within 880–920 seconds of now (±60s tolerance)."""
    agent_id = str(uuid4())
    token = create_widget_jwt(agent_id)

    claims = jose_jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    now_ts = datetime.now(timezone.utc).timestamp()
    delta = claims["exp"] - now_ts

    assert 880 < delta < 920, f"Expected expiry ~900s from now, got {delta:.1f}s"


# ---------------------------------------------------------------------------
# Test 3: Tampered token raises 401
# ---------------------------------------------------------------------------


def test_validate_widget_jwt_tampered_token_raises_401():
    """Flipping a character in the signature segment must raise HTTPException 401."""
    agent_id = str(uuid4())
    token = create_widget_jwt(agent_id)

    # Split JWT into header.payload.signature, then corrupt the signature at the
    # BYTE level. Tampering the base64url text instead is silently flaky: an
    # HS256 signature is 32 bytes = 43 base64url chars, so the final char encodes
    # only 4 significant bits and its low 2 bits are unused. Flipping that char
    # to 'b' decodes to identical bytes whenever it was 'Y' (1 of the 16 legal
    # final chars) — the signature still verifies and no 401 is raised, failing
    # this test on ~6% of runs.
    parts = token.split(".")
    assert len(parts) == 3, "JWT must have three dot-separated segments"
    sig_bytes = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    tampered_bytes = bytes([sig_bytes[0] ^ 0xFF]) + sig_bytes[1:]
    assert tampered_bytes != sig_bytes, "tamper must actually change the signature"
    tampered_sig = base64.urlsafe_b64encode(tampered_bytes).rstrip(b"=").decode()
    tampered_token = ".".join([parts[0], parts[1], tampered_sig])

    with pytest.raises(HTTPException) as exc_info:
        validate_widget_jwt(tampered_token, agent_id)

    assert exc_info.value.status_code == 401
    assert "Invalid or expired token" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Test 4: Expired token raises 401
# ---------------------------------------------------------------------------


def test_validate_widget_jwt_expired_token_raises_401():
    """A token with exp in the past must raise HTTPException 401."""
    agent_id = str(uuid4())
    # Manually craft an expired token
    expired_payload = {
        "sub": "widget",
        "agent_id": agent_id,
        "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    expired_token = jose_jwt.encode(
        expired_payload, settings.JWT_SECRET, algorithm="HS256"
    )

    with pytest.raises(HTTPException) as exc_info:
        validate_widget_jwt(expired_token, agent_id)

    assert exc_info.value.status_code == 401
    assert "Invalid or expired token" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Test 5: Mismatched agent_id raises 401 with correct detail string
# ---------------------------------------------------------------------------


def test_validate_widget_jwt_mismatched_agent_id_raises_401():
    """Token issued for agent_a but validated against agent_b must raise 401."""
    agent_id_a = str(uuid4())
    agent_id_b = str(uuid4())

    token = create_widget_jwt(agent_id_a)

    with pytest.raises(HTTPException) as exc_info:
        validate_widget_jwt(token, agent_id_b)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token agent_id mismatch"
