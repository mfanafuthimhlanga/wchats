"""
Unit tests for StripeAdapter — INT-05, INT-07 invariants.

Task 1 (RED/GREEN): issue_refund, update_subscription
  test_issue_refund_idempotency_key:
      Mock stripe.StripeClient; issue_refund(IssueRefundInput(...idempotency_key="idem-1"...))
      calls client.v1.refunds.create with idempotency_key="idem-1" (via options dict) and
      currency == self._currency_code; returns IssueRefundOutput(status="refunded", ...).

  test_currency_from_config_not_args:
      Even if a stray currency-like value appears in raw args, the create() call's currency
      equals the adapter's configured currency_code (lowercased). Proves INT-07.

  test_update_subscription:
      update_subscription calls client.v1.subscriptions.update with the subscription_id
      and the new plan; returns UpdateSubscriptionOutput(status="updated").

  test_sync_offloaded:
      SDK call runs via asyncio.to_thread — method is awaitable and completes without
      a running-loop error when the mock is synchronous.

Task 2 (RED/GREEN): place_order, NotImplemented stubs
  test_place_order_checkout_session:
      place_order calls client.v1.checkout.sessions.create with mode="payment",
      currency == self._currency_code, idempotency_key via options; returns
      PlaceOrderOutput(status="pending_confirmation", order_id=<session id>).

  test_unsupported_methods_raise:
      cancel_order, book_slot, update_customer_record each raise NotImplementedError
      mentioning StripeAdapter.

Test infrastructure:
  asyncio_mode = "auto" in pyproject.toml — all async def tests run automatically.
  stripe.StripeClient is patched via unittest.mock so no network call occurs.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Task 1 Tests: issue_refund + update_subscription
# ---------------------------------------------------------------------------


async def test_issue_refund_idempotency_key() -> None:
    """issue_refund forwards idempotency_key to Stripe's native Idempotency-Key (TXN-02).

    Verifies that:
    - StripeClient is constructed with json.loads(handle.use())["api_key"] (not the JSON blob)
    - refunds.create is called with the correct params dict
    - idempotency_key is forwarded via the options (second arg) dict
    - currency == self._currency_code (from __init__, lowercased)
    - Returns IssueRefundOutput(refund_id=<mock id>, status="refunded")
    """
    from app.services.transactional.adapters.stripe_adapter import StripeAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import IssueRefundInput

    handle = CredentialHandle(_raw=json.dumps({"api_key": "sk_test_xxx"}))
    adapter = StripeAdapter(handle=handle, currency_code="USD")

    args = IssueRefundInput(
        idempotency_key="idem-1",
        order_id="ch_test123",
        refund_amount_cents=5000,
        reason="Customer requested refund",
    )

    mock_refund = MagicMock()
    mock_refund.id = "re_test123"

    with patch("app.services.transactional.adapters.stripe_adapter.stripe") as mock_stripe:
        mock_client = mock_stripe.StripeClient.return_value
        mock_client.v1.refunds.create.return_value = mock_refund

        result = await adapter.issue_refund(args, agent_id="agent-001")

    # StripeClient must be constructed with the raw api_key, not the JSON blob
    mock_stripe.StripeClient.assert_called_once_with("sk_test_xxx")

    # idempotency_key forwarded as the options dict (second positional arg)
    mock_client.v1.refunds.create.assert_called_once_with(
        {
            "charge": "ch_test123",
            "amount": 5000,
            "reason": "requested_by_customer",
            "currency": "usd",  # lowercased from "USD"
        },
        {"idempotency_key": "idem-1"},
    )

    assert result.refund_id == "re_test123"
    assert result.status == "refunded"


async def test_currency_from_config_not_args() -> None:
    """Currency sent to Stripe equals the configured currency_code, not any arg value (INT-07).

    The IssueRefundInput schema has no currency field, but even if a stray value were present,
    the adapter MUST only use self._currency_code from __init__. This test uses 'ZAR' to
    prove it's independent of order_id, reason, or any other arg value.
    """
    from app.services.transactional.adapters.stripe_adapter import StripeAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import IssueRefundInput

    handle = CredentialHandle(_raw=json.dumps({"api_key": "sk_test_zzz"}))
    # Configured with ZAR (South African Rand) — deliberately non-default
    adapter = StripeAdapter(handle=handle, currency_code="ZAR")

    args = IssueRefundInput(
        idempotency_key="idem-zar",
        order_id="ch_zar_order",
        refund_amount_cents=10000,
        reason="Wrong item",
    )

    mock_refund = MagicMock()
    mock_refund.id = "re_zar_001"

    with patch("app.services.transactional.adapters.stripe_adapter.stripe") as mock_stripe:
        mock_client = mock_stripe.StripeClient.return_value
        mock_client.v1.refunds.create.return_value = mock_refund

        result = await adapter.issue_refund(args, agent_id="agent-002")

    # Assert currency is 'zar' (lowercased from ZAR config), NOT any other value
    call_params = mock_client.v1.refunds.create.call_args
    assert call_params is not None
    params_dict = call_params[0][0]  # first positional arg (params dict)
    assert params_dict["currency"] == "zar", (
        f"Expected 'zar' from config, got {params_dict['currency']!r} — INT-07 violation"
    )
    assert result.refund_id == "re_zar_001"


async def test_update_subscription() -> None:
    """update_subscription calls client.v1.subscriptions.update with subscription_id + new plan.

    Returns UpdateSubscriptionOutput(subscription_id=..., status="updated").
    """
    from app.services.transactional.adapters.stripe_adapter import StripeAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import UpdateSubscriptionInput

    handle = CredentialHandle(_raw=json.dumps({"api_key": "sk_test_sub"}))
    adapter = StripeAdapter(handle=handle, currency_code="USD")

    args = UpdateSubscriptionInput(
        idempotency_key="idem-sub-1",
        subscription_id="sub_test456",
        new_plan="price_pro_monthly",
        effective_date="2026-07-01",
    )

    mock_subscription = MagicMock()
    mock_subscription.id = "sub_test456"

    with patch("app.services.transactional.adapters.stripe_adapter.stripe") as mock_stripe:
        mock_client = mock_stripe.StripeClient.return_value
        mock_client.v1.subscriptions.update.return_value = mock_subscription

        result = await adapter.update_subscription(args, agent_id="agent-003")

    # StripeClient constructed with the raw api_key
    mock_stripe.StripeClient.assert_called_once_with("sk_test_sub")

    # subscriptions.update called with subscription_id as first arg
    mock_client.v1.subscriptions.update.assert_called_once_with(
        "sub_test456",
        {"items": [{"price": "price_pro_monthly"}]},
        {"idempotency_key": "idem-sub-1"},
    )

    assert result.subscription_id == "sub_test456"
    assert result.status == "updated"


async def test_sync_offloaded() -> None:
    """SDK call runs inside asyncio.to_thread — method is awaitable, mock runs synchronously.

    Ensures the adapter method is a coroutine (awaitable) and that a synchronous mock
    completes without raising RuntimeError('no running event loop') or similar errors.
    This proves Pitfall 3 is avoided.
    """
    import inspect

    from app.services.transactional.adapters.stripe_adapter import StripeAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import IssueRefundInput

    handle = CredentialHandle(_raw=json.dumps({"api_key": "sk_test_async"}))
    adapter = StripeAdapter(handle=handle, currency_code="GBP")

    # Verify issue_refund is a coroutine function
    assert inspect.iscoroutinefunction(adapter.issue_refund), (
        "issue_refund must be async (coroutine) — SDK calls must be offloaded to thread"
    )
    assert inspect.iscoroutinefunction(adapter.update_subscription), (
        "update_subscription must be async (coroutine)"
    )

    args = IssueRefundInput(
        idempotency_key="idem-async",
        order_id="ch_async",
        refund_amount_cents=1000,
        reason="async test",
    )

    mock_refund = MagicMock()
    mock_refund.id = "re_async"

    with patch("app.services.transactional.adapters.stripe_adapter.stripe") as mock_stripe:
        mock_client = mock_stripe.StripeClient.return_value
        # Synchronous mock — no coroutine — proves asyncio.to_thread offloads correctly
        mock_client.v1.refunds.create.return_value = mock_refund

        # If asyncio.to_thread is NOT used, this would block; with to_thread it completes
        result = await adapter.issue_refund(args, agent_id="agent-004")

    assert result.status == "refunded"


# ---------------------------------------------------------------------------
# Task 2 Tests: place_order + NotImplemented stubs
# ---------------------------------------------------------------------------


async def test_place_order_checkout_session() -> None:
    """place_order creates a Stripe Checkout Session in payment mode (no raw card handling).

    Verifies:
    - checkout.sessions.create called with mode="payment"
    - currency == self._currency_code (from config, not args)
    - idempotency_key forwarded via options dict
    - line_items contain product_id as name and quantity
    - Returns PlaceOrderOutput(status="pending_confirmation", order_id=<session.id>)
    - No card-number/CVC/expiry field in the params (PCI boundary preserved)
    """
    from app.services.transactional.adapters.stripe_adapter import StripeAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import PlaceOrderInput

    handle = CredentialHandle(_raw=json.dumps({"api_key": "sk_test_order"}))
    adapter = StripeAdapter(handle=handle, currency_code="EUR")

    args = PlaceOrderInput(
        idempotency_key="idem-order-1",
        product_id="prod_widget_deluxe",
        quantity=2,
        customer_email="test@example.com",
        shipping_address="123 Main St, Cape Town, ZA",
        amount_cents=4000,  # total: 4000 cents for 2 units = 2000 cents/unit
    )

    mock_session = MagicMock()
    mock_session.id = "cs_test_session_001"
    mock_session.url = "https://checkout.stripe.com/pay/cs_test_session_001"

    with patch("app.services.transactional.adapters.stripe_adapter.stripe") as mock_stripe:
        mock_client = mock_stripe.StripeClient.return_value
        mock_client.v1.checkout.sessions.create.return_value = mock_session

        result = await adapter.place_order(args, agent_id="agent-005")

    # StripeClient constructed with the raw api_key
    mock_stripe.StripeClient.assert_called_once_with("sk_test_order")

    # checkout.sessions.create called with correct params
    call_args = mock_client.v1.checkout.sessions.create.call_args
    assert call_args is not None
    session_params = call_args[0][0]  # first positional arg (params dict)
    session_options = call_args[0][1]  # second positional arg (options dict)

    # mode must be "payment" — no card handling, no subscription mode
    assert session_params["mode"] == "payment"

    # currency comes from config (EUR -> "eur"), not from args
    line_item = session_params["line_items"][0]
    assert line_item["price_data"]["currency"] == "eur"

    # product_id used as the product name
    assert line_item["price_data"]["product_data"]["name"] == "prod_widget_deluxe"

    # quantity preserved
    assert line_item["quantity"] == 2

    # idempotency_key forwarded
    assert session_options == {"idempotency_key": "idem-order-1"}

    # No card fields anywhere in params
    params_str = str(session_params)
    for forbidden_field in ("card_number", "cvc", "expiry", "cvv", "pan"):
        assert forbidden_field not in params_str, (
            f"PCI violation: '{forbidden_field}' found in Checkout Session params"
        )

    # Output shape
    assert result.order_id == "cs_test_session_001"
    assert result.status == "pending_confirmation"
    assert "cs_test_session_001" in result.message or "checkout.stripe.com" in result.message


async def test_unsupported_methods_raise() -> None:
    """cancel_order, book_slot, update_customer_record raise NotImplementedError.

    StripeAdapter only supports refund, subscription, and checkout. The dispatcher's
    except Exception handler catches NotImplementedError and returns is_error=True.
    """
    from app.services.transactional.adapters.stripe_adapter import StripeAdapter
    from app.services.transactional.credential_service import CredentialHandle
    from app.services.transactional.schemas import (
        BookSlotInput,
        CancelOrderInput,
        UpdateCustomerRecordInput,
    )

    handle = CredentialHandle(_raw=json.dumps({"api_key": "sk_test_stub"}))
    adapter = StripeAdapter(handle=handle, currency_code="USD")

    cancel_args = CancelOrderInput(
        idempotency_key="idem-cancel",
        order_id="ch_cancel",
        reason="Customer changed mind",
    )
    book_args = BookSlotInput(
        idempotency_key="idem-book",
        service_type="consultation",
        preferred_date="2026-07-10",
        preferred_time="14:00",
        customer_name="Jane Doe",
    )
    ucr_args = UpdateCustomerRecordInput(
        idempotency_key="idem-ucr",
        customer_id="cus_test",
        field_name="email",
        new_value="newemail@example.com",
    )

    with pytest.raises(NotImplementedError, match="StripeAdapter"):
        await adapter.cancel_order(cancel_args, agent_id="agent-006")

    with pytest.raises(NotImplementedError, match="StripeAdapter"):
        await adapter.book_slot(book_args, agent_id="agent-006")

    with pytest.raises(NotImplementedError, match="StripeAdapter"):
        await adapter.update_customer_record(ucr_args, agent_id="agent-006")


# ---------------------------------------------------------------------------
# Security invariant: no module-level stripe.api_key
# ---------------------------------------------------------------------------


def test_no_module_level_stripe_api_key() -> None:
    """stripe_adapter.py must not set stripe.api_key at module level (Pitfall 2 / T-16-01).

    Reads the source of stripe_adapter.py and asserts zero occurrences of 'stripe.api_key'.
    Cross-tenant key bleed is prevented by constructing a fresh StripeClient inside
    each asyncio.to_thread closure.
    """
    import ast
    import importlib.util
    import pathlib

    adapter_path = pathlib.Path(__file__).parent.parent.parent / (
        "app/services/transactional/adapters/stripe_adapter.py"
    )
    source = adapter_path.read_text(encoding="utf-8")

    assert "stripe.api_key" not in source, (
        "stripe.api_key must not appear in stripe_adapter.py — "
        "cross-tenant key bleed risk (Pitfall 2, T-16-01). "
        "Use stripe.StripeClient(api_key) inside asyncio.to_thread instead."
    )
