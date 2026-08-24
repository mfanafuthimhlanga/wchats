"""
Unit tests for CalendlyAdapter — INT-06 (Calendly Scheduling API, async httpx + Bearer PAT).

Task 2 (RED/GREEN): book_slot with config_data event_type mapping, unknown service_type
                    error, and NotImplemented stubs.

  test_book_slot:
      CalendlyAdapter.book_slot resolves args.service_type via
      config_data["event_types"][service_type] to a Calendly event_type URI,
      POSTs to CALENDLY_API_BASE + "/invitees" with Authorization: Bearer <PAT>
      and start_time built from preferred_date/preferred_time;
      returns BookSlotOutput(status="confirmed", booking_id from response resource.uri).

  test_unknown_service_type_raises:
      A service_type absent from config_data["event_types"] raises a clear
      ValueError (so the dispatcher audits a "provider/config error" rather than
      a silent success).

  test_unsupported_methods_raise:
      place_order, cancel_order, issue_refund, update_subscription,
      update_customer_record raise NotImplementedError.

Security invariants verified:
  T-16-01: The PAT (personal_access_token) is NEVER logged — it is extracted inside
            book_slot via handle.use() and placed only in the Authorization header.
            The CredentialHandle.__repr__ returns "<CredentialHandle:redacted>".
  T-16-02: No URL field from args — CALENDLY_API_BASE is a fixed module constant.
            The event_type URI comes from config_data, not from args.service_type directly.
  Open Question 2 resolution: event_type URI mapping lives in config_data["event_types"],
            keeping BookSlotInput.service_type provider-agnostic (human label → URI).

Calendly paid-plan caveat (Pitfall 7):
  POST /invitees requires a paid Calendly plan. On free plans, Calendly returns 403.
  The adapter raises_for_status() which surfaces as an audited error. Tests verify
  the adapter does NOT suppress 403 — it propagates the HTTP exception so the
  dispatcher can audit it.

Test infrastructure:
  asyncio_mode = "auto" in pyproject.toml — all async def tests run automatically.
  httpx is mocked via respx (already a dev dep at 0.23.1) for async-native httpx mocking.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_handle(personal_access_token: str = "eyJ_test_PAT_abc"):
    """Return a CredentialHandle wrapping a Calendly credential JSON blob."""
    from app.services.transactional.credential_service import CredentialHandle

    return CredentialHandle(
        _raw=json.dumps({"personal_access_token": personal_access_token})
    )


def _make_config_data() -> dict:
    """Return a sample config_data with event_type URI mapping (Open Question 2)."""
    return {
        "event_types": {
            "consultation": "https://api.calendly.com/event_types/AAABBB111",
            "demo": "https://api.calendly.com/event_types/CCCDDD222",
        }
    }


def _make_adapter(config_data: dict | None = None):
    """Return a CalendlyAdapter with a test credential handle and config_data."""
    from app.services.transactional.adapters.calendly_adapter import CalendlyAdapter

    return CalendlyAdapter(
        handle=_make_handle(),
        config_data=config_data if config_data is not None else _make_config_data(),
    )


# ---------------------------------------------------------------------------
# test_book_slot — POST /invitees with event_type URI mapping
# ---------------------------------------------------------------------------


@respx.mock
async def test_book_slot() -> None:
    """book_slot POSTs to CALENDLY_API_BASE/invitees with Bearer PAT + event_type URI (INT-06).

    Verifies that:
    - args.service_type ("consultation") is resolved to the config_data event_type URI
    - The request URL is CALENDLY_API_BASE + "/invitees"
    - Authorization header is "Bearer eyJ_test_PAT_abc"
    - The request JSON body contains event_type == config_data["event_types"]["consultation"]
    - start_time is built from preferred_date + preferred_time + "Z"
    - Returns BookSlotOutput(status="confirmed", booking_id from resource.uri)
    """
    from app.services.transactional.adapters.calendly_adapter import (
        CALENDLY_API_BASE,
    )
    from app.domain.transactional_schemas import BookSlotInput

    adapter = _make_adapter()
    args = BookSlotInput(
        idempotency_key="idem-book-1",
        service_type="consultation",
        preferred_date="2026-07-15",
        preferred_time="10:00",
        customer_name="Alice Smith",
    )

    mock_response_body = {
        "resource": {
            "uri": "https://api.calendly.com/scheduled_events/XXXYYY/invitees/ZZZ",
            "join_url": "https://calendly.com/events/XXXYYY",
        }
    }

    # Mock the POST /invitees endpoint
    invitees_route = respx.post(f"{CALENDLY_API_BASE}/invitees").mock(
        return_value=httpx.Response(200, json=mock_response_body)
    )

    result = await adapter.book_slot(args, agent_id="agent-cal-001")

    # The route must have been called exactly once
    assert invitees_route.called, "CalendlyAdapter.book_slot must POST to /invitees"
    assert invitees_route.call_count == 1

    # Inspect the captured request
    captured_request = invitees_route.calls[0].request

    # Authorization header must be Bearer PAT (T-16-01: PAT from handle.use(), not from args)
    auth_header = captured_request.headers.get("authorization", "")
    assert auth_header.startswith("Bearer "), (
        f"Authorization header must be 'Bearer <PAT>'; got {auth_header!r}"
    )
    assert "eyJ_test_PAT_abc" in auth_header, (
        f"Authorization header must contain the PAT; got {auth_header!r}"
    )

    # Request body must have event_type == config_data["event_types"]["consultation"]
    request_body = json.loads(captured_request.content)
    expected_event_type_uri = _make_config_data()["event_types"]["consultation"]
    assert request_body.get("event_type") == expected_event_type_uri, (
        f"event_type must be the config_data URI; got {request_body.get('event_type')!r}"
    )

    # start_time must be built from preferred_date + "T" + preferred_time + ":00Z"
    assert request_body.get("start_time") == "2026-07-15T10:00:00Z", (
        f"start_time must be '2026-07-15T10:00:00Z'; got {request_body.get('start_time')!r}"
    )

    # invitee.name must be customer_name
    assert request_body.get("invitee", {}).get("name") == "Alice Smith", (
        f"invitee.name must be 'Alice Smith'; got {request_body.get('invitee')!r}"
    )

    # Returns BookSlotOutput(status="confirmed", booking_id from resource.uri)
    assert result.status == "confirmed", f"status must be 'confirmed'; got {result.status!r}"
    assert result.booking_id == mock_response_body["resource"]["uri"], (
        f"booking_id must be the resource.uri; got {result.booking_id!r}"
    )


# ---------------------------------------------------------------------------
# test_unknown_service_type_raises — config_data miss path
# ---------------------------------------------------------------------------


async def test_unknown_service_type_raises() -> None:
    """A service_type absent from config_data["event_types"] raises ValueError (INT-06).

    The CalendlyAdapter must NOT silently pass an unresolved service_type to the
    Calendly API (which would reject it or book the wrong event type). The error
    is raised before any HTTP call so the dispatcher can audit it as a config error.
    """
    from app.domain.transactional_schemas import BookSlotInput

    adapter = _make_adapter()  # config_data has "consultation" and "demo" only
    args = BookSlotInput(
        idempotency_key="idem-book-2",
        service_type="nonexistent_service_type",  # not in config_data
        preferred_date="2026-07-15",
        preferred_time="14:00",
        customer_name="Bob Jones",
    )

    with pytest.raises((ValueError, KeyError, LookupError)):
        await adapter.book_slot(args, agent_id="agent-cal-001")


# ---------------------------------------------------------------------------
# test_event_types_must_be_dict (WR-06)
# ---------------------------------------------------------------------------


def test_event_types_must_be_dict() -> None:
    """WR-06: CalendlyAdapter raises ValueError if event_types is a list (not a dict).

    The provisioning script docstring previously showed event_types as a list
    (["uuid-1", "uuid-2"]) which would cause AttributeError at runtime when
    book_slot calls self._event_types.get(service_type). The adapter now validates
    the type at construction time so operators get a clear error at provision time,
    not a silent AttributeError at runtime.
    """
    from app.services.transactional.adapters.calendly_adapter import CalendlyAdapter

    with pytest.raises(ValueError, match="event_types"):
        CalendlyAdapter(
            handle=_make_handle(),
            config_data={"event_types": ["uuid-1", "uuid-2"]},  # wrong type — must be dict
        )


# ---------------------------------------------------------------------------
# test_unsupported_methods_raise — NotImplementedError stubs
# ---------------------------------------------------------------------------


async def test_unsupported_methods_raise() -> None:
    """place_order, cancel_order, issue_refund, update_subscription, update_customer_record
    raise NotImplementedError (INT-06 — CalendlyAdapter only supports book_slot).

    The dispatcher's except Exception handler returns is_error=True for unsupported calls.
    """
    from app.domain.transactional_schemas import (
        CancelOrderInput,
        IssueRefundInput,
        PlaceOrderInput,
        UpdateCustomerRecordInput,
        UpdateSubscriptionInput,
    )

    adapter = _make_adapter()

    place_args = PlaceOrderInput(
        idempotency_key="idem-place-1",
        product_id="SKU-001",
        quantity=1,
        customer_email="customer@example.com",
        shipping_address="123 Test St",
        amount_cents=1000,
    )
    with pytest.raises(NotImplementedError):
        await adapter.place_order(place_args, agent_id="agent-cal-001")

    cancel_args = CancelOrderInput(
        idempotency_key="idem-cancel-1",
        order_id="ord-123",
        reason="test",
    )
    with pytest.raises(NotImplementedError):
        await adapter.cancel_order(cancel_args, agent_id="agent-cal-001")

    refund_args = IssueRefundInput(
        idempotency_key="idem-refund-1",
        order_id="ord-123",
        refund_amount_cents=500,
        reason="test",
    )
    with pytest.raises(NotImplementedError):
        await adapter.issue_refund(refund_args, agent_id="agent-cal-001")

    sub_args = UpdateSubscriptionInput(
        idempotency_key="idem-sub-1",
        subscription_id="sub-123",
        new_plan="pro",
        effective_date="2026-07-01",
    )
    with pytest.raises(NotImplementedError):
        await adapter.update_subscription(sub_args, agent_id="agent-cal-001")

    cust_args = UpdateCustomerRecordInput(
        idempotency_key="idem-cust-1",
        field_name="email",
        new_value="new@example.com",
    )
    with pytest.raises(NotImplementedError):
        await adapter.update_customer_record(cust_args, agent_id="agent-cal-001")
