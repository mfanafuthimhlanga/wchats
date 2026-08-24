"""
domain.transactional_schemas. Typed Pydantic v2 input/output models for all 7 transactional tools.

14 models total:
  Mutating (6 tools × 2 = 12):
    PlaceOrderInput / PlaceOrderOutput
    CancelOrderInput / CancelOrderOutput
    IssueRefundInput / IssueRefundOutput
    UpdateSubscriptionInput / UpdateSubscriptionOutput
    BookSlotInput / BookSlotOutput
    UpdateCustomerRecordInput / UpdateCustomerRecordOutput

  Non-mutating (1 tool × 2 = 2):
    ConfirmActionInput / ConfirmActionOutput

Security contract (T-14-02-01):
  - No field may be a free-form blob, SQL string, URL, or open dict.
  - Every field is a typed scalar (str/int/bool) or a typed enum.
  - amount_cents / refund_amount_cents are Annotated[int, Field(ge=0)] so the
    Plan-03 max_amount_cents blast-radius constraint has a typed field to compare.
  - idempotency_key is required on all 6 mutating Input models (never on Output
    models or ConfirmActionInput, which is non-mutating).

Schema contract:
  - model_json_schema() produces {"type": "object", "properties": {...}} for every
    model, which the SDK's _build_schema pass-through and the A2A v1.2 serializer
    can consume directly.
  - Every field carries Field(description=...) so the schema is self-describing for
    both the SDK input_schema and the A2A manifest.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 1. place_order
# ---------------------------------------------------------------------------


class PlaceOrderInput(BaseModel):
    """Input for the place_order transactional tool."""

    idempotency_key: Annotated[
        str,
        Field(description="Client-generated UUID for replay protection. Scoped to (agent_id, skill)."),
    ]
    product_id: Annotated[
        str,
        Field(description="SKU or platform product identifier."),
    ]
    quantity: Annotated[
        int,
        Field(ge=1, description="Number of units to order."),
    ]
    customer_email: Annotated[
        str,
        Field(description="Customer email address for order confirmation."),
    ]
    shipping_address: Annotated[
        str,
        Field(description="Full shipping address for delivery."),
    ]
    amount_cents: Annotated[
        int,
        Field(ge=0, description="Expected order total in cents. Used by the Plan-03 max_amount_cents constraint."),
    ]


class PlaceOrderOutput(BaseModel):
    """Output for the place_order transactional tool."""

    order_id: Annotated[
        str,
        Field(description="Platform-assigned order identifier."),
    ]
    status: Annotated[
        str,
        Field(description="Order status: 'placed' | 'pending_confirmation' | 'error'."),
    ]
    message: Annotated[
        str,
        Field(description="Human-readable result for the agent to convey to the customer."),
    ]


# ---------------------------------------------------------------------------
# 2. cancel_order
# ---------------------------------------------------------------------------


class CancelOrderInput(BaseModel):
    """Input for the cancel_order transactional tool."""

    idempotency_key: Annotated[
        str,
        Field(description="Client-generated UUID for replay protection. Scoped to (agent_id, skill)."),
    ]
    order_id: Annotated[
        str,
        Field(description="Platform-assigned order identifier to cancel."),
    ]
    reason: Annotated[
        str,
        Field(description="Reason for cancellation (shown in order history)."),
    ]


class CancelOrderOutput(BaseModel):
    """Output for the cancel_order transactional tool."""

    order_id: Annotated[
        str,
        Field(description="Identifier of the cancelled order."),
    ]
    status: Annotated[
        str,
        Field(description="Cancellation status: 'cancelled' | 'pending_cancellation' | 'error'."),
    ]
    message: Annotated[
        str,
        Field(description="Human-readable result for the agent to convey to the customer."),
    ]


# ---------------------------------------------------------------------------
# 3. issue_refund
# ---------------------------------------------------------------------------


class IssueRefundInput(BaseModel):
    """Input for the issue_refund transactional tool."""

    idempotency_key: Annotated[
        str,
        Field(description="Client-generated UUID for replay protection. Scoped to (agent_id, skill)."),
    ]
    order_id: Annotated[
        str,
        Field(description="Platform-assigned order identifier to refund."),
    ]
    refund_amount_cents: Annotated[
        int,
        Field(ge=0, description="Refund amount in cents. Used by the Plan-03 max_amount_cents constraint."),
    ]
    reason: Annotated[
        str,
        Field(description="Reason for the refund (shown in order history and to payment provider)."),
    ]


class IssueRefundOutput(BaseModel):
    """Output for the issue_refund transactional tool."""

    refund_id: Annotated[
        str,
        Field(description="Platform-assigned refund identifier."),
    ]
    status: Annotated[
        str,
        Field(description="Refund status: 'refunded' | 'pending_refund' | 'error'."),
    ]
    message: Annotated[
        str,
        Field(description="Human-readable result for the agent to convey to the customer."),
    ]


# ---------------------------------------------------------------------------
# 4. update_subscription
# ---------------------------------------------------------------------------


class UpdateSubscriptionInput(BaseModel):
    """Input for the update_subscription transactional tool."""

    idempotency_key: Annotated[
        str,
        Field(description="Client-generated UUID for replay protection. Scoped to (agent_id, skill)."),
    ]
    subscription_id: Annotated[
        str,
        Field(description="Platform-assigned subscription identifier."),
    ]
    new_plan: Annotated[
        str,
        Field(description="Target subscription plan identifier (e.g. 'basic', 'pro', 'enterprise')."),
    ]
    effective_date: Annotated[
        str,
        Field(description="ISO 8601 date (YYYY-MM-DD) when the plan change takes effect."),
    ]


class UpdateSubscriptionOutput(BaseModel):
    """Output for the update_subscription transactional tool."""

    subscription_id: Annotated[
        str,
        Field(description="Identifier of the updated subscription."),
    ]
    status: Annotated[
        str,
        Field(description="Update status: 'updated' | 'pending_update' | 'error'."),
    ]
    message: Annotated[
        str,
        Field(description="Human-readable result for the agent to convey to the customer."),
    ]


# ---------------------------------------------------------------------------
# 5. book_slot
# ---------------------------------------------------------------------------


class BookSlotInput(BaseModel):
    """Input for the book_slot transactional tool."""

    idempotency_key: Annotated[
        str,
        Field(description="Client-generated UUID for replay protection. Scoped to (agent_id, skill)."),
    ]
    service_type: Annotated[
        str,
        Field(description="Type of service to book (e.g. 'consultation', 'delivery', 'installation')."),
    ]
    preferred_date: Annotated[
        str,
        Field(description="Preferred booking date in ISO 8601 format (YYYY-MM-DD)."),
    ]
    preferred_time: Annotated[
        str,
        Field(description="Preferred booking time in HH:MM (24-hour) format."),
    ]
    customer_name: Annotated[
        str,
        Field(description="Full name of the customer for the booking."),
    ]


class BookSlotOutput(BaseModel):
    """Output for the book_slot transactional tool."""

    booking_id: Annotated[
        str,
        Field(description="Platform-assigned booking identifier."),
    ]
    status: Annotated[
        str,
        Field(description="Booking status: 'confirmed' | 'pending_confirmation' | 'error'."),
    ]
    message: Annotated[
        str,
        Field(description="Human-readable result for the agent to convey to the customer."),
    ]


# ---------------------------------------------------------------------------
# 6. update_customer_record
# ---------------------------------------------------------------------------


class UpdateCustomerRecordInput(BaseModel):
    """Input for the update_customer_record transactional tool."""

    idempotency_key: Annotated[
        str,
        Field(description="Client-generated UUID for replay protection. Scoped to (agent_id, skill)."),
    ]
    field_name: Annotated[
        str,
        Field(
            description=(
                "Name of the customer record field to update. "
                "Allowed values: 'email', 'phone', 'address', 'name'."
            )
        ),
    ]
    new_value: Annotated[
        str,
        Field(description="New value for the specified field."),
    ]


class UpdateCustomerRecordOutput(BaseModel):
    """Output for the update_customer_record transactional tool."""

    record_id: Annotated[
        str,
        Field(description="Platform-assigned customer record identifier."),
    ]
    status: Annotated[
        str,
        Field(description="Update status: 'updated' | 'pending_update' | 'error'."),
    ]
    message: Annotated[
        str,
        Field(description="Human-readable result for the agent to convey to the customer."),
    ]


# ---------------------------------------------------------------------------
# 7. confirm_action (non-mutating — no idempotency_key)
# ---------------------------------------------------------------------------


class ConfirmActionInput(BaseModel):
    """Input for the confirm_action tool (non-mutating — no idempotency_key).

    confirm_action is called by the agent to create a pending_confirmations row
    when a mutating tool requires human approval before execution.  It does NOT
    directly execute a provider action, hence mutating=False.
    """

    skill: Annotated[
        str,
        Field(description="Canonical skill name of the action being confirmed (e.g. 'place_order')."),
    ]
    action_reference: Annotated[
        str,
        Field(
            description=(
                "Typed reference to the pending action — typically the idempotency_key of "
                "the associated mutating tool call that is awaiting confirmation."
            )
        ),
    ]


class ConfirmActionOutput(BaseModel):
    """Output for the confirm_action tool."""

    confirmation_id: Annotated[
        str,
        Field(description="Platform-assigned pending_confirmations row identifier."),
    ]
    status: Annotated[
        str,
        Field(description="Confirmation status: 'pending' | 'already_pending' | 'error'."),
    ]
    message: Annotated[
        str,
        Field(description="Human-readable result for the agent to convey to the customer."),
    ]


# ---------------------------------------------------------------------------
# SKILL_INPUT_MODELS — skill name -> Input model, for the six mutating skills
# ---------------------------------------------------------------------------
#
# Definition-time mapping, written by hand — never derived from a tool name at
# runtime, matching TOOL_REGISTRY's own stated rule (registry.py docstring:
# "never runtime-inferred from the tool name or arguments", T-14-02-02).
#
# confirm_action is deliberately absent. It has no adapter method and no
# idempotency_key (mutating=False) — including it here would make an
# unexecutable skill look executable to a resolver that reads this mapping to
# decide what it may re-validate and dispatch (ACT-07's confirmation_resolution.py).
#
# Its key set is asserted equal to the registry's mutating set by a unit test
# (test_confirmation_resolution.py), so adding a seventh mutating skill without
# adding an entry here is a red test, not a silent gap.
SKILL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "place_order": PlaceOrderInput,
    "cancel_order": CancelOrderInput,
    "issue_refund": IssueRefundInput,
    "update_subscription": UpdateSubscriptionInput,
    "book_slot": BookSlotInput,
    "update_customer_record": UpdateCustomerRecordInput,
}
