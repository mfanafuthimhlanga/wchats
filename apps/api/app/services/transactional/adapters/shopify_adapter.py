"""
transactional.adapters.shopify_adapter — Real Shopify provider adapter (INT-03).

Implements ProviderAdapter for Shopify Admin GraphQL API, covering:
  - place_order    → orderCreate mutation (Admin GraphQL)
  - cancel_order   → orderCancel mutation (Admin GraphQL)
  - issue_refund   → refundCreate mutation (Admin GraphQL)

Unsupported methods (update_subscription, book_slot, update_customer_record) raise
NotImplementedError so the dispatcher returns is_error=True without a network call.

Security invariants enforced here:
  T-16-01: The Shopify access_token is NEVER logged. It is extracted from the
            credential JSON blob only inside the sync closure (handle.use() → json.loads
            → ["access_token"]). structlog logs only skill/status/ids.
  T-16-02: shop_url is a constructor param from integration_credentials.config_data.
            It is NEVER read from tool args (SSRF prevention). No URL field exists in
            the typed schemas.
  T-16-dep: REST Admin API is forbidden (deprecated for apps Feb 2025). All mutations
             use the Shopify Admin GraphQL API exclusively.

Session-per-call pattern (Pitfall 2 analog for Shopify):
  Each sync closure activates a fresh shopify.Session inside asyncio.to_thread and
  always clears the session in a finally block. This prevents cross-tenant session
  bleed in a multi-tenant Celery worker.

Pitfall avoidance:
  Pitfall 3: All Shopify SDK calls (sync Python) are wrapped with asyncio.to_thread
             to avoid blocking the Celery worker event loop.
  Pitfall 6: API version is pinned as _API_VERSION = "2025-04". Do not use API
             versions that Shopify has sunset.

GraphQL mutation API reference:
  orderCreate:  shopify.dev/docs/api/admin-graphql/latest/mutations/orderCreate
  orderCancel:  shopify.dev/docs/api/admin-graphql/latest/mutations/orderCancel
  refundCreate: shopify.dev/docs/api/admin-graphql/latest/mutations/refundCreate
"""

from __future__ import annotations

import asyncio
import json

import shopify
import structlog

from app.services.transactional.credential_service import CredentialHandle
from app.services.transactional.provider_adapter import ProviderAdapter
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

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Shopify API version pin (Pitfall 6: explicit version required)
# ---------------------------------------------------------------------------
_API_VERSION = "2025-04"


