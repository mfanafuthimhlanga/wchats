"""
transactional.adapters.stripe_adapter — Real Stripe provider adapter (INT-05).

Implements ProviderAdapter for Stripe, covering:
  - issue_refund   → stripe.v1.refunds.create (Refunds API)
  - update_subscription → stripe.v1.subscriptions.update (Subscriptions API)
  - place_order    → stripe.v1.checkout.sessions.create (Checkout Sessions, mode=payment)

Unsupported methods (cancel_order, book_slot, update_customer_record) raise
NotImplementedError so the dispatcher returns is_error=True without a network call.

Security invariants enforced here:
  T-16-01: The module-level Stripe API key attribute is NEVER set.
            Each method constructs a fresh stripe.StripeClient inside its asyncio.to_thread
            closure, keeping the key local to the sync call. The CredentialHandle.__repr__
            is already redacted — structlog cannot accidentally log the raw key.
  T-16-02: No user-controlled URL fields. success_url/cancel_url are static placeholders.
            The stripe SDK targets fixed Stripe endpoints only.
  T-16-08: args.idempotency_key is forwarded to Stripe's native Idempotency-Key via the
            options dict (second positional arg). Stripe returns the original response on
            replay, adding defense-in-depth atop the W Chats idempotency engine (TXN-02).
  T-16-cur: currency is ALWAYS self._currency_code from integration_credentials.currency_code,
            NEVER read from tool args (INT-07 — single currency per tenant, no override path).

Pitfall avoidance:
  Pitfall 2: StripeClient is constructed INSIDE the asyncio.to_thread closure so the
             api_key is localized to one sync call. Setting the Stripe module-level
             api_key attribute would cause cross-tenant key bleed in a multi-tenant
             Celery worker — never do this.
  Pitfall 3: All Stripe SDK calls (sync Python) are wrapped with asyncio.to_thread
             to avoid blocking the Celery worker event loop.
"""

from __future__ import annotations

import asyncio
import json

import stripe
import structlog

