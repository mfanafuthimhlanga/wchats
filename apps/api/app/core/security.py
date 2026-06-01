"""
Security helpers for W Chats control plane.

Provides:
    fernet_encrypt  — encrypt a plaintext str → bytes (for BYTEA storage)
    fernet_decrypt  — decrypt bytes → plaintext str
    hash_api_key    — argon2id hash of a raw API key → stored hash str
    verify_api_key  — constant-time compare: returns bool, never raises on mismatch
    generate_api_key — generate a "vrd_live_" prefixed URL-safe token

Threat mitigations:
    T-02-01: fernet_decrypt return value must never be passed to a logger — caller's responsibility.
    T-02-02: verify_api_key returns bool only; raw key never stored in module state.
    T-02-04: JSON serializer enforced at Celery level; this module has no pickle dependency.

Use Fernet + argon2-cffi only. Do not bring in standard-library key-derivation modules.
"""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet

from app.core.config import settings

# ---------------------------------------------------------------------------
# Module-level Argon2 singleton — avoids recreating tuning params per call.
# ---------------------------------------------------------------------------
_ph = PasswordHasher()


# ---------------------------------------------------------------------------
# Fernet helpers
# ---------------------------------------------------------------------------


def _get_fernet() -> Fernet:
    """Build a Fernet instance from NEON_ENCRYPTION_KEY.

    The key must be a URL-safe base64-encoded 32-byte value.
    Fernet raises ValueError at construction time if the key is malformed,
    giving a fast startup failure rather than a silent crypto error.
    """
    key_bytes: bytes = settings.NEON_ENCRYPTION_KEY.encode()
    return Fernet(key_bytes)


def fernet_encrypt(plaintext: str) -> bytes:
    """Encrypt *plaintext* and return ciphertext bytes suitable for BYTEA storage.

    Returns bytes — not str.  Store directly as a BYTEA column value.
    Each call produces a different ciphertext (random IV) for the same plaintext.
    """
    return _get_fernet().encrypt(plaintext.encode())


def fernet_decrypt(ciphertext: bytes) -> str:
    """Decrypt *ciphertext* and return the original plaintext string.

    Raises cryptography.fernet.InvalidToken on corrupt or tampered input.
    Do NOT catch this exception here — let the caller decide how to handle it.
    Never pass the return value to a log statement (T-02-01).
    """
    return _get_fernet().decrypt(ciphertext).decode()


# ---------------------------------------------------------------------------
# Argon2 API key hashing
# ---------------------------------------------------------------------------


def hash_api_key(raw_key: str) -> str:
    """Hash *raw_key* with argon2id and return the encoded hash string.

    The returned string starts with "$argon2id$" and is safe to store in the
    database ``api_key`` column.  The raw key is never persisted or logged.
    """
    return _ph.hash(raw_key)


def verify_api_key(stored_hash: str, raw_key: str) -> bool:
    """Verify *raw_key* against *stored_hash*.

    Returns True on match, False on mismatch.  Never raises on any argon2
    exception — always returns a bool so callers can write
    ``if verify_api_key(...)`` without try/except (T-02-02).

    Catches all three argon2 exception types:
        VerifyMismatchError — wrong key (expected case)
        VerificationError   — hash computation failed (corrupted params)
        InvalidHashError    — stored value is not a valid argon2 hash

    The comparison is timing-safe (argon2-cffi uses a constant-time comparison
    internally, providing resistance against timing-based side-channel attacks).
    """
    try:
        return _ph.verify(stored_hash, raw_key)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ---------------------------------------------------------------------------
# HMAC key prefix — for O(1) indexed lookup (WR-01)
# ---------------------------------------------------------------------------


def hmac_key_prefix(raw_key: str) -> str:
    """Return the first 16 hex chars of HMAC-SHA256(raw_key, ADMIN_KEY).

    The prefix is deterministic for a given raw_key and is stored in the
    ``api_key_prefix`` column of the tenants table with a DB index.
    get_current_tenant filters by this prefix first, then runs a single
    argon2 verify() — reducing auth cost from O(N) to O(1).

    The prefix is NOT secret by itself (it does not allow key recovery),
    but it is keyed with ADMIN_KEY so that an attacker with only read-DB
    access cannot precompute a rainbow table without also knowing ADMIN_KEY.
    """
    return hmac.new(
        settings.ADMIN_KEY.encode(),
        raw_key.encode(),
        hashlib.sha256,
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# API key generation
# ---------------------------------------------------------------------------


def generate_api_key() -> str:
    """Generate a new API key for a tenant.

    Format: ``vrd_live_{token}`` where token is 43 chars of URL-safe base64
    (secrets.token_urlsafe(32) = 256 bits of entropy).

    The ``vrd_live_`` prefix makes keys identifiable in logs before masking.
    """
    return "vrd_live_" + secrets.token_urlsafe(32)
