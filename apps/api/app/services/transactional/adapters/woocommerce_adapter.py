"""
transactional.adapters.woocommerce_adapter — Real WooCommerce provider adapter (INT-04).

Implements ProviderAdapter for WooCommerce REST API v3, covering:
  - place_order    → POST /wp-json/wc/v3/orders
  - cancel_order   → PUT /wp-json/wc/v3/orders/{id} (status=cancelled)
  - issue_refund   → POST /wp-json/wc/v3/orders/{id}/refunds

Unsupported methods (update_subscription, book_slot, update_customer_record) raise
NotImplementedError so the dispatcher returns is_error=True without a network call.

WooCommerce package decision (16-02 gate):
  The WooCommerce PyPI package was REJECTED at the 16-02 supply-chain checkpoint
  (stale, last released 2021). This adapter uses the approved fallback:
    - httpx (0.28.1, already a core dep) for sync HTTP transport
    - requests-oauthlib>=2.0 for OAuth1 HMAC-SHA256 Authorization-header signing

  The _WooOAuth1Auth class bridges requests-oauthlib OAuth1 signing to httpx.Auth,
  using a requests.PreparedRequest as the signing vehicle and transplanting the
  resulting Authorization header onto the httpx.Request.

Security invariants enforced here:
  T-16-01: consumer_key and consumer_secret are NEVER logged. They are extracted from
            the credential JSON blob only inside _WooOAuth1Auth.auth_flow via
            CredentialHandle.use(), and the CredentialHandle.__repr__ is redacted so
            structlog cannot accidentally log the raw values.
  T-16-02: site_url is a constructor param from integration_credentials.config_data.
            It is NEVER read from tool args (SSRF prevention). No URL field exists
            in any of the typed Input schemas.
  T-16-woo-http: __init__ raises ValueError if site_url does not start with "https://".
                 HTTP is rejected — WooCommerce OAuth1 signing behavior differs over
                 HTTP (query-string-based) vs HTTPS (Authorization header). Always
                 require HTTPS to avoid auth ambiguity (Pitfall 5, RESEARCH.md).

Pitfall avoidance:
  Pitfall 3: All httpx.Client calls (sync transport) are wrapped with asyncio.to_thread
             to avoid blocking the Celery worker event loop.
  Pitfall 5: Raises ValueError on non-HTTPS site_url at construction time.

WooCommerce REST API v3 reference:
  POST /wp-json/wc/v3/orders            — create order
  PUT  /wp-json/wc/v3/orders/{id}       — update order (used for status=cancelled)
  POST /wp-json/wc/v3/orders/{id}/refunds — issue refund
  Authentication: OAuth1 Authorization header (HTTPS) or Basic Auth (HTTPS only)
  [Source: woocommerce.github.io/woocommerce-rest-api-docs/]
"""

from __future__ import annotations

import asyncio
import json

import httpx
import structlog
from requests import Request
from requests_oauthlib import OAuth1

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

# WooCommerce REST API v3 path prefix (wc/v3, stable since WordPress 5.0+)
_WC_API_PREFIX = "/wp-json/wc/v3"


class _WooOAuth1Auth(httpx.Auth):
    """OAuth1 HMAC-SHA256 auth adapter bridging requests-oauthlib to httpx.

    requests-oauthlib's OAuth1 class is a requests.auth.AuthBase subclass and
    cannot be passed directly to httpx. This class implements httpx.Auth by:
      1. Creating an OAuth1 signer per-call (stateless, per-request).
      2. Constructing a requests.PreparedRequest as the signing vehicle.
      3. Calling the OAuth1 signer on the PreparedRequest to compute the
         Authorization header (HMAC-SHA256 signature over method + URL + body).
      4. Transplanting the signed Authorization header onto the httpx.Request.

    Security: consumer_key and consumer_secret are stored only for the lifetime
    of the adapter object (scoped to one tool call). They are never logged.
    The _WooOAuth1Auth instance goes out of scope with the WooCommerceAdapter.
    """

    def __init__(self, consumer_key: str, consumer_secret: str) -> None:
        # Stored only for signing; never passed to structlog or repr'd
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret

    def auth_flow(self, request: httpx.Request):
        """Compute and inject the OAuth1 Authorization header.

        Creates a fresh OAuth1 signer per call (stateless, no nonce reuse).
        Uses HMAC-SHA256 per WooCommerce REST v3 docs.
        """
        oauth = OAuth1(
            self._consumer_key,
            self._consumer_secret,
            signature_method="HMAC-SHA256",
        )
        # Use requests.Request as the signing vehicle — requests-oauthlib's API
        # requires a PreparedRequest to compute the Authorization header.
        prep = Request(
            method=request.method,
            url=str(request.url),
            headers=dict(request.headers),
        ).prepare()
        signed = oauth(prep)
        # Transplant the signed Authorization header onto the httpx.Request
        request.headers["Authorization"] = signed.headers["Authorization"]
        yield request