from app.domain.transactional_schemas import (
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
from app.services.transactional.credential_service import CredentialHandle
from app.services.transactional.provider_adapter import ProviderAdapter

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Static placeholder URLs for Checkout Session (T-16-02: no user-controlled URLs)
# ---------------------------------------------------------------------------
_CHECKOUT_SUCCESS_URL = "https://example.com/checkout/success"
_CHECKOUT_CANCEL_URL = "https://example.com/checkout/cancel"


class StripeAdapter(ProviderAdapter):
    """Real Stripe provider adapter behind the typed tool contract (INT-05).

    Receives a CredentialHandle (decrypted in-memory) and the per-tenant currency_code
    from integration_credentials. Never reads from tool args for credentials or currency.

    Usage (injected by get_adapter_for_skill in Plan 16-02):
        adapter = StripeAdapter(handle=handle, currency_code=config.currency_code)
        result = await adapter.issue_refund(args, agent_id=agent_id)
    """

    def __init__(self, handle: CredentialHandle, currency_code: str) -> None:
        """Initialise the adapter with a resolved credential handle and tenant currency.

        Args:
            handle: In-memory CredentialHandle wrapping the decrypted Stripe credential
                    JSON blob ({"api_key": "rk_live_..."}). Lifetime scoped to tool call.
            currency_code: ISO-4217 currency code from integration_credentials.currency_code.
                           Stored lowercased — Stripe's API requires lowercase (e.g. "usd").
        """
        self._handle = handle
        self._currency_code = currency_code.lower()  # Stripe expects lowercase ISO-4217

    # -----------------------------------------------------------------------
    # issue_refund — Stripe Refunds API
    # -----------------------------------------------------------------------

    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        """Issue a refund via Stripe Refunds API with native idempotency (TXN-02).

        The StripeClient is constructed INSIDE the sync closure (Pitfall 2 avoided).
        Currency is self._currency_code — never from args (INT-07).
        Idempotency-Key is forwarded as the options dict second arg (T-16-08).

        Args:
            args.order_id: Stripe charge ID (ch_...) to refund against.
            args.refund_amount_cents: Amount in cents to refund.
            args.idempotency_key: TXN-02 key forwarded to Stripe's native Idempotency-Key.

        Returns:
            IssueRefundOutput(refund_id=..., status="refunded", message=...)
        """

        def _sync() -> stripe.Refund:
            # Construct StripeClient INSIDE the closure — Pitfall 2 prevention.
            # json.loads extracts api_key from the credential JSON blob.
            client = stripe.StripeClient(json.loads(self._handle.use())["api_key"])
            return client.v1.refunds.create(
                {
                    "charge": args.order_id,
                    "amount": args.refund_amount_cents,
                    "reason": "requested_by_customer",
                    # WR-01: currency is NOT passed for charge-based refunds.
                    # Stripe derives the refund currency from the original charge;
                    # passing currency returns 400 "unknown parameter: currency".
                    # INT-07 is still enforced — currency is set per-tenant in
                    # integration_credentials.currency_code but not forwarded here.
                },
                {"idempotency_key": args.idempotency_key},  # TXN-02 → Stripe Idempotency-Key
            )

        refund = await asyncio.to_thread(_sync)

        log.info(
            "stripe.refund_issued",
            refund_id=refund.id,
            status="refunded",
            agent_id=agent_id,
            # NEVER log the api_key or CredentialHandle
        )
        return IssueRefundOutput(
            refund_id=refund.id,
            status="refunded",
            message=f"Refund {refund.id} issued for {args.refund_amount_cents} cents.",
        )

    # -----------------------------------------------------------------------
    # update_subscription — Stripe Subscriptions API
    # -----------------------------------------------------------------------

    async def update_subscription(
        self, args: UpdateSubscriptionInput, agent_id: str
    ) -> UpdateSubscriptionOutput:
        """Update a Stripe subscription to a new plan (price ID).

        Retrieves the current subscription first to obtain the existing item's id,
        then updates using {"id": <existing_item_id>, "price": new_plan} so the plan
        is REPLACED instead of a duplicate item being added on top of the old one.

        Per Stripe's documented behavior: items without an id are created; existing
        items are only modified when their id is provided. Without the existing item id,
        update_subscription would add a second item and bill the customer for both plans.

        Args:
            args.subscription_id: Stripe subscription ID (sub_...) to modify.
            args.new_plan: Target price ID (price_...) for the subscription item.
            args.idempotency_key: TXN-02 key forwarded to Stripe's Idempotency-Key.

        Returns:
            UpdateSubscriptionOutput(subscription_id=updated_sub.id, status="updated", ...)
            subscription_id comes from the server response to confirm the update was applied.

        Raises:
            ValueError: If the subscription has no items to update.
        """

        def _sync() -> stripe.Subscription:
            client = stripe.StripeClient(json.loads(self._handle.use())["api_key"])
            # Retrieve first to get the existing item id.
            # Without the id, Stripe adds a NEW item on top of the existing one,
            # double-billing the customer (CR-02).
            sub = client.v1.subscriptions.retrieve(args.subscription_id)
            existing_item_id = sub.items.data[0].id if sub.items.data else None
            if existing_item_id is None:
                raise ValueError(
                    f"Subscription {args.subscription_id} has no items to update."
                )
            return client.v1.subscriptions.update(
                args.subscription_id,
                {"items": [{"id": existing_item_id, "price": args.new_plan}]},
                {"idempotency_key": args.idempotency_key},
            )

        updated_sub = await asyncio.to_thread(_sync)

        log.info(
            "stripe.subscription_updated",
            subscription_id=updated_sub.id,
            new_plan=args.new_plan,
            status="updated",
            agent_id=agent_id,
        )
        return UpdateSubscriptionOutput(
            subscription_id=updated_sub.id,  # server-confirmed ID (not just the input arg)
            status="updated",
            message=(
                f"Subscription {updated_sub.id} updated to plan {args.new_plan!r} "
                f"effective {args.effective_date}."
            ),
        )

    # -----------------------------------------------------------------------
    # place_order — Stripe Checkout Sessions API (no raw card handling)
    # -----------------------------------------------------------------------

    async def place_order(self, args: PlaceOrderInput, agent_id: str) -> PlaceOrderOutput:
        """Create a Stripe Checkout Session in payment mode (Stripe-hosted, no PCI surface).

        Stripe Checkout handles all card data collection on Stripe's servers — no card
        number, CVC, or expiry field is ever present in our tool schemas or API calls
        (T-16-02 PCI boundary preserved).

        WR-07: The total amount is passed as a single line item with quantity=1
        to avoid integer-division rounding. The old approach of args.amount_cents //
        quantity could silently undercharge (e.g. 100 cents / 3 items = 33 cents per
        item × 3 = 99 cents billed instead of 100), causing reconciliation failures.
        The product name includes the quantity so the customer sees the correct bundle.

        Args:
            args.product_id: Product SKU used as the product_data name.
            args.quantity: Number of units; included in the product name for customer clarity.
            args.amount_cents: Total amount in cents. Passed as unit_amount with quantity=1
                               to avoid integer-division remainder loss (WR-07).
            args.idempotency_key: TXN-02 key forwarded to Stripe's Idempotency-Key.

        Returns:
            PlaceOrderOutput(order_id=<session.id>, status="pending_confirmation", message=...)
        """
        quantity = max(args.quantity, 1)

        def _sync() -> stripe.checkout.Session:
            client = stripe.StripeClient(json.loads(self._handle.use())["api_key"])
            return client.v1.checkout.sessions.create(
                {
                    "mode": "payment",  # one-time payment; no card fields (PCI)
                    "line_items": [
                        {
                            "price_data": {
                                "currency": self._currency_code,  # INT-07: from config
                                # WR-07: single line item with full total as unit_amount,
                                # quantity=1. This ensures billed total == args.amount_cents
                                # with no integer-division remainder silently dropped.
                                "product_data": {"name": f"{quantity}x {args.product_id}"},
                                "unit_amount": args.amount_cents,
                            },
                            "quantity": 1,
                        }
                    ],
                    "success_url": _CHECKOUT_SUCCESS_URL,  # T-16-02: static, not from args
                    "cancel_url": _CHECKOUT_CANCEL_URL,
                },
                {"idempotency_key": args.idempotency_key},
            )

        session = await asyncio.to_thread(_sync)

        session_url = getattr(session, "url", "N/A")
        log.info(
            "stripe.checkout_session_created",
            order_id=session.id,
            status="pending_confirmation",
            agent_id=agent_id,
        )
        return PlaceOrderOutput(
            order_id=session.id,
            status="pending_confirmation",
            message=f"Checkout session created: {session_url}",
        )

    # -----------------------------------------------------------------------
    # Unsupported methods — raise NotImplementedError (fast-fail)
    # -----------------------------------------------------------------------

    async def cancel_order(
        self, args: CancelOrderInput, agent_id: str
    ) -> CancelOrderOutput:
        """Not supported by StripeAdapter.

        Stripe does not have a direct order cancellation API behind our tool contract.
        The dispatcher's except Exception handler returns is_error=True.
        """
        raise NotImplementedError(
            "cancel_order not supported by StripeAdapter — "
            "use the Stripe Dashboard or a Shopify/WooCommerce adapter for order cancellation."
        )

    async def book_slot(self, args: BookSlotInput, agent_id: str) -> BookSlotOutput:
        """Not supported by StripeAdapter.

        Scheduling/slot booking is a Calendly adapter concern.
        """
        raise NotImplementedError(
            "book_slot not supported by StripeAdapter — use CalendlyAdapter."
        )

    async def update_customer_record(
        self, args: UpdateCustomerRecordInput, agent_id: str
    ) -> UpdateCustomerRecordOutput:
        """Not supported by StripeAdapter (Phase 16 deferred per Provider→Tool Mapping).

        update_customer_record is deferred across all providers in Phase 16.
        The StubProviderAdapter pattern is maintained here.
        """
        raise NotImplementedError(
            "update_customer_record not supported by StripeAdapter."
        )
