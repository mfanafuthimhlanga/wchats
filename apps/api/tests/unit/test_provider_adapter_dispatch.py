"""
Unit tests for provider_adapter.get_adapter_for_skill — INT-02 dispatch coverage.

Phase 16 Plan 06 (Task 1 — RED/GREEN):

Validates that get_adapter_for_skill:
  - fetches + decrypts the tenant credential from integration_credentials
  - dispatches to the correct concrete adapter class for each provider_type
  - raises ProviderNotConfiguredError when no row is found (None from _fetch_credential_config)
  - raises CredentialDecryptionError when ciphertext was encrypted under a different tenant key
  - raises ProviderNotConfiguredError for an unknown provider_type

Security invariants verified:
  T-16-01: the raw credential string never appears in repr/str (CredentialHandle is redacted)
  T-16-06: raw credential never crosses test assertions — only adapter class identity is checked
  T-16-02: shop_url/site_url come from config_data, never from tool args

Test infrastructure:
  asyncio_mode = "auto" in pyproject.toml — all async def tests run automatically.
  No real DB needed — _fetch_credential_config is mocked throughout.
  PLATFORM_CREDENTIAL_KEY is set to a deterministic value per test via environment patching.
  _tenant_id_var is set directly (not via build_tool_server) to exercise the ContextVar path.
"""

from __future__ import annotations

import base64
import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

# Deterministic 32-byte master key for all dispatch tests.
# Same key used both to encrypt test credentials and to decode settings.PLATFORM_CREDENTIAL_KEY.
_TEST_MASTER_BYTES = b"\xab" * 32
_TEST_PLATFORM_KEY = base64.urlsafe_b64encode(_TEST_MASTER_BYTES).decode()
_TEST_TENANT_ID = "tenant-dispatch-test-001"
_TEST_AGENT_ID = "agent-dispatch-001"
_TEST_CONN_STR = "postgresql://test:test@localhost/test"


# ---------------------------------------------------------------------------
# Helper: derive the Fernet instance the factory uses for _TEST_TENANT_ID
# ---------------------------------------------------------------------------


def _make_test_fernet(tenant_id: str = _TEST_TENANT_ID) -> Fernet:
    """Return the same Fernet that get_adapter_for_skill would derive for this tenant."""
    from app.services.transactional.credential_service import _derive_tenant_fernet

    return _derive_tenant_fernet(_TEST_MASTER_BYTES, tenant_id)


def _encrypt(payload: dict, tenant_id: str = _TEST_TENANT_ID) -> bytes:
    """Encrypt a credential payload with the per-tenant Fernet."""
    return _make_test_fernet(tenant_id).encrypt(json.dumps(payload).encode("utf-8"))


# ---------------------------------------------------------------------------
# Helper: build a _CredentialConfig for each provider type
# ---------------------------------------------------------------------------


def _stripe_config():
    from app.services.transactional.credential_service import _CredentialConfig

    return _CredentialConfig(
        provider_type="stripe",
        credential_data=_encrypt({"api_key": "rk_test_stripe_key"}),
        config_data={},
        currency_code="USD",
    )


def _shopify_config():
    from app.services.transactional.credential_service import _CredentialConfig

    return _CredentialConfig(
        provider_type="shopify",
        credential_data=_encrypt({"access_token": "shpat_test_token"}),
        config_data={"shop_url": "teststore.myshopify.com"},
        currency_code="USD",
    )


def _woocommerce_config():
    from app.services.transactional.credential_service import _CredentialConfig

    return _CredentialConfig(
        provider_type="woocommerce",
        credential_data=_encrypt({"consumer_key": "ck_test", "consumer_secret": "cs_test"}),
        config_data={"site_url": "https://teststore.example.com"},
        currency_code="GBP",
    )


