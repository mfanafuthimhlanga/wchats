"""
transactional.credential_service — Per-tenant credential storage and in-memory handle.

INT-01: Derives a per-tenant Fernet key from the platform master key (PLATFORM_CREDENTIAL_KEY)
        via HKDF(SHA-256, salt=tenant_id) and reads integration_credentials from the tenant DB.

INT-02: Returns a CredentialHandle wrapping the decrypted credential so the raw value
        never appears in structlog output, exception messages, ContextVars, Celery task
        args, or JSON serialization.

Security invariants enforced here:
  T-16-01: CredentialHandle.__repr__ / __str__ always return the redacted marker.
  T-16-06: tenant_id flows via _tenant_id_var ContextVar (set by bind_tool_context),
            never as a Celery task arg.
  T-16-10: _derive_tenant_fernet creates a FRESH HKDF instance per call (HKDF.derive()
            is single-use). Never cache the HKDF instance.

CRITICAL pitfall (Pitfall 1 in RESEARCH.md):
    HKDF.derive() is single-use per instance — calling it twice raises AlreadyFinalized.
    Always construct a new HKDF(...) object inside _derive_tenant_fernet. Do NOT move
    the HKDF constructor to module level or cache the result.

IMPORTANT: Do NOT add a reference to the Neon encryption key here — per-tenant credentials
use the separate PLATFORM_CREDENTIAL_KEY with a per-tenant HKDF salt (different key material,
different purpose, different derivation).
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass

import psycopg2
import structlog
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class ProviderNotConfiguredError(Exception):
    """Raised when no integration_credentials row is found for the requested skill."""


class CredentialDecryptionError(Exception):
    """Raised when the Fernet decryption of credential_data fails (wrong key, tampered data)."""


# ---------------------------------------------------------------------------
# CredentialHandle — in-memory secret wrapper (INT-02, T-16-01)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CredentialHandle:
    """In-memory wrapper for a decrypted provider credential.

    Intentionally non-serializable: __repr__ and __str__ return the redacted marker
    so the raw credential never appears in structlog output, exception tracebacks,
    or any JSON serialization path.

    Lifetime: created immediately before adapter call, discarded after the adapter
    method returns (garbage collected). Never stored in ContextVars, module state,
    Redis, or any persistent storage.

    Usage:
        handle = CredentialHandle(_raw=decrypted_string)
        client = stripe.StripeClient(handle.use())  # raw value inside use() only
    """

    _raw: str

    def use(self) -> str:
        """Return the raw credential for direct SDK initialization.

        Only call this inside the provider adapter constructor — never log the result.
        """
        return self._raw

    def __repr__(self) -> str:
        return "<CredentialHandle:redacted>"

    __str__ = __repr__


# ---------------------------------------------------------------------------
# HKDF per-tenant key derivation (INT-01, T-16-10)
# ---------------------------------------------------------------------------


def _derive_tenant_fernet(platform_master_key: bytes, tenant_id: str) -> Fernet:
    """Derive a per-tenant Fernet instance using HKDF(SHA-256).

    Args:
        platform_master_key: raw bytes of PLATFORM_CREDENTIAL_KEY (decoded from URL-safe b64).
        tenant_id: the tenant UUID string — used as HKDF salt for per-tenant uniqueness.

    Returns:
        A Fernet instance keyed uniquely for this tenant.

    CRITICAL: A fresh HKDF instance is constructed on every call.
        HKDF.derive() is single-use per instance — calling .derive() a second time
        raises cryptography.exceptions.AlreadyFinalized. Do NOT cache the HKDF object.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=tenant_id.encode("utf-8"),  # per-tenant salt → per-tenant unique key
        info=b"wchats-integration-credential",  # application-specific context string
    )
    derived_key = hkdf.derive(platform_master_key)
    fernet_key = base64.urlsafe_b64encode(derived_key)
    return Fernet(fernet_key)


# ---------------------------------------------------------------------------
# _CredentialConfig — internal result type from _fetch_credential_config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CredentialConfig:
    """Internal result from _fetch_credential_config.

    Fields match the integration_credentials table columns returned by the SELECT.
    credential_data is raw bytes (Fernet-encrypted BYTEA) — decryption happens in
    get_adapter_for_skill(), not here.
    """

    provider_type: str
    credential_data: bytes  # encrypted BYTEA — must not be logged
    config_data: dict
    currency_code: str


# ---------------------------------------------------------------------------
# _fetch_credential_config — async tenant-DB reader (INT-02)
# ---------------------------------------------------------------------------


async def _fetch_credential_config(conn_str: str, skill: str) -> _CredentialConfig | None:
    """Fetch the integration_credentials row that serves the requested skill.

    Returns None immediately if conn_str is falsy (no DB call attempted).
    Returns None if no row matches (ProviderNotConfiguredError raised by caller).
    Never logs credential_data or any decrypted value.

    Args:
        conn_str: Decrypted tenant DB connection string (from _conn_str_var ContextVar).
                  Callers read _conn_str_var into a local before calling this function
                  (ContextVar read-before-thread pattern — see agent_tools.py).
        skill: Canonical skill name (e.g. "issue_refund"). Used in a JSONB array
               containment query against enabled_skills.

    Returns:
        _CredentialConfig with provider_type, credential_data, config_data, currency_code,
        or None if no row found.
    """
    if not conn_str:
        return None

    # Capture locals before asyncio.to_thread — ContextVars are not propagated to
    # thread-pool threads (ContextVar read-before-thread pattern, RESEARCH.md).
    _conn_str = conn_str
    _skill = skill

    def _sync_fetch() -> _CredentialConfig | None:
        """Synchronous psycopg2 fetch — runs in a thread pool via asyncio.to_thread."""
        conn = psycopg2.connect(_conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                # JSONB array containment: enabled_skills @> '["issue_refund"]'::jsonb
                # Avoids the bare ? operator ambiguity in psycopg2 (use %s::jsonb instead).
                cur.execute(
                    """
                    SELECT provider_type, credential_data, config_data, currency_code
                    FROM integration_credentials
                    WHERE enabled_skills @> %s::jsonb
                    LIMIT 1
                    """,
                    (json.dumps([_skill]),),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        provider_type, credential_data, config_data, currency_code = row
        # config_data may come back as dict (psycopg2 JSONB auto-decode) or str
        if isinstance(config_data, str):
            config_data = json.loads(config_data)
        return _CredentialConfig(
            provider_type=provider_type,
            credential_data=bytes(credential_data),  # ensure bytes (BYTEA -> memoryview)
            config_data=config_data or {},
            currency_code=currency_code or "USD",
        )

    try:
        return await asyncio.to_thread(_sync_fetch)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "credential_service.fetch_failed",
            skill=skill,
            error=str(exc),
        )
        return None