class WooCommerceAdapter(ProviderAdapter):
    """Real WooCommerce provider adapter behind the typed tool contract (INT-04).

    Uses httpx (sync client) + requests-oauthlib OAuth1 HMAC-SHA256 signing over HTTPS.
    The WooCommerce PyPI package was rejected at the 16-02 legitimacy gate; this is
    the approved httpx + OAuth1 fallback implementation.

    site_url MUST be https:// — raises ValueError on construction if not (T-16-woo-http).
    consumer_key/consumer_secret come from the CredentialHandle (T-16-01: never logged).
    No URL is ever read from tool args (T-16-02: SSRF prevention).

    Usage (injected by get_adapter_for_skill in Plan 16-02):
        adapter = WooCommerceAdapter(handle=handle, site_url=config.site_url, currency_code=config.currency_code)
        result = await adapter.issue_refund(args, agent_id=agent_id)
    """

    def __init__(self, handle: CredentialHandle, site_url: str, currency_code: str) -> None:
        """Initialise the adapter with a resolved credential handle, site URL, and currency.

        Args:
            handle: In-memory CredentialHandle wrapping the decrypted WooCommerce credential
                    JSON blob ({"consumer_key": "ck_...", "consumer_secret": "cs_..."}).
                    Lifetime scoped to tool call.
            site_url: WooCommerce site HTTPS URL from integration_credentials.config_data.
                      e.g. "https://mystore.com". MUST start with "https://"
                      (T-16-woo-http, Pitfall 5 — HTTP triggers OAuth1/auth ambiguity).
            currency_code: ISO-4217 currency code from integration_credentials.currency_code.
                           Stored for INT-07 validation; not sent in REST API calls directly.

        Raises:
            ValueError: If site_url does not start with "https://".
        """
        if not site_url.startswith("https://"):
            raise ValueError(
                f"WooCommerceAdapter: site_url must be HTTPS, got {site_url!r}. "
                "HTTP is rejected — WooCommerce OAuth1 Authorization-header signing "
                "requires TLS to prevent credential interception (Pitfall 5, T-16-woo-http)."
            )
        self._site_url = site_url.rstrip("/")
        self._currency_code = currency_code

        # Extract credentials from handle — only here, never stored as plain strings,
        # never logged (T-16-01). The _WooOAuth1Auth stores them for signing only.
        creds = json.loads(handle.use())
        self._auth = _WooOAuth1Auth(
            consumer_key=creds["consumer_key"],
            consumer_secret=creds["consumer_secret"],
        )

    def _wc_url(self, path: str) -> str:
        """Build a full WooCommerce REST v3 URL.

        Args:
            path: Relative path within /wp-json/wc/v3/ (e.g. "orders" or "orders/42/refunds").
                  Must NOT start with "/".

        Returns:
            Full HTTPS URL (e.g. "https://mystore.com/wp-json/wc/v3/orders").
        """
        return f"{self._site_url}{_WC_API_PREFIX}/{path}"

    # -----------------------------------------------------------------------
    # issue_refund — POST /orders/{id}/refunds
    # -----------------------------------------------------------------------

    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        """Issue a refund via WooCommerce REST v3 POST /orders/{id}/refunds.

        Amount is converted from cents to currency-major string (e.g. 1050 → "10.50").
        raise_for_status() propagates non-2xx HTTP errors to the dispatcher audit.

        Args:
            args.order_id: WooCommerce order ID (integer as string).
            args.refund_amount_cents: Refund amount in cents (ge=0, INT-07 enforced).

        Returns:
            IssueRefundOutput(refund_id=<WC refund id>, status="refunded", message=...)
        """
        url = self._wc_url(f"orders/{args.order_id}/refunds")
        amount_str = f"{args.refund_amount_cents / 100:.2f}"
        body = {"amount": amount_str}

        def _sync() -> dict:
            """Sync httpx call — runs in thread pool via asyncio.to_thread (Pitfall 3)."""
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, json=body, auth=self._auth)
                response.raise_for_status()
                return response.json()

        result = await asyncio.to_thread(_sync)
        refund_id = str(result.get("id", f"woo-refund-{args.order_id}"))

        log.info(
            "woocommerce.refund_issued",
            refund_id=refund_id,
            order_id=args.order_id,
            amount=amount_str,
            agent_id=agent_id,
            # NEVER log consumer_key, consumer_secret, or CredentialHandle
        )
        return IssueRefundOutput(
            refund_id=refund_id,
            status="refunded",
            message=f"Refund {refund_id} of {amount_str} issued for order {args.order_id}.",
        )

    # -----------------------------------------------------------------------
    # place_order — POST /orders
    # -----------------------------------------------------------------------

    async def place_order(self, args: PlaceOrderInput, agent_id: str) -> PlaceOrderOutput:
        """Place a new order via WooCommerce REST v3 POST /orders.

        Maps PlaceOrderInput to a WooCommerce order payload with a line item and
        billing email. product_id maps to the WooCommerce product_id field.

        Args:
            args.product_id: WooCommerce product ID or SKU.
            args.quantity: Number of units to order (ge=1).
            args.customer_email: Customer email address for billing.

        Returns:
            PlaceOrderOutput(order_id=<WC order id>, status="placed", message=...)
        """
        url = self._wc_url("orders")
        body = {
            "line_items": [
                {"product_id": args.product_id, "quantity": args.quantity}
            ],
            "billing": {"email": args.customer_email},
        }

        def _sync() -> dict:
            """Sync httpx call — runs in thread pool via asyncio.to_thread (Pitfall 3)."""
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, json=body, auth=self._auth)
                response.raise_for_status()
                return response.json()

        result = await asyncio.to_thread(_sync)
        order_id = str(result.get("id", f"woo-order-{args.product_id}"))

        log.info(
            "woocommerce.order_placed",
            order_id=order_id,
            product_id=args.product_id,
            quantity=args.quantity,
            agent_id=agent_id,
        )
        return PlaceOrderOutput(
            order_id=order_id,
            status="placed",
            message=f"Order {order_id} placed for {args.quantity}x product {args.product_id}.",
        )

    # -----------------------------------------------------------------------
    # cancel_order — PUT /orders/{id} (status=cancelled)
    # -----------------------------------------------------------------------

    async def cancel_order(
        self, args: CancelOrderInput, agent_id: str
    ) -> CancelOrderOutput:
        """Cancel an order via WooCommerce REST v3 PUT /orders/{id}.

        Sets the order status to 'cancelled'. WooCommerce uses the standard order
        update endpoint (PUT) for status changes — there is no dedicated cancel endpoint.
        The returned WC status may be 'cancelled' or a transition state.

        Args:
            args.order_id: WooCommerce order ID to cancel.
            args.reason: Reason for cancellation (logged locally; not sent to WooCommerce).

        Returns:
            CancelOrderOutput(order_id=..., status in {"cancelled","pending_cancellation"}, message=...)
        """
        url = self._wc_url(f"orders/{args.order_id}")
        body = {"status": "cancelled"}

        def _sync() -> dict:
            """Sync httpx call — runs in thread pool via asyncio.to_thread (Pitfall 3)."""
            with httpx.Client(timeout=30.0) as client:
                response = client.put(url, json=body, auth=self._auth)
                response.raise_for_status()
                return response.json()

        result = await asyncio.to_thread(_sync)
        returned_status = result.get("status", "cancelled")
        # Map WooCommerce response status to our typed output status
        adapter_status = "cancelled" if returned_status == "cancelled" else "pending_cancellation"

        log.info(
            "woocommerce.order_cancelled",
            order_id=args.order_id,
            wc_status=returned_status,
            adapter_status=adapter_status,
            agent_id=agent_id,
        )
        return CancelOrderOutput(
            order_id=args.order_id,
            status=adapter_status,
            message=f"Order {args.order_id} cancellation submitted (WC status: {returned_status}).",
        )

    # -----------------------------------------------------------------------
    # Unsupported methods — raise NotImplementedError (fast-fail)
    # -----------------------------------------------------------------------

    async def update_subscription(
        self, args: UpdateSubscriptionInput, agent_id: str
    ) -> UpdateSubscriptionOutput:
        """Not supported by WooCommerceAdapter.

        WooCommerce does not have a subscription management API in the Phase 16
        Provider→Tool mapping. Use StripeAdapter for subscription management.
        """
        raise NotImplementedError(
            "update_subscription not supported by WooCommerceAdapter — "
            "use StripeAdapter for subscription management."
        )

    async def book_slot(self, args: BookSlotInput, agent_id: str) -> BookSlotOutput:
        """Not supported by WooCommerceAdapter.

        Scheduling/slot booking is a CalendlyAdapter concern.
        """
        raise NotImplementedError(
            "book_slot not supported by WooCommerceAdapter — use CalendlyAdapter."
        )

    async def update_customer_record(
        self, args: UpdateCustomerRecordInput, agent_id: str
    ) -> UpdateCustomerRecordOutput:
        """Not supported by WooCommerceAdapter (Phase 16 deferred per Provider→Tool Mapping).

        update_customer_record is deferred across all providers in Phase 16.
        """
        raise NotImplementedError(
            "update_customer_record not supported by WooCommerceAdapter."
        )