class ShopifyAdapter(ProviderAdapter):
    """Real Shopify provider adapter behind the typed tool contract (INT-03).

    Receives a CredentialHandle (decrypted in-memory), the per-tenant shop_url
    from integration_credentials.config_data, and the currency_code from
    integration_credentials.currency_code. Never reads credentials or URLs
    from tool args.

    Usage (injected by get_adapter_for_skill in Plan 16-02):
        adapter = ShopifyAdapter(handle=handle, shop_url=config.shop_url, currency_code=config.currency_code)
        result = await adapter.issue_refund(args, agent_id=agent_id)
    """

    def __init__(self, handle: CredentialHandle, shop_url: str, currency_code: str) -> None:
        """Initialise the adapter with a resolved credential handle, shop URL, and currency.

        Args:
            handle: In-memory CredentialHandle wrapping the decrypted Shopify credential
                    JSON blob ({"access_token": "shpat_..."}). Lifetime scoped to tool call.
            shop_url: Shopify myshopify.com domain from integration_credentials.config_data.
                      e.g. "mystore.myshopify.com". MUST come from config, never from args
                      (T-16-02: SSRF prevention).
            currency_code: ISO-4217 currency code from integration_credentials.currency_code.
                           Stored as-is — Shopify Admin GraphQL accepts uppercase (e.g. "USD").
        """
        self._handle = handle
        self._shop_url = shop_url  # from config_data, never from args (T-16-02)
        self._currency_code = currency_code
        self._api_version = _API_VERSION

    # -----------------------------------------------------------------------
    # Session management — Session.temp() per-call pattern (WR-02: thread safety)
    # -----------------------------------------------------------------------
    # WR-02: The old _make_session() / _clear_session() pattern used
    # ShopifyResource.activate_session() which stores the active session in a
    # class-level attribute. Under concurrent asyncio.to_thread calls from
    # different tenant requests in the same Celery worker, Thread A could
    # overwrite Thread B's session, causing cross-tenant credential bleed.
    #
    # shopify.Session.temp() is a thread-safe context manager (ShopifyAPI >= 8.x)
    # that scopes the session to the current thread's execution context.
    # All sync closures use `with shopify.Session.temp(shop_url, version, token):`
    # instead of the activate/clear pair.

    # -----------------------------------------------------------------------
    # issue_refund — Shopify refundCreate mutation
    # -----------------------------------------------------------------------

    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        """Issue a refund via Shopify Admin GraphQL refundCreate mutation.

        Uses an amount-based refund via the RefundInput.transactions field.
        This is the correct path for arbitrary-amount partial refunds — it creates
        a REFUND transaction against the order's payment gateway for the requested
        amount, rather than deriving the amount from line items.

        The session is activated inside the asyncio.to_thread closure and always
        cleared in a finally block (session-per-call pattern).
        Currency is self._currency_code — never from args (INT-07).

        Args:
            args.order_id: Shopify order GID (gid://shopify/Order/...) to refund against.
            args.refund_amount_cents: Amount in cents to refund. Converted to a decimal
                                      string (e.g. 3500 → "35.00") for the transactions
                                      field amount. Non-zero required for a real refund.
            args.reason: Reason for refund, forwarded as the note field.

        Returns:
            IssueRefundOutput(refund_id=<GID>, status="refunded", message=...)
        """
        mutation = """
        mutation refundCreate($input: RefundInput!) {
          refundCreate(input: $input) {
            refund { id }
            userErrors { field message }
          }
        }
        """
        # Convert cents to a decimal currency-major string (e.g. 3500 → "35.00")
        # RefundInput.transactions.amount expects a currency-major string, not cents.
        refund_amount_decimal = f"{args.refund_amount_cents / 100:.2f}"
        variables = {
            "input": {
                "orderId": args.order_id,
                "currency": self._currency_code,  # INT-07: from config, never args
                "note": args.reason,
                # transactions-based refund: amount-based path for arbitrary partial refunds.
                # An empty refundLineItems would create a $0 refund — always use transactions.
                "transactions": [
                    {
                        "orderId": args.order_id,
                        "kind": "REFUND",
                        "gateway": "shopify_payments",
                        "amount": refund_amount_decimal,
                    }
                ],
            }
        }

        def _sync() -> str:
            """Sync Shopify call — runs in thread pool via asyncio.to_thread (Pitfall 3).

            WR-02: Session.temp() is thread-safe — scoped to this closure's execution
            context. The token is extracted inside the closure (T-16-01: never logged).
            """
            # T-16-01: token extracted only here, never stored outside the closure
            token = json.loads(self._handle.use())["access_token"]
            with shopify.Session.temp(self._shop_url, self._api_version, token):
                return shopify.GraphQL().execute(mutation, variables=variables)
            # Session.temp() context manager clears the session on exit (even on exception)

        result_str = await asyncio.to_thread(_sync)
        data = json.loads(result_str)

        user_errors = (
            data.get("data", {}).get("refundCreate", {}).get("userErrors", [])
        )
        if user_errors:
            raise RuntimeError(
                f"Shopify refundCreate returned userErrors: {user_errors}"
            )

        refund_data = data.get("data", {}).get("refundCreate", {}).get("refund") or {}
        refund_id = refund_data.get("id", f"shopify-refund-{args.order_id}")

        log.info(
            "shopify.refund_issued",
            refund_id=refund_id,
            order_id=args.order_id,
            agent_id=agent_id,
            # NEVER log the access_token or CredentialHandle
        )
        return IssueRefundOutput(
            refund_id=refund_id,
            status="refunded",
            message=f"Refund {refund_id} issued for order {args.order_id}.",
        )

    # -----------------------------------------------------------------------
    # place_order — Shopify orderCreate mutation
    # -----------------------------------------------------------------------

    async def place_order(self, args: PlaceOrderInput, agent_id: str) -> PlaceOrderOutput:
        """Create an order via Shopify Admin GraphQL orderCreate mutation.

        Uses the product_id as a variantId line item. The session is activated
        inside the asyncio.to_thread closure and always cleared in a finally block.

        Args:
            args.product_id: Shopify product variant GID or SKU as the line item identifier.
            args.quantity: Number of units.
            args.customer_email: Customer email for order confirmation.
            args.shipping_address: Shipping address (forwarded as address1).

        Returns:
            PlaceOrderOutput(order_id=<GID>, status="placed", message=...)
        """
        mutation = """
        mutation orderCreate($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput) {
          orderCreate(order: $order, options: $options) {
            order { id }
            userErrors { field message }
          }
        }
        """
        variables = {
            "order": {
                "lineItems": [
                    {
                        "variantId": args.product_id,
                        "quantity": args.quantity,
                    }
                ],
                "currency": self._currency_code,  # INT-07: from config, never args
                "email": args.customer_email,
                "shippingAddress": {
                    "address1": args.shipping_address,
                },
            }
        }

        def _sync() -> str:
            """Sync Shopify call — runs in thread pool via asyncio.to_thread (Pitfall 3).

            WR-02: Session.temp() scopes the session to this closure's thread context.
            """
            token = json.loads(self._handle.use())["access_token"]
            with shopify.Session.temp(self._shop_url, self._api_version, token):
                return shopify.GraphQL().execute(mutation, variables=variables)

        result_str = await asyncio.to_thread(_sync)
        data = json.loads(result_str)

        user_errors = (
            data.get("data", {}).get("orderCreate", {}).get("userErrors", [])
        )
        if user_errors:
            raise RuntimeError(
                f"Shopify orderCreate returned userErrors: {user_errors}"
            )

        order_data = data.get("data", {}).get("orderCreate", {}).get("order") or {}
        order_id = order_data.get("id", f"shopify-order-{args.product_id}")

        log.info(
            "shopify.order_placed",
            order_id=order_id,
            product_id=args.product_id,
            quantity=args.quantity,
            agent_id=agent_id,
        )
        return PlaceOrderOutput(
            order_id=order_id,
            status="placed",
            message=(
                f"Order {order_id} placed for {args.quantity}x {args.product_id}."
            ),
        )

    # -----------------------------------------------------------------------
    # cancel_order — Shopify orderCancel mutation
    # -----------------------------------------------------------------------

    async def cancel_order(
        self, args: CancelOrderInput, agent_id: str
    ) -> CancelOrderOutput:
        """Cancel an order via Shopify Admin GraphQL orderCancel mutation.

        Forwards args.reason as the staffNote field. Shopify's orderCancel mutation
        requires a reason enum (CUSTOMER, DECLINED, FRAUD, INVENTORY, OTHER, STAFF);
        we default to OTHER for agent-initiated cancellations.

        Args:
            args.order_id: Shopify order GID (gid://shopify/Order/...) to cancel.
            args.reason: Human-readable reason, forwarded as staffNote.

        Returns:
            CancelOrderOutput(order_id=..., status="cancelled", message=...)
        """
        mutation = """
        mutation orderCancel($orderId: ID!, $reason: OrderCancelReason!, $staffNote: String) {
          orderCancel(orderId: $orderId, reason: $reason, staffNote: $staffNote) {
            job { id }
            orderCancelUserErrors { field message }
          }
        }
        """
        variables = {
            "orderId": args.order_id,
            "reason": "OTHER",  # agent-initiated; reason detail is in staffNote
            "staffNote": args.reason,
        }

        def _sync() -> str:
            """Sync Shopify call — runs in thread pool via asyncio.to_thread (Pitfall 3).

            WR-02: Session.temp() scopes the session to this closure's thread context.
            """
            token = json.loads(self._handle.use())["access_token"]
            with shopify.Session.temp(self._shop_url, self._api_version, token):
                return shopify.GraphQL().execute(mutation, variables=variables)

        result_str = await asyncio.to_thread(_sync)
        data = json.loads(result_str)

        errors = (
            data.get("data", {}).get("orderCancel", {}).get("orderCancelUserErrors", [])
        )
        if errors:
            raise RuntimeError(
                f"Shopify orderCancel returned errors: {errors}"
            )

        log.info(
            "shopify.order_cancelled",
            order_id=args.order_id,
            agent_id=agent_id,
        )
        return CancelOrderOutput(
            order_id=args.order_id,
            status="cancelled",
            message=f"Order {args.order_id} cancellation submitted.",
        )

    # -----------------------------------------------------------------------
    # Unsupported methods — raise NotImplementedError (fast-fail)
    # -----------------------------------------------------------------------

    async def update_subscription(
        self, args: UpdateSubscriptionInput, agent_id: str
    ) -> UpdateSubscriptionOutput:
        """Not supported by ShopifyAdapter.

        Shopify does not have a subscription management API in the Provider→Tool
        mapping (Phase 16 Provider→Tool table). The dispatcher's except Exception
        handler returns is_error=True.
        """
        raise NotImplementedError(
            "update_subscription not supported by ShopifyAdapter — "
            "use StripeAdapter for subscription management."
        )

    async def book_slot(self, args: BookSlotInput, agent_id: str) -> BookSlotOutput:
        """Not supported by ShopifyAdapter.

        Scheduling/slot booking is a CalendlyAdapter concern.
        """
        raise NotImplementedError(
            "book_slot not supported by ShopifyAdapter — use CalendlyAdapter."
        )

    async def update_customer_record(
        self, args: UpdateCustomerRecordInput, agent_id: str
    ) -> UpdateCustomerRecordOutput:
        """Not supported by ShopifyAdapter (Phase 16 deferred per Provider→Tool Mapping).

        update_customer_record is deferred across all providers in Phase 16.
        The StubProviderAdapter pattern is maintained here.
        """
        raise NotImplementedError(
            "update_customer_record not supported by ShopifyAdapter."
        )
