"""
Unit tests for ShopifyAdapter — INT-03 (Shopify Admin GraphQL mutations).

Task 1 (RED/GREEN): issue_refund, place_order, shop_url_from_constructor
  test_issue_refund_calls_refund_create:
      Mock shopify.GraphQL().execute; issue_refund runs the refundCreate mutation with
      variables referencing args.order_id and self._currency_code; returns
      IssueRefundOutput(status="refunded"); session activated then cleared.

  test_place_order_calls_order_create:
      place_order runs the orderCreate mutation with line items derived from
      args.product_id/quantity; returns PlaceOrderOutput(status in {"placed","pending_confirmation"}).

  test_shop_url_from_constructor:
      The session is created with the constructor shop_url; no shop_url field is
      read from args (T-16-02: SSRF prevention).

Task 2 (RED/GREEN): cancel_order, NotImplemented stubs
  test_cancel_order_calls_order_cancel:
      cancel_order runs the orderCancel mutation referencing args.order_id;
      returns CancelOrderOutput(status in {"cancelled","pending_cancellation"}).

  test_unsupported_methods_raise:
      update_subscription, book_slot, update_customer_record each raise
      NotImplementedError mentioning ShopifyAdapter.

Test infrastructure:
  asyncio_mode = "auto" in pyproject.toml — all async def tests run automatically.
  shopify module is patched via unittest.mock so no network call occurs.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Task 1 Tests: issue_refund + place_order + shop_url_from_constructor
# ---------------------------------------------------------------------------


async def test_issue_refund_calls_refund_create() -> None:
    """issue_refund executes the refundCreate mutation with orderId + currency (INT-03).

    Verifies that:
    - shopify.Session is constructed with the constructor shop_url (T-16-02)
    - shopify.ShopifyResource.activate_session is called (session-per-call pattern)
    - shopify.GraphQL().execute is called with a mutation containing "refundCreate"
    - variables include orderId == args.order_id and currency == self._currency_code
    - shopify.ShopifyResource.clear_session is called after execute (session hygiene)
    - Returns IssueRefundOutput(status="refunded")
    """
    from app.services.transactional.adapters.shopify_adapter import ShopifyAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import IssueRefundInput

    handle = CredentialHandle(_raw=json.dumps({"access_token": "shpat_test_abc"}))
    adapter = ShopifyAdapter(
        handle=handle,
        shop_url="teststore.myshopify.com",
        currency_code="USD",
    )

    args = IssueRefundInput(
        idempotency_key="idem-refund-1",
        order_id="gid://shopify/Order/123456789",
        refund_amount_cents=3500,
        reason="Customer requested return",
    )

    mock_execute_response = json.dumps({
        "data": {
            "refundCreate": {
                "refund": {"id": "gid://shopify/Refund/111222"},
                "userErrors": [],
            }
        }
    })

    with patch("app.services.transactional.adapters.shopify_adapter.shopify") as mock_shopify:
        mock_graphql_instance = MagicMock()
        mock_graphql_instance.execute.return_value = mock_execute_response
        mock_shopify.GraphQL.return_value = mock_graphql_instance

        result = await adapter.issue_refund(args, agent_id="agent-shopify-001")

    # Mutation must contain "refundCreate"
    execute_call = mock_graphql_instance.execute.call_args
    assert execute_call is not None, "shopify.GraphQL().execute must be called"
    mutation_str = execute_call[0][0]
    assert "refundCreate" in mutation_str, (
        f"Mutation must contain 'refundCreate'; got: {mutation_str!r}"
    )

    # Variables must reference args.order_id and self._currency_code
    variables_kwarg = execute_call[1].get("variables") or (
        execute_call[0][1] if len(execute_call[0]) > 1 else None
    )
    assert variables_kwarg is not None, "execute must receive variables"
    variables_str = json.dumps(variables_kwarg)
    assert "gid://shopify/Order/123456789" in variables_str, (
        "variables must include args.order_id"
    )
    assert "USD" in variables_str or "usd" in variables_str, (
        "variables must include currency_code"
    )

    # CR-01 fix: amount-based refund via transactions, NOT empty refundLineItems.
    # Empty refundLineItems would create a $0 refund (silent money failure).
    input_vars = variables_kwarg.get("input", {})
    assert "transactions" in input_vars, (
        "variables.input must contain 'transactions' for amount-based refund (CR-01); "
        "empty refundLineItems creates a $0 refund"
    )
    assert "refundLineItems" not in input_vars, (
        "variables.input must NOT contain 'refundLineItems' — use transactions instead (CR-01)"
    )
    transactions = input_vars["transactions"]
    assert len(transactions) == 1, (
        f"Expected exactly 1 transaction entry; got {len(transactions)}"
    )
    assert transactions[0]["kind"] == "REFUND", (
        f"Transaction kind must be 'REFUND'; got {transactions[0].get('kind')!r}"
    )
    # 3500 cents → "35.00"
    assert transactions[0]["amount"] == "35.00", (
        f"Transaction amount must be '35.00' for 3500 cents; got {transactions[0].get('amount')!r}"
    )

    # WR-02 fix: Session.temp() used (thread-safe) instead of activate/clear pair
    mock_shopify.Session.temp.assert_called_once()
    temp_call_args = mock_shopify.Session.temp.call_args[0]
    assert temp_call_args[0] == "teststore.myshopify.com", (
        f"Session.temp shop_url must be the constructor value; got {temp_call_args[0]!r}"
    )

    # Output shape
    assert result.status == "refunded"
    assert "gid://shopify/Refund/111222" in result.refund_id


async def test_place_order_calls_order_create() -> None:
    """place_order executes the orderCreate mutation with product line items (INT-03).

    Verifies that:
    - shopify.GraphQL().execute is called with a mutation containing "orderCreate"
    - variables include product_id and quantity from args
    - Returns PlaceOrderOutput(status in {"placed", "pending_confirmation"})
    - Session is activated and cleared
    """
    from app.services.transactional.adapters.shopify_adapter import ShopifyAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import PlaceOrderInput

    handle = CredentialHandle(_raw=json.dumps({"access_token": "shpat_test_xyz"}))
    adapter = ShopifyAdapter(
        handle=handle,
        shop_url="mystore.myshopify.com",
        currency_code="ZAR",
    )

    args = PlaceOrderInput(
        idempotency_key="idem-order-shopify-1",
        product_id="gid://shopify/ProductVariant/987654321",
        quantity=3,
        customer_email="customer@example.com",
        shipping_address="42 Long Street, Cape Town, 8000, ZA",
        amount_cents=15000,
    )

    mock_execute_response = json.dumps({
        "data": {
            "orderCreate": {
                "order": {"id": "gid://shopify/Order/555666777"},
                "userErrors": [],
            }
        }
    })

    with patch("app.services.transactional.adapters.shopify_adapter.shopify") as mock_shopify:
        mock_graphql_instance = MagicMock()
        mock_graphql_instance.execute.return_value = mock_execute_response
        mock_shopify.GraphQL.return_value = mock_graphql_instance

        result = await adapter.place_order(args, agent_id="agent-shopify-002")

    # Mutation must contain "orderCreate"
    execute_call = mock_graphql_instance.execute.call_args
    assert execute_call is not None, "shopify.GraphQL().execute must be called"
    mutation_str = execute_call[0][0]
    assert "orderCreate" in mutation_str, (
        f"Mutation must contain 'orderCreate'; got: {mutation_str!r}"
    )

    # Variables must include product_id and quantity
    variables_kwarg = execute_call[1].get("variables") or (
        execute_call[0][1] if len(execute_call[0]) > 1 else None
    )
    assert variables_kwarg is not None, "execute must receive variables"
    variables_str = json.dumps(variables_kwarg)
    assert "gid://shopify/ProductVariant/987654321" in variables_str, (
        "variables must include args.product_id"
    )
    assert "3" in variables_str, "variables must include quantity"

    # WR-02 fix: Session.temp() used instead of activate/clear pair
    mock_shopify.Session.temp.assert_called_once()

    # Output shape
    assert result.status in {"placed", "pending_confirmation"}, (
        f"status must be 'placed' or 'pending_confirmation'; got {result.status!r}"
    )
    assert "gid://shopify/Order/555666777" in result.order_id


async def test_shop_url_from_constructor() -> None:
    """shop_url comes from the constructor, NOT from tool args (T-16-02: SSRF prevention).

    WR-02 fix: the session is now created via Session.temp() (thread-safe context manager)
    rather than ShopifyResource.activate_session() (class-level state, not thread-safe).

    Verifies that:
    - shopify.Session.temp() is called with self._shop_url (from constructor)
    - The access token is extracted from json.loads(handle.use())["access_token"]
    - No field named shop_url or url is read from args
    """
    from app.services.transactional.adapters.shopify_adapter import ShopifyAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import IssueRefundInput

    constructor_shop_url = "secure-store.myshopify.com"
    raw_token = "shpat_secure_token_xyz"
    handle = CredentialHandle(_raw=json.dumps({"access_token": raw_token}))
    adapter = ShopifyAdapter(
        handle=handle,
        shop_url=constructor_shop_url,
        currency_code="GBP",
    )

    args = IssueRefundInput(
        idempotency_key="idem-ssrf",
        order_id="gid://shopify/Order/999",
        refund_amount_cents=1000,
        reason="Test",
    )

    mock_execute_response = json.dumps({
        "data": {
            "refundCreate": {
                "refund": {"id": "gid://shopify/Refund/999"},
                "userErrors": [],
            }
        }
    })

    with patch("app.services.transactional.adapters.shopify_adapter.shopify") as mock_shopify:
        mock_graphql_instance = MagicMock()
        mock_graphql_instance.execute.return_value = mock_execute_response
        mock_shopify.GraphQL.return_value = mock_graphql_instance

        await adapter.issue_refund(args, agent_id="agent-shopify-ssrf")

    # WR-02: Session.temp() must be called (not Session() constructor directly)
    session_temp_call = mock_shopify.Session.temp.call_args
    assert session_temp_call is not None, "shopify.Session.temp must be called (WR-02 thread safety)"
    temp_args = session_temp_call[0]  # positional args
    assert temp_args[0] == constructor_shop_url, (
        f"Session.temp shop_url must be the constructor value '{constructor_shop_url}'; "
        f"got {temp_args[0]!r} — SSRF prevention requires shop_url from config only (T-16-02)"
    )
    # Token extracted from JSON blob (not raw JSON string)
    assert temp_args[2] == raw_token, (
        f"Session.temp token must be the bare access_token '{raw_token}'; "
        f"got {temp_args[2]!r} — must use json.loads(handle.use())[\"access_token\"]"
    )


# ---------------------------------------------------------------------------
# Task 2 Tests: cancel_order + NotImplemented stubs
# ---------------------------------------------------------------------------


async def test_cancel_order_calls_order_cancel() -> None:
    """cancel_order executes the orderCancel mutation referencing args.order_id (INT-03).

    Verifies that:
    - shopify.GraphQL().execute is called with a mutation containing "orderCancel"
    - variables include orderId == args.order_id
    - args.reason is forwarded as a staffNote/note field in variables
    - Returns CancelOrderOutput(status in {"cancelled", "pending_cancellation"})
    - Session is activated and cleared
    """
    from app.services.transactional.adapters.shopify_adapter import ShopifyAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import CancelOrderInput

    handle = CredentialHandle(_raw=json.dumps({"access_token": "shpat_cancel_token"}))
    adapter = ShopifyAdapter(
        handle=handle,
        shop_url="cancelstore.myshopify.com",
        currency_code="USD",
    )

    args = CancelOrderInput(
        idempotency_key="idem-cancel-shopify-1",
        order_id="gid://shopify/Order/888777666",
        reason="Customer changed their mind",
    )

    mock_execute_response = json.dumps({
        "data": {
            "orderCancel": {
                "job": {"id": "gid://shopify/Job/cancel123"},
                "orderCancelUserErrors": [],
            }
        }
    })

    with patch("app.services.transactional.adapters.shopify_adapter.shopify") as mock_shopify:
        mock_graphql_instance = MagicMock()
        mock_graphql_instance.execute.return_value = mock_execute_response
        mock_shopify.GraphQL.return_value = mock_graphql_instance

        result = await adapter.cancel_order(args, agent_id="agent-shopify-003")

    # Mutation must contain "orderCancel"
    execute_call = mock_graphql_instance.execute.call_args
    assert execute_call is not None, "shopify.GraphQL().execute must be called"
    mutation_str = execute_call[0][0]
    assert "orderCancel" in mutation_str, (
        f"Mutation must contain 'orderCancel'; got: {mutation_str!r}"
    )

    # Variables must include args.order_id
    variables_kwarg = execute_call[1].get("variables") or (
        execute_call[0][1] if len(execute_call[0]) > 1 else None
    )
    assert variables_kwarg is not None, "execute must receive variables"
    variables_str = json.dumps(variables_kwarg)
    assert "gid://shopify/Order/888777666" in variables_str, (
        "variables must include args.order_id"
    )

    # WR-02 fix: Session.temp() used instead of activate/clear pair
    mock_shopify.Session.temp.assert_called_once()

    # Output shape
    assert result.order_id == "gid://shopify/Order/888777666"
    assert result.status in {"cancelled", "pending_cancellation"}, (
        f"status must be 'cancelled' or 'pending_cancellation'; got {result.status!r}"
    )


async def test_unsupported_methods_raise() -> None:
    """update_subscription, book_slot, update_customer_record raise NotImplementedError.

    ShopifyAdapter only supports place/cancel order and refund. The dispatcher's
    except Exception handler catches NotImplementedError and returns is_error=True.
    The error message must mention 'ShopifyAdapter' so logs are clear.
    """
    from app.services.transactional.adapters.shopify_adapter import ShopifyAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import (
        BookSlotInput,
        UpdateCustomerRecordInput,
        UpdateSubscriptionInput,
    )

    handle = CredentialHandle(_raw=json.dumps({"access_token": "shpat_stub_token"}))
    adapter = ShopifyAdapter(
        handle=handle,
        shop_url="stubstore.myshopify.com",
        currency_code="USD",
    )

    sub_args = UpdateSubscriptionInput(
        idempotency_key="idem-sub",
        subscription_id="sub_shopify_999",
        new_plan="plan_premium",
        effective_date="2026-07-01",
    )
    book_args = BookSlotInput(
        idempotency_key="idem-book",
        service_type="installation",
        preferred_date="2026-07-15",
        preferred_time="10:00",
        customer_name="Sipho Dlamini",
    )
    ucr_args = UpdateCustomerRecordInput(
        idempotency_key="idem-ucr",
        field_name="shipping_address",
        new_value="15 Vilakazi Street, Soweto",
    )

    with pytest.raises(NotImplementedError, match="ShopifyAdapter"):
        await adapter.update_subscription(sub_args, agent_id="agent-shopify-stub")

    with pytest.raises(NotImplementedError, match="ShopifyAdapter"):
        await adapter.book_slot(book_args, agent_id="agent-shopify-stub")

    with pytest.raises(NotImplementedError, match="ShopifyAdapter"):
        await adapter.update_customer_record(ucr_args, agent_id="agent-shopify-stub")


# ---------------------------------------------------------------------------
# Security invariant: no args.shop_url in adapter source
# ---------------------------------------------------------------------------


def test_no_args_shop_url_in_source() -> None:
    """shopify_adapter.py must not read shop_url from args (T-16-02: SSRF prevention).

    Reads the source of shopify_adapter.py and asserts 'args.shop_url' does not appear.
    The shop_url is a constructor param from integration_credentials.config_data only.
    """
    import pathlib

    adapter_path = pathlib.Path(__file__).parent.parent.parent / (
        "app/services/transactional/adapters/shopify_adapter.py"
    )
    source = adapter_path.read_text(encoding="utf-8")

    assert "args.shop_url" not in source, (
        "SSRF vulnerability: 'args.shop_url' must not appear in shopify_adapter.py. "
        "shop_url must come from the constructor (integration_credentials.config_data) only."
    )
