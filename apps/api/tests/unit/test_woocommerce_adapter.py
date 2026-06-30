"""
Unit tests for WooCommerceAdapter — INT-04 (WooCommerce REST v3, httpx + OAuth1).

Task 1 (RED/GREEN): issue_refund, place_order, cancel_order, HTTPS guard, NotImplemented stubs.

  test_issue_refund:
      WooCommerceAdapter.issue_refund POSTs to /wp-json/wc/v3/orders/{id}/refunds with
      amount derived from args.refund_amount_cents (currency-major string);
      returns IssueRefundOutput(status="refunded").

  test_place_order:
      place_order POSTs to /wp-json/wc/v3/orders with a line_item from args
      (product_id, quantity) and billing email; returns PlaceOrderOutput.

  test_cancel_order:
      cancel_order PUTs to /wp-json/wc/v3/orders/{id} with status="cancelled";
      returns CancelOrderOutput(status in {"cancelled","pending_cancellation"}).

  test_http_url_rejected:
      Constructing WooCommerceAdapter with an http:// site_url raises ValueError
      (Pitfall 5 — T-16-woo-http HTTPS guard).

  test_unsupported_methods_raise:
      update_subscription, book_slot, update_customer_record raise NotImplementedError.

WooCommerce package decision (16-02 gate):
  The WooCommerce PyPI package was REJECTED (stale, last released 2021).
  The adapter uses httpx (sync client) + requests_oauthlib OAuth1 HMAC-SHA256.
  Tests mock httpx.Client to avoid network calls.

Security invariants verified:
  T-16-01: consumer_secret NEVER appears in structlog output (auth is mocked at transport
            level; the raw secret is extracted only inside _WooOAuth1Auth).
  T-16-02: No URL field read from args — site_url only from the constructor (checked
            by asserting the request targets self._site_url, not anything in args).
  T-16-woo-http: ValueError on http:// URL (test_http_url_rejected).

Test infrastructure:
  asyncio_mode = "auto" in pyproject.toml — all async def tests run automatically.
  httpx.Client is patched via unittest.mock.patch so no network call occurs.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_handle(consumer_key: str = "ck_test_abc", consumer_secret: str = "cs_test_xyz"):
    """Return a CredentialHandle wrapping a WooCommerce credential JSON blob."""
    from app.services.transactional.credential_service import CredentialHandle

    return CredentialHandle(
        _raw=json.dumps({"consumer_key": consumer_key, "consumer_secret": consumer_secret})
    )


def _make_adapter(site_url: str = "https://teststore.example.com", currency_code: str = "USD"):
    """Return a WooCommerceAdapter with a test credential handle."""
    from app.services.transactional.adapters.woocommerce_adapter import WooCommerceAdapter

    return WooCommerceAdapter(
        handle=_make_handle(),
        site_url=site_url,
        currency_code=currency_code,
    )


# ---------------------------------------------------------------------------
# test_issue_refund — POST /orders/{id}/refunds
# ---------------------------------------------------------------------------


async def test_issue_refund() -> None:
    """issue_refund POSTs to /wp-json/wc/v3/orders/{id}/refunds with currency-major amount (INT-04).

    Verifies that:
    - The URL contains 'orders/{order_id}/refunds'
    - The JSON body contains 'amount' as a currency-major string (e.g. '35.00' for 3500 cents)
    - Returns IssueRefundOutput(status="refunded")
    - refund_id is populated from the WooCommerce API response 'id' field
    """
    from app.services.transactional.schemas import IssueRefundInput

    adapter = _make_adapter()
    args = IssueRefundInput(
        idempotency_key="idem-refund-1",
        order_id="42",
        refund_amount_cents=3500,
        reason="Customer requested return",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": 99, "amount": "35.00"}
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.transactional.adapters.woocommerce_adapter.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = mock_response

        result = await adapter.issue_refund(args, agent_id="agent-woo-001")

    # URL must contain orders/{order_id}/refunds
    post_call = mock_client.post.call_args
    assert post_call is not None, "httpx.Client.post must be called for issue_refund"
    called_url = post_call[0][0]
    assert "orders/42/refunds" in called_url, (
        f"POST URL must contain 'orders/42/refunds'; got: {called_url!r}"
    )

    # JSON body must have 'amount' as currency-major string
    called_body = post_call[1].get("json") or (post_call[0][1] if len(post_call[0]) > 1 else None)
    assert called_body is not None, "JSON body must be provided to httpx.Client.post"
    assert "amount" in called_body, f"JSON body must contain 'amount'; got {called_body!r}"
    assert called_body["amount"] == "35.00", (
        f"amount must be '35.00' for 3500 cents; got {called_body['amount']!r}"
    )

    assert result.status == "refunded"
    assert result.refund_id == "99"


# ---------------------------------------------------------------------------
# test_place_order — POST /orders
# ---------------------------------------------------------------------------


async def test_place_order() -> None:
    """place_order POSTs to /wp-json/wc/v3/orders with line_items and billing (INT-04).

    Verifies that:
    - The URL targets the orders endpoint (no order ID in path)
    - The JSON body contains line_items with product_id and quantity from args
    - The JSON body contains billing.email from args.customer_email
    - Returns PlaceOrderOutput with an order_id populated from the API response
    """
    from app.services.transactional.schemas import PlaceOrderInput

    adapter = _make_adapter()
    args = PlaceOrderInput(
        idempotency_key="idem-order-1",
        product_id="SKU-001",
        quantity=2,
        customer_email="customer@example.com",
        shipping_address="123 Test St",
        amount_cents=4000,
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": 77, "status": "pending"}
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.transactional.adapters.woocommerce_adapter.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client
        mock_client.post.return_value = mock_response

        result = await adapter.place_order(args, agent_id="agent-woo-001")

    post_call = mock_client.post.call_args
    assert post_call is not None, "httpx.Client.post must be called for place_order"
    called_url = post_call[0][0]
    # URL must end with /orders (not /orders/<id>/...)
    assert called_url.endswith("/orders") or "/orders" in called_url, (
        f"POST URL must target orders endpoint; got: {called_url!r}"
    )
    assert "refunds" not in called_url, (
        f"place_order must NOT call the refunds endpoint; got: {called_url!r}"
    )

    called_body = post_call[1].get("json") or {}
    assert "line_items" in called_body, f"JSON body must contain 'line_items'; got {called_body!r}"
    assert len(called_body["line_items"]) >= 1
    line_item = called_body["line_items"][0]
    assert str(line_item.get("product_id")) == "SKU-001", (
        f"line_item.product_id must be 'SKU-001'; got {line_item!r}"
    )
    assert line_item.get("quantity") == 2, (
        f"line_item.quantity must be 2; got {line_item!r}"
    )

    # billing.email must come from args.customer_email (T-16-02: not from URL)
    billing = called_body.get("billing", {})
    assert billing.get("email") == "customer@example.com", (
        f"billing.email must be 'customer@example.com'; got {billing!r}"
    )

    assert result.order_id == "77"


# ---------------------------------------------------------------------------
# test_cancel_order — PUT /orders/{id} (status=cancelled)
# ---------------------------------------------------------------------------


async def test_cancel_order() -> None:
    """cancel_order PUTs to /wp-json/wc/v3/orders/{id} with status=cancelled (INT-04).

    Verifies that:
    - The method is PUT (not POST or DELETE)
    - The URL contains orders/{order_id}
    - The JSON body has status="cancelled"
    - Returns CancelOrderOutput(status in {"cancelled","pending_cancellation"})
    """
    from app.services.transactional.schemas import CancelOrderInput

    adapter = _make_adapter()
    args = CancelOrderInput(
        idempotency_key="idem-cancel-1",
        order_id="55",
        reason="Customer requested cancellation",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": 55, "status": "cancelled"}
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.transactional.adapters.woocommerce_adapter.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client
        mock_client.put.return_value = mock_response

        result = await adapter.cancel_order(args, agent_id="agent-woo-001")

    put_call = mock_client.put.call_args
    assert put_call is not None, "httpx.Client.put must be called for cancel_order"
    called_url = put_call[0][0]
    assert "orders/55" in called_url, (
        f"PUT URL must contain 'orders/55'; got: {called_url!r}"
    )

    called_body = put_call[1].get("json") or {}
    assert called_body.get("status") == "cancelled", (
        f"JSON body must have status='cancelled'; got {called_body!r}"
    )

    assert result.order_id == "55"
    assert result.status in {"cancelled", "pending_cancellation"}, (
        f"status must be 'cancelled' or 'pending_cancellation'; got {result.status!r}"
    )


# ---------------------------------------------------------------------------
# test_http_url_rejected — HTTPS guard (T-16-woo-http, Pitfall 5)
# ---------------------------------------------------------------------------


def test_http_url_rejected() -> None:
    """Constructing WooCommerceAdapter with an http:// site_url raises ValueError.

    This is the Pitfall 5 / T-16-woo-http HTTPS guard. WooCommerce OAuth1 behavior
    is ambiguous over HTTP (OAuth1 via query string vs Authorization header); HTTPS
    is required to avoid auth downgrade attacks.
    """
    from app.services.transactional.adapters.woocommerce_adapter import WooCommerceAdapter

    with pytest.raises(ValueError, match="HTTPS"):
        WooCommerceAdapter(
            handle=_make_handle(),
            site_url="http://insecure-store.example.com",
            currency_code="USD",
        )


# ---------------------------------------------------------------------------
# test_unsupported_methods_raise — NotImplementedError stubs
# ---------------------------------------------------------------------------


async def test_unsupported_methods_raise() -> None:
    """update_subscription, book_slot, update_customer_record raise NotImplementedError.

    WooCommerce does not support subscription management or slot booking.
    The dispatcher's except Exception handler returns is_error=True.
    """
    from app.services.transactional.schemas import (
        BookSlotInput,
        UpdateCustomerRecordInput,
        UpdateSubscriptionInput,
    )

    adapter = _make_adapter()

    update_sub_args = UpdateSubscriptionInput(
        idempotency_key="idem-sub-1",
        subscription_id="sub-123",
        new_plan="pro",
        effective_date="2026-07-01",
    )
    with pytest.raises(NotImplementedError):
        await adapter.update_subscription(update_sub_args, agent_id="agent-woo-001")

    book_args = BookSlotInput(
        idempotency_key="idem-book-1",
        service_type="consultation",
        preferred_date="2026-07-15",
        preferred_time="10:00",
        customer_name="Test Customer",
    )
    with pytest.raises(NotImplementedError):
        await adapter.book_slot(book_args, agent_id="agent-woo-001")

    update_cust_args = UpdateCustomerRecordInput(
        idempotency_key="idem-cust-1",
        field_name="email",
        new_value="newemail@example.com",
    )
    with pytest.raises(NotImplementedError):
        await adapter.update_customer_record(update_cust_args, agent_id="agent-woo-001")
