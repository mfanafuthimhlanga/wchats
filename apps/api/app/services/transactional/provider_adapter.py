"""
transactional.provider_adapter — ProviderAdapter ABC + StubProviderAdapter + get_adapter_for_skill().

ProviderAdapter defines the per-method typed interface for all 6 mutating tools.
Four concrete adapters (StripeAdapter, ShopifyAdapter, WooCommerceAdapter, CalendlyAdapter)
are dispatched via get_adapter_for_skill() — the credential-resolution entry point (INT-02).

StubProviderAdapter is the Phase-14 offline implementation:
  - Returns [STUB]-labelled Output objects for every method.
  - Generates stub identifiers using uuid4().
  - No network calls, no real side effects (T-14-02-03).

get_adapter_for_skill(skill, agent_id, conn_str) — Phase 16 entry point:
  Fetches + decrypts the tenant credential from the tenant DB, derives a per-tenant
  Fernet via HKDF(PLATFORM_CREDENTIAL_KEY, salt=tenant_id), returns the correct
  concrete adapter. The raw credential never leaves this function — only a CredentialHandle
  (redacted __repr__) is passed to the adapter constructor.

  Called ONLY from _execute_transactional_tool (tools.py step 6).
  MUST NOT be imported or called from any FastAPI route handler or SDK hook.

Circular import note:
  The four concrete adapters import ProviderAdapter from this module. To avoid a
  circular import at module load time, adapter classes are imported LAZILY inside the
  get_adapter_for_skill function body — not at the top of this file.

  Credential service symbols (_fetch_credential_config, _derive_tenant_fernet,
  CredentialHandle, etc.) are safe to import at module level because credential_service.py
  does NOT import provider_adapter.py.

  _tenant_id_var is imported LAZILY (also inside get_adapter_for_skill) to avoid the
  agent_tools → tools → provider_adapter circular import chain.
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from uuid import uuid4

import structlog

from app.core.config import settings
from app.services.transactional.credential_service import (
    CredentialDecryptionError,
    CredentialHandle,
    ProviderNotConfiguredError,
    _derive_tenant_fernet,
    _fetch_credential_config,
)
from app.services.transactional.schemas import (
    BookSlotInput,
    BookSlotOutput,
    CancelOrderInput,
    CancelOrderOutput,
    IssueRefundInput,
    IssueRefundOutput,
    PlaceOrderInput,
    PlaceOrderOutput,
    UpdateCustomerRecordInput,
    UpdateCustomerRecordOutput,
    UpdateSubscriptionInput,
    UpdateSubscriptionOutput,
)


class ProviderAdapter(ABC):
    """Abstract base class for transactional tool providers.

    Each concrete subclass implements the 6 mutating methods for a specific
    e-commerce / scheduling / CRM provider.  The method signatures are the
    canonical typed contract; no free-form dicts, SQL strings, or URLs.
    """

    @abstractmethod
    async def place_order(self, args: PlaceOrderInput, agent_id: str) -> PlaceOrderOutput:
        """Place a customer order through the tenant's connected store."""
        ...

    @abstractmethod
    async def cancel_order(self, args: CancelOrderInput, agent_id: str) -> CancelOrderOutput:
        """Cancel an existing order."""
        ...

    @abstractmethod
    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        """Issue a refund for an order."""
        ...

    @abstractmethod
    async def update_subscription(
        self, args: UpdateSubscriptionInput, agent_id: str
    ) -> UpdateSubscriptionOutput:
        """Update a customer's subscription plan."""
        ...

    @abstractmethod
    async def book_slot(self, args: BookSlotInput, agent_id: str) -> BookSlotOutput:
        """Book a time slot (consultation, delivery, installation, etc.)."""
        ...

    @abstractmethod
    async def update_customer_record(
        self, args: UpdateCustomerRecordInput, agent_id: str
    ) -> UpdateCustomerRecordOutput:
        """Update a field on a customer record."""
        ...


class StubProviderAdapter(ProviderAdapter):
    """Phase-14 offline stub adapter.

    Returns [STUB]-labelled Output objects for every method.
    No network calls, no real side effects.

    Phase 16 replaces this with real provider adapters (ShopifyAdapter,
    StripeAdapter, etc.) by subclassing ProviderAdapter and injecting via
    get_adapter(agent_id).
    """

    async def place_order(self, args: PlaceOrderInput, agent_id: str) -> PlaceOrderOutput:
        return PlaceOrderOutput(
            order_id=f"stub-{uuid4()}",
            status="pending_confirmation",
            message=(
                f"[STUB] Order received for {args.quantity}x {args.product_id} "
                f"(amount_cents={args.amount_cents}) — no real action taken in Phase 14."
            ),
        )

    async def cancel_order(self, args: CancelOrderInput, agent_id: str) -> CancelOrderOutput:
        return CancelOrderOutput(
            order_id=args.order_id,
            status="pending_cancellation",
            message=(
                f"[STUB] Cancellation request received for order {args.order_id} "
                f"(reason={args.reason!r}) — no real action taken in Phase 14."
            ),
        )

    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        return IssueRefundOutput(
            refund_id=f"stub-{uuid4()}",
            status="pending_refund",
            message=(
                f"[STUB] Refund of {args.refund_amount_cents} cents requested for order "
                f"{args.order_id} — no real action taken in Phase 14."
            ),
        )

    async def update_subscription(
        self, args: UpdateSubscriptionInput, agent_id: str
    ) -> UpdateSubscriptionOutput:
        return UpdateSubscriptionOutput(
            subscription_id=args.subscription_id,
            status="pending_update",
            message=(
                f"[STUB] Subscription {args.subscription_id} plan change to {args.new_plan!r} "
                f"effective {args.effective_date} — no real action taken in Phase 14."
            ),
        )

    async def book_slot(self, args: BookSlotInput, agent_id: str) -> BookSlotOutput:
        return BookSlotOutput(
            booking_id=f"stub-{uuid4()}",
            status="pending_confirmation",
            message=(
                f"[STUB] Booking request for {args.service_type} on {args.preferred_date} "
                f"at {args.preferred_time} for {args.customer_name} — no real action taken in Phase 14."
            ),
        )

    async def update_customer_record(
        self, args: UpdateCustomerRecordInput, agent_id: str
    ) -> UpdateCustomerRecordOutput:
        return UpdateCustomerRecordOutput(
            record_id=f"stub-{uuid4()}",
            status="pending_update",
            message=(
                f"[STUB] Customer record field {args.field_name!r} update requested "
                f"— no real action taken in Phase 14."
            ),
        )


