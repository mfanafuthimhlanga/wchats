"""
Unit tests for app.core.security — RED phase (TDD).

Tests: Fernet round-trip, argon2 hash/verify, generate_api_key prefix.
Environment: NEON_ENCRYPTION_KEY must be set before importing security module.
"""

import base64
import os

import pytest

# Set a valid test key before importing settings/security.
# Key must be URL-safe base64-encoded 32 bytes.
_TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
os.environ.setdefault("NEON_ENCRYPTION_KEY", _TEST_KEY)
os.environ.setdefault("NEON_API_KEY", "test_neon_api_key")
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")

from app.core.security import (  # noqa: E402
    fernet_decrypt,
    fernet_encrypt,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)

# ---------------------------------------------------------------------------
# Fernet encrypt / decrypt
# ---------------------------------------------------------------------------


class TestFernetEncryption:
    def test_encrypt_returns_bytes(self):
        ct = fernet_encrypt("postgresql://user:pass@host/db")
        assert isinstance(ct, bytes), "fernet_encrypt must return bytes, not str"

    def test_round_trip(self):
        plaintext = "postgresql://user:pass@host/db"
        ct = fernet_encrypt(plaintext)
        assert fernet_decrypt(ct) == plaintext

    def test_round_trip_empty_string(self):
        plaintext = ""
        ct = fernet_encrypt(plaintext)
        assert fernet_decrypt(ct) == plaintext

    def test_different_ciphertexts_same_plaintext(self):
        """Fernet uses a random IV; encrypting twice yields different ciphertexts."""
        pt = "postgresql://test"
        ct1 = fernet_encrypt(pt)
        ct2 = fernet_encrypt(pt)
        assert ct1 != ct2

    def test_decrypt_invalid_bytes_raises(self):
        """fernet_decrypt must raise (InvalidToken) on bad input, not return garbage."""
        from cryptography.fernet import InvalidToken

        with pytest.raises((InvalidToken, Exception)):
            fernet_decrypt(b"definitely_not_valid_ciphertext")


# ---------------------------------------------------------------------------
# Argon2 API key hashing
# ---------------------------------------------------------------------------


class TestArgon2Hashing:
    def test_hash_returns_str(self):
        h = hash_api_key("vrd_live_abc123")
        assert isinstance(h, str)

    def test_hash_starts_with_argon2(self):
        h = hash_api_key("vrd_live_abc123")
        assert h.startswith("$argon2"), f"Expected argon2 hash, got: {h[:20]}"

    def test_verify_correct_key_returns_true(self):
        raw = "vrd_live_abc123"
        h = hash_api_key(raw)
        assert verify_api_key(h, raw) is True

    def test_verify_wrong_key_returns_false(self):
        h = hash_api_key("vrd_live_abc123")
        result = verify_api_key(h, "wrong_key")
        assert result is False, "verify_api_key must return False (not raise) on mismatch"

    def test_verify_does_not_raise_on_mismatch(self):
        """Rule: verify_api_key returns bool only, never raises VerifyMismatchError."""
        h = hash_api_key("vrd_live_test")
        try:
            result = verify_api_key(h, "completely_wrong_key")
        except Exception as exc:
            pytest.fail(f"verify_api_key raised {type(exc).__name__} instead of returning False")
        assert result is False


# ---------------------------------------------------------------------------
# API key generation
# ---------------------------------------------------------------------------


class TestGenerateApiKey:
    def test_starts_with_prefix(self):
        key = generate_api_key()
        assert key.startswith("vrd_live_"), f"Key must start with 'vrd_live_', got: {key[:20]}"

    def test_returns_str(self):
        key = generate_api_key()
        assert isinstance(key, str)

    def test_uniqueness(self):
        keys = {generate_api_key() for _ in range(10)}
        assert len(keys) == 10, "generate_api_key should return unique values"


# ---------------------------------------------------------------------------
# Security assertions — no plaintext exposure
# ---------------------------------------------------------------------------


class TestSecurityConstraints:
    def test_no_bcrypt_or_hashlib(self):
        """Acceptance criterion: module must not use bcrypt or standalone hashlib for key derivation.

        hashlib is permitted only as a digest parameter to hmac (HMAC-SHA256 prefix for O(1)
        lookup). Direct hashlib key hashing (replacing argon2) is forbidden.
        """
        import importlib.util

        spec = importlib.util.find_spec("app.core.security")
        assert spec is not None
        source = spec.origin
        with open(source) as f:
            src = f.read()
        assert "bcrypt" not in src, "security.py must not use bcrypt"
        # Forbid standalone hashlib calls used as key-derivation (e.g. hashlib.sha256(key).hexdigest()).
        # hashlib.sha256 as a digest *parameter* to hmac.new() is permitted (see hmac_key_prefix).
        assert "hashlib.sha256(" not in src, "security.py must not call hashlib.sha256() directly"
        assert "hashlib.md5(" not in src, "security.py must not call hashlib.md5() directly"
