"""
transactional.provider_adapter — ProviderAdapter ABC + StubProviderAdapter + get_adapter().

ProviderAdapter defines the per-method typed interface for all 6 mutating tools.
Phase 16 will subclass ProviderAdapter to implement real provider calls
(Shopify, Stripe, Calendly, etc.) without touching the tool handlers.

StubProviderAdapter is the Phase-14 offline implementation:
  - Returns [STUB]-labelled Output objects for every method.
  - Generates stub identifiers using uuid4().
  - No network calls, no real side effects (T-14-02-03).

get_adapter(agent_id) returns the module-level stub singleton in Phase 14.
Phase 16 replaces this with per-agent provider dispatch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import uuid4

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
    Phase 16: look up per-agent provider configuration and return the
              appropriate concrete adapter (ShopifyAdapter, StripeAdapter, etc.).
    """
    return _STUB_ADAPTER