# ---------------------------------------------------------------------------
# Module-level singleton + factory
# ---------------------------------------------------------------------------

_STUB_ADAPTER: StubProviderAdapter = StubProviderAdapter()


def get_adapter(agent_id: str | None = None) -> ProviderAdapter:
    """Return the ProviderAdapter for the given agent.

    Phase 14: always returns the stub singleton.
    Retained for backward compatibility with existing unit tests.
    Phase 16 live path: use get_adapter_for_skill() instead.
    """
    return _STUB_ADAPTER


log = structlog.get_logger(__name__)


async def get_adapter_for_skill(
    skill: str,
    agent_id: str,
    conn_str: str,
) -> ProviderAdapter:
    """Resolve credentials and return the correct ProviderAdapter for a skill (INT-02).

    Called at step 6 of _execute_transactional_tool. The conn_str is already
    available via _conn_str_var (set by build_tool_server, never a task arg).

    Security invariants:
      T-16-01: The raw credential string is wrapped in a CredentialHandle whose
               __repr__ is redacted. It is never logged, never returned, and goes
               out of scope when the adapter is garbage-collected.
      T-16-02: shop_url / site_url come from config_data inside this function,
               never from tool-arg schemas.
      T-16-06: The raw credential never appears in Celery task args, agent context,
               audit rows, or log events.

    MUST NOT be imported or called from any FastAPI route handler or SDK hook —
    only from _execute_transactional_tool (tools.py step 6).

    Args:
        skill:     Canonical skill name (e.g. "issue_refund"). Used to look up
                   the integration_credentials row with enabled_skills @> [skill].
        agent_id:  Agent UUID string (for future per-agent dispatch; currently unused
                   in the credential look-up but preserved for forward compatibility).
        conn_str:  Decrypted tenant DB connection string (from _conn_str_var ContextVar).

    Returns:
        A concrete ProviderAdapter subclass holding an in-memory CredentialHandle.

    Raises:
        ProviderNotConfiguredError: No integration_credentials row found for the skill,
                                    or the provider_type is not recognised.
        CredentialDecryptionError:  Fernet decryption failed (wrong key or tampered data).
    """
    # 1. Fetch encrypted credential + provider config from tenant DB.
    #    Returns None if conn_str is empty or no row matches.
    config = await _fetch_credential_config(conn_str, skill)
    if config is None:
        raise ProviderNotConfiguredError(
            f"No integration credential configured for skill '{skill}'"
        )

    # 2. Derive per-tenant Fernet key (in-memory only, discarded after decrypt).
    #    _tenant_id_var is imported LAZILY to avoid the agent_tools → tools →
    #    provider_adapter circular import chain (same pattern as tools.py line 146).
    from app.services.agent_tools import _tenant_id_var  # noqa: PLC0415

    tenant_id = _tenant_id_var.get()
    master_key_bytes = base64.urlsafe_b64decode(settings.PLATFORM_CREDENTIAL_KEY)
    fernet = _derive_tenant_fernet(master_key_bytes, tenant_id)

    # 3. Decrypt → CredentialHandle.
    #    raw_cred exists only in this stack frame and is never logged (T-16-01).
    try:
        raw_cred = fernet.decrypt(config.credential_data).decode("utf-8")
    except Exception:  # noqa: BLE001
        raise CredentialDecryptionError(
            f"Failed to decrypt credential for provider '{config.provider_type}'"
        )
    handle = CredentialHandle(_raw=raw_cred)

    # 4. Dispatch to concrete adapter by provider_type.
    #    Adapters are imported LAZILY to avoid the circular import:
    #    stripe_adapter.py → provider_adapter.py (for ProviderAdapter ABC).
    if config.provider_type == "stripe":
        from app.services.transactional.adapters.stripe_adapter import StripeAdapter  # noqa: PLC0415

        return StripeAdapter(handle=handle, currency_code=config.currency_code)

    elif config.provider_type == "shopify":
        from app.services.transactional.adapters.shopify_adapter import ShopifyAdapter  # noqa: PLC0415

        return ShopifyAdapter(
            handle=handle,
            shop_url=config.config_data["shop_url"],  # T-16-02: from config, never from args
            currency_code=config.currency_code,
        )

    elif config.provider_type == "woocommerce":
        from app.services.transactional.adapters.woocommerce_adapter import WooCommerceAdapter  # noqa: PLC0415

        return WooCommerceAdapter(
            handle=handle,
            site_url=config.config_data["site_url"],  # T-16-02: from config, never from args
            currency_code=config.currency_code,
        )

    elif config.provider_type == "calendly":
        from app.services.transactional.adapters.calendly_adapter import CalendlyAdapter  # noqa: PLC0415

        return CalendlyAdapter(handle=handle, config_data=config.config_data)

    else:
        raise ProviderNotConfiguredError(
            f"Unknown provider_type '{config.provider_type}'"
        )