def _calendly_config():
    from app.services.transactional.credential_service import _CredentialConfig

    return _CredentialConfig(
        provider_type="calendly",
        credential_data=_encrypt({"personal_access_token": "eyJ_test_PAT_xyz"}),
        config_data={
            "event_types": {
                "consultation": "https://api.calendly.com/event_types/TEST_UUID_1"
            }
        },
        currency_code="USD",
    )


# ---------------------------------------------------------------------------
# Helper: context manager that sets up the standard mocks for every dispatch test
# ---------------------------------------------------------------------------

from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def _dispatch_ctx(config_or_none, tenant_id: str = _TEST_TENANT_ID):
    """Set _tenant_id_var + mock _fetch_credential_config + patch PLATFORM_CREDENTIAL_KEY.

    Patches _fetch_credential_config at the provider_adapter module level (where the name
    is bound after the module-level `from ... import` statement). Patching the source
    module (credential_service) would NOT intercept calls from provider_adapter.py.
    """
    from app.services.agent_tools import _tenant_id_var  # noqa: PLC0415

    _tenant_id_var.set(tenant_id)

    with (
        # Patch the name as it exists in provider_adapter's namespace (not credential_service's)
        patch(
            "app.services.transactional.provider_adapter._fetch_credential_config",
            AsyncMock(return_value=config_or_none),
        ),
        # Patch PLATFORM_CREDENTIAL_KEY on the already-instantiated settings singleton
        patch(
            "app.services.transactional.provider_adapter.settings.PLATFORM_CREDENTIAL_KEY",
            _TEST_PLATFORM_KEY,
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# test_adapter_dispatch_stripe
# ---------------------------------------------------------------------------


async def test_adapter_dispatch_stripe() -> None:
    """provider_type='stripe' → returns a StripeAdapter instance."""
    from app.services.transactional.adapters.stripe_adapter import StripeAdapter
    from app.services.transactional.provider_adapter import get_adapter_for_skill

    with _dispatch_ctx(_stripe_config()):
        adapter = await get_adapter_for_skill("issue_refund", _TEST_AGENT_ID, _TEST_CONN_STR)

    assert isinstance(adapter, StripeAdapter), (
        f"Expected StripeAdapter, got {type(adapter).__name__}"
    )
    # T-16-01: CredentialHandle must not appear in adapter repr (not stored at module level)
    assert "rk_test_stripe_key" not in repr(adapter)


# ---------------------------------------------------------------------------
# test_adapter_dispatch_shopify
# ---------------------------------------------------------------------------


async def test_adapter_dispatch_shopify() -> None:
    """provider_type='shopify' → returns a ShopifyAdapter with shop_url from config_data."""
    from app.services.transactional.adapters.shopify_adapter import ShopifyAdapter
    from app.services.transactional.provider_adapter import get_adapter_for_skill

    with _dispatch_ctx(_shopify_config()):
        adapter = await get_adapter_for_skill("place_order", _TEST_AGENT_ID, _TEST_CONN_STR)

    assert isinstance(adapter, ShopifyAdapter), (
        f"Expected ShopifyAdapter, got {type(adapter).__name__}"
    )
    # shop_url must come from config_data (T-16-02)
    assert adapter._shop_url == "teststore.myshopify.com"


# ---------------------------------------------------------------------------
# test_adapter_dispatch_woocommerce
# ---------------------------------------------------------------------------


async def test_adapter_dispatch_woocommerce() -> None:
    """provider_type='woocommerce' → returns a WooCommerceAdapter with site_url from config_data."""
    from app.services.transactional.adapters.woocommerce_adapter import WooCommerceAdapter
    from app.services.transactional.provider_adapter import get_adapter_for_skill

    with _dispatch_ctx(_woocommerce_config()):
        adapter = await get_adapter_for_skill("issue_refund", _TEST_AGENT_ID, _TEST_CONN_STR)

    assert isinstance(adapter, WooCommerceAdapter), (
        f"Expected WooCommerceAdapter, got {type(adapter).__name__}"
    )
    # site_url must come from config_data (T-16-02)
    assert adapter._site_url == "https://teststore.example.com"


# ---------------------------------------------------------------------------
# test_adapter_dispatch_calendly
# ---------------------------------------------------------------------------


async def test_adapter_dispatch_calendly() -> None:
    """provider_type='calendly' → returns a CalendlyAdapter with config_data event_types."""
    from app.services.transactional.adapters.calendly_adapter import CalendlyAdapter
    from app.services.transactional.provider_adapter import get_adapter_for_skill

    with _dispatch_ctx(_calendly_config()):
        adapter = await get_adapter_for_skill("book_slot", _TEST_AGENT_ID, _TEST_CONN_STR)

    assert isinstance(adapter, CalendlyAdapter), (
        f"Expected CalendlyAdapter, got {type(adapter).__name__}"
    )
    # event_types must be populated from config_data (Open Question 2 resolution)
    assert "consultation" in adapter._event_types


# ---------------------------------------------------------------------------
# test_unconfigured_skill_raises
# ---------------------------------------------------------------------------


async def test_unconfigured_skill_raises() -> None:
    """_fetch_credential_config returning None raises ProviderNotConfiguredError."""
    from app.services.transactional.credential_service import ProviderNotConfiguredError
    from app.services.transactional.provider_adapter import get_adapter_for_skill

    with _dispatch_ctx(None):  # None → no row in integration_credentials
        with pytest.raises(ProviderNotConfiguredError, match="No integration credential"):
            await get_adapter_for_skill("issue_refund", _TEST_AGENT_ID, _TEST_CONN_STR)


# ---------------------------------------------------------------------------
# test_decrypt_failure_raises
# ---------------------------------------------------------------------------


async def test_decrypt_failure_raises() -> None:
    """Credential encrypted under a different tenant key raises CredentialDecryptionError.

    The config row was encrypted with tenant-A's Fernet, but the ContextVar
    holds tenant-B's id → the derived Fernet key is different → InvalidToken.
    """
    from app.services.transactional.credential_service import (
        CredentialDecryptionError,
        _CredentialConfig,
    )
    from app.services.transactional.provider_adapter import get_adapter_for_skill

    # Encrypt with tenant-A
    ciphertext_wrong_tenant = _encrypt(
        {"api_key": "rk_test_stripe_key"}, tenant_id="tenant-A"
    )
    wrong_key_config = _CredentialConfig(
        provider_type="stripe",
        credential_data=ciphertext_wrong_tenant,
        config_data={},
        currency_code="USD",
    )

    # But ContextVar says tenant-B → derived Fernet is different → decrypt fails
    wrong_tenant_id = "tenant-B"  # differs from "tenant-A"
    with _dispatch_ctx(wrong_key_config, tenant_id=wrong_tenant_id):
        with pytest.raises(CredentialDecryptionError, match="Failed to decrypt"):
            await get_adapter_for_skill("issue_refund", _TEST_AGENT_ID, _TEST_CONN_STR)


# ---------------------------------------------------------------------------
# test_unknown_provider_type_raises
# ---------------------------------------------------------------------------


async def test_unknown_provider_type_raises() -> None:
    """An unrecognised provider_type raises ProviderNotConfiguredError."""
    from app.services.transactional.credential_service import (
        ProviderNotConfiguredError,
        _CredentialConfig,
    )
    from app.services.transactional.provider_adapter import get_adapter_for_skill

    unknown_config = _CredentialConfig(
        provider_type="paypal",  # not implemented in Phase 16
        credential_data=_encrypt({"api_key": "some_paypal_key"}),
        config_data={},
        currency_code="USD",
    )
    with _dispatch_ctx(unknown_config):
        with pytest.raises(ProviderNotConfiguredError, match="Unknown provider_type"):
            await get_adapter_for_skill("issue_refund", _TEST_AGENT_ID, _TEST_CONN_STR)
