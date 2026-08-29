"""
Unit tests for credential_service.py — INT-01 / INT-02 invariants.

Wave 0 (Phase 16, Plan 01) — credential substrate tests:

  test_hkdf_per_tenant_isolation:
      Two different tenant_ids derive different Fernet keys from the same platform
      master key. Ciphertext encrypted with tenant-A's key cannot be decrypted with
      tenant-B's key (raises cryptography.fernet.InvalidToken). This proves T-16-10.

  test_handle_repr_redacted:
      CredentialHandle(_raw="sk_live_secret_credential") repr returns the literal
      redacted marker. The raw value must not appear in __repr__ or __str__. .use()
      returns the raw value. This proves T-16-01.

  test_no_credential_in_tool_schema:
      For every TOOL_REGISTRY entry with mutating=True, the Pydantic Input schema
      contains no property key matching {api_key, credential, secret, password,
      token, access_token}. Also verifies sdk_tool schema when populated.
      Proves the "no credential in agent-facing schema" invariant.

  test_fetch_credential_config_none_when_missing:
      _fetch_credential_config("", "issue_refund") returns None without making any
      psycopg2 connection attempt (empty conn_str early-return path).

Test infrastructure:
  asyncio_mode = "auto" in pyproject.toml — all async def tests run automatically.
  No real DB or Redis needed: empty-conn_str path is pure Python.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

# ---------------------------------------------------------------------------
# test_hkdf_per_tenant_isolation — T-16-10
# ---------------------------------------------------------------------------


def test_hkdf_per_tenant_isolation() -> None:
    """Two tenant IDs derive different Fernet keys from the same master key.

    Ciphertext encrypted under tenant-A's key raises InvalidToken when decrypted
    with tenant-B's key. Calling _derive_tenant_fernet twice must NOT raise
    AlreadyFinalized (HKDF instance is fresh per call).
    """
    from app.services.transactional.credential_service import _derive_tenant_fernet

    master_key = b"\x00" * 32  # deterministic 32-byte master key for testing

    fernet_a = _derive_tenant_fernet(master_key, "tenant-aaa")
    fernet_b = _derive_tenant_fernet(master_key, "tenant-bbb")

    # Encrypt a message with tenant-A's key
    ciphertext = fernet_a.encrypt(b"super-secret-api-key")

    # tenant-A can decrypt its own ciphertext
    assert fernet_a.decrypt(ciphertext) == b"super-secret-api-key"

    # tenant-B CANNOT decrypt tenant-A's ciphertext
    with pytest.raises(InvalidToken):
        fernet_b.decrypt(ciphertext)

    # Calling _derive_tenant_fernet a second time for the same tenant must not
    # raise AlreadyFinalized — each call must create a fresh HKDF instance.
    fernet_a2 = _derive_tenant_fernet(master_key, "tenant-aaa")
    assert fernet_a2.decrypt(ciphertext) == b"super-secret-api-key"


# ---------------------------------------------------------------------------
# test_handle_repr_redacted — T-16-01
# ---------------------------------------------------------------------------


def test_handle_repr_redacted() -> None:
    """CredentialHandle repr/str is the redacted marker; .use() returns the raw value."""
    from app.services.transactional.credential_service import CredentialHandle

    raw = "sk_live_secret_credential"
    handle = CredentialHandle(_raw=raw)

    # repr must be the literal redacted marker
    assert repr(handle) == "<CredentialHandle:redacted>"

    # str must also be the redacted marker
    assert str(handle) == "<CredentialHandle:redacted>"

    # The raw value must not appear in repr or str
    assert raw not in repr(handle)
    assert "secret" not in repr(handle)
    assert raw not in str(handle)

    # .use() must return the raw value
    assert handle.use() == raw


# ---------------------------------------------------------------------------
# test_no_credential_in_tool_schema — INT-02 invariant
# ---------------------------------------------------------------------------


def test_no_credential_in_tool_schema() -> None:
    """No transactional tool Input schema exposes credential-like field names.

    Checks the Pydantic model_json_schema() properties for all mutating tools.
    The tool contract (T-14-02-01) forbids api_key, credential, secret, password,
    token, and access_token from appearing as input schema property keys.
    """
    # Pydantic Input model map — import the Input schemas directly
    from app.domain.transactional_schemas import (
        BookSlotInput,
        CancelOrderInput,
        IssueRefundInput,
        PlaceOrderInput,
        UpdateCustomerRecordInput,
        UpdateSubscriptionInput,
    )
    from app.services.transactional.registry import TOOL_REGISTRY

    _INPUT_MODELS = {
        "place_order": PlaceOrderInput,
        "cancel_order": CancelOrderInput,
        "issue_refund": IssueRefundInput,
        "update_subscription": UpdateSubscriptionInput,
        "book_slot": BookSlotInput,
        "update_customer_record": UpdateCustomerRecordInput,
    }

    _FORBIDDEN_KEYS = {
        "api_key",
        "credential",
        "secret",
        "password",
        "token",
        "access_token",
    }

    for skill_name, tool_def in TOOL_REGISTRY.items():
        if not tool_def.mutating:
            continue

        # Check Pydantic schema
        if skill_name in _INPUT_MODELS:
            schema = _INPUT_MODELS[skill_name].model_json_schema()
            props = set(schema.get("properties", {}).keys())
            violations = props & _FORBIDDEN_KEYS
            assert not violations, (
                f"Tool '{skill_name}' Input schema exposes credential fields: {violations}. "
                "Credentials must never appear in agent-facing tool schemas (INT-02)."
            )

        # Also check sdk_tool.input_schema if populated
        if tool_def.sdk_tool is not None:
            sdk_schema = getattr(tool_def.sdk_tool, "input_schema", {}) or {}
            sdk_props = set(sdk_schema.get("properties", {}).keys())
            sdk_violations = sdk_props & _FORBIDDEN_KEYS
            assert not sdk_violations, (
                f"Tool '{skill_name}' sdk_tool.input_schema exposes credential fields: "
                f"{sdk_violations}. Credentials must never appear in agent-facing schemas."
            )


# ---------------------------------------------------------------------------
# test_fetch_credential_config_none_when_missing — empty conn_str path
# ---------------------------------------------------------------------------


async def test_fetch_credential_config_none_when_missing() -> None:
    """_fetch_credential_config with empty conn_str returns None immediately.

    No psycopg2 connection must be attempted. Verifies the early-return guard
    that protects against calling a sync DB operation on an empty/invalid conn_str.
    """
    from unittest.mock import patch

    from app.services.transactional.credential_service import _fetch_credential_config

    with patch("psycopg2.connect") as mock_connect:
        result = await _fetch_credential_config("", "issue_refund")

    assert result is None
    mock_connect.assert_not_called()
