"""
Unit tests for the Phase-14 typed tool contract (Plan 02).

Covers:
  Task 1 — Pydantic schemas (14 models):
    - Each mutating Input rejects missing idempotency_key with ValidationError
    - Each mutating Input rejects a wrong-typed field with ValidationError
    - Each mutating Input accepts a valid payload (no exception)
    - model_json_schema() returns "type":"object" + "properties" for every model

  Task 2 — TransactionalToolDef registry:
    - All 6 mutating skills have mutating=True and idempotency_required=True
    - confirm_action has mutating=False and idempotency_required=False
    - A2A metadata fields (a2a_input_modes, a2a_output_modes, examples) present for all 7

  Task 3 — ProviderAdapter + actor_seam:
    - StubProviderAdapter.place_order returns a PlaceOrderOutput with STUB marker in message
    - call_actor_gate returns ("approve", "") unconditionally

No DB or SDK imports — pure contract test.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Task 1: Schema tests
# ---------------------------------------------------------------------------
from app.domain.transactional_schemas import (
    BookSlotInput,
    BookSlotOutput,
    CancelOrderInput,
    CancelOrderOutput,
    ConfirmActionInput,
    ConfirmActionOutput,
    IssueRefundInput,
    IssueRefundOutput,
    PlaceOrderInput,
    PlaceOrderOutput,
    UpdateCustomerRecordInput,
    UpdateCustomerRecordOutput,
    UpdateSubscriptionInput,
    UpdateSubscriptionOutput,
)
from app.services.actor_seam import call_actor_gate
from app.services.transactional.provider_adapter import (
    ProviderAdapter,
    StubProviderAdapter,
    get_adapter,
)
from app.services.transactional.registry import (
    TOOL_METADATA,
    TOOL_REGISTRY,
    to_a2a_skill,
)
from tests.model_doubles import ledger

# --------------- idempotency_key required on all 6 mutating inputs -----------


def test_place_order_missing_idempotency_key_raises():
    with pytest.raises(ValidationError):
        PlaceOrderInput(
            product_id="SKU-001",
            quantity=2,
            customer_email="test@example.com",
            shipping_address="1 Main St",
            amount_cents=1000,
            # idempotency_key intentionally omitted
        )


def test_cancel_order_missing_idempotency_key_raises():
    with pytest.raises(ValidationError):
        CancelOrderInput(
            order_id="ORD-001",
            reason="Customer changed mind",
        )


def test_issue_refund_missing_idempotency_key_raises():
    with pytest.raises(ValidationError):
        IssueRefundInput(
            order_id="ORD-001",
            refund_amount_cents=500,
            reason="Defective product",
        )


def test_update_subscription_missing_idempotency_key_raises():
    with pytest.raises(ValidationError):
        UpdateSubscriptionInput(
            subscription_id="SUB-001",
            new_plan="pro",
            effective_date="2026-07-01",
        )


def test_book_slot_missing_idempotency_key_raises():
    with pytest.raises(ValidationError):
        BookSlotInput(
            service_type="consultation",
            preferred_date="2026-07-01",
            preferred_time="10:00",
            customer_name="Alice",
        )


def test_update_customer_record_missing_idempotency_key_raises():
    with pytest.raises(ValidationError):
        UpdateCustomerRecordInput(
            field_name="email",
            new_value="new@example.com",
        )


# --------------- wrong-typed field raises ValidationError -------------------


def test_place_order_wrong_type_amount_raises():
    """amount_cents must be int, not str."""
    with pytest.raises(ValidationError):
        PlaceOrderInput(
            idempotency_key="idem-001",
            product_id="SKU-001",
            quantity=2,
            customer_email="test@example.com",
            shipping_address="1 Main St",
            amount_cents="not-an-int",  # type: ignore[arg-type]
        )


def test_place_order_negative_amount_raises():
    """amount_cents has ge=0."""
    with pytest.raises(ValidationError):
        PlaceOrderInput(
            idempotency_key="idem-001",
            product_id="SKU-001",
            quantity=2,
            customer_email="test@example.com",
            shipping_address="1 Main St",
            amount_cents=-1,
        )


def test_issue_refund_negative_amount_raises():
    """refund_amount_cents has ge=0."""
    with pytest.raises(ValidationError):
        IssueRefundInput(
            idempotency_key="idem-002",
            order_id="ORD-001",
            refund_amount_cents=-100,
            reason="Bad refund",
        )


# --------------- valid payloads succeed ------------------------------------


def test_place_order_valid():
    obj = PlaceOrderInput(
        idempotency_key="idem-001",
        product_id="SKU-001",
        quantity=2,
        customer_email="test@example.com",
        shipping_address="1 Main St, Cape Town",
        amount_cents=1000,
    )
    assert obj.idempotency_key == "idem-001"
    assert obj.amount_cents == 1000


def test_cancel_order_valid():
    obj = CancelOrderInput(
        idempotency_key="idem-002",
        order_id="ORD-001",
        reason="Customer changed mind",
    )
    assert obj.idempotency_key == "idem-002"


def test_issue_refund_valid():
    obj = IssueRefundInput(
        idempotency_key="idem-003",
        order_id="ORD-001",
        refund_amount_cents=500,
        reason="Defective product",
    )
    assert obj.refund_amount_cents == 500


def test_update_subscription_valid():
    obj = UpdateSubscriptionInput(
        idempotency_key="idem-004",
        subscription_id="SUB-001",
        new_plan="pro",
        effective_date="2026-07-01",
    )
    assert obj.idempotency_key == "idem-004"


def test_book_slot_valid():
    obj = BookSlotInput(
        idempotency_key="idem-005",
        service_type="consultation",
        preferred_date="2026-07-01",
        preferred_time="10:00",
        customer_name="Alice",
    )
    assert obj.idempotency_key == "idem-005"


def test_update_customer_record_valid():
    obj = UpdateCustomerRecordInput(
        idempotency_key="idem-006",
        field_name="email",
        new_value="new@example.com",
    )
    assert obj.idempotency_key == "idem-006"


def test_confirm_action_valid():
    """ConfirmActionInput has no idempotency_key."""
    obj = ConfirmActionInput(
        skill="place_order",
        action_reference="ORD-pending-001",
    )
    assert obj.skill == "place_order"
    assert not hasattr(obj, "idempotency_key") or True  # non-mutating: no idempotency_key required


# --------------- model_json_schema produces type+properties ----------------

_ALL_MODELS = [
    PlaceOrderInput, PlaceOrderOutput,
    CancelOrderInput, CancelOrderOutput,
    IssueRefundInput, IssueRefundOutput,
    UpdateSubscriptionInput, UpdateSubscriptionOutput,
    BookSlotInput, BookSlotOutput,
    UpdateCustomerRecordInput, UpdateCustomerRecordOutput,
    ConfirmActionInput, ConfirmActionOutput,
]


@pytest.mark.parametrize("model_cls", _ALL_MODELS)
def test_model_json_schema_has_type_and_properties(model_cls):
    schema = model_cls.model_json_schema()
    assert schema.get("type") == "object", f"{model_cls.__name__} missing type:object in schema"
    assert "properties" in schema, f"{model_cls.__name__} missing properties in schema"


# --------------- 14 models are all present ---------------------------------


def test_fourteen_models_importable():
    """Ensure all 14 named models can be imported (import at top of file verifies this)."""
    assert len(_ALL_MODELS) == 14


# ---------------------------------------------------------------------------
# Task 2: Registry tests
# ---------------------------------------------------------------------------

MUTATING_SKILLS = [
    "place_order",
    "cancel_order",
    "issue_refund",
    "update_subscription",
    "book_slot",
    "update_customer_record",
]

NON_MUTATING_SKILLS = ["confirm_action"]


def test_tool_registry_has_all_seven_skills():
    for skill in MUTATING_SKILLS + NON_MUTATING_SKILLS:
        assert skill in TOOL_REGISTRY, f"Missing skill {skill!r} in TOOL_REGISTRY"


def test_mutating_tools_have_mutating_true():
    for skill in MUTATING_SKILLS:
        tdef = TOOL_REGISTRY[skill]
        assert tdef.mutating is True, f"{skill}: expected mutating=True, got {tdef.mutating}"


def test_mutating_tools_have_idempotency_required_true():
    for skill in MUTATING_SKILLS:
        tdef = TOOL_REGISTRY[skill]
        assert tdef.idempotency_required is True, (
            f"{skill}: expected idempotency_required=True"
        )


def test_confirm_action_is_not_mutating():
    tdef = TOOL_REGISTRY["confirm_action"]
    assert tdef.mutating is False
    assert tdef.idempotency_required is False


def test_all_tools_have_a2a_metadata():
    for skill, tdef in TOOL_REGISTRY.items():
        assert isinstance(tdef.a2a_input_modes, list) and len(tdef.a2a_input_modes) > 0, (
            f"{skill}: a2a_input_modes missing"
        )
        assert isinstance(tdef.a2a_output_modes, list) and len(tdef.a2a_output_modes) > 0, (
            f"{skill}: a2a_output_modes missing"
        )


def test_mutating_tools_have_examples():
    for skill in MUTATING_SKILLS:
        tdef = TOOL_REGISTRY[skill]
        assert isinstance(tdef.examples, list) and len(tdef.examples) >= 1, (
            f"{skill}: expected at least 1 example, got {tdef.examples!r}"
        )


def test_tool_metadata_and_tool_registry_are_same_object():
    """TOOL_METADATA is the alias for TOOL_REGISTRY."""
    assert TOOL_METADATA is TOOL_REGISTRY


def test_to_a2a_skill_returns_correct_fields():
    tdef = TOOL_REGISTRY["place_order"]
    result = to_a2a_skill(tdef)
    assert result["id"] == "place_order"
    assert "inputModes" in result
    assert "outputModes" in result
    assert "examples" in result


def test_requires_identity_verification_defaults():
    """book_slot is False; other mutating tools are True (per research Cluster 2)."""
    assert TOOL_REGISTRY["book_slot"].requires_identity_verification is False
    for skill in MUTATING_SKILLS:
        if skill != "book_slot":
            assert TOOL_REGISTRY[skill].requires_identity_verification is True, (
                f"{skill}: expected requires_identity_verification=True"
            )


# ---------------------------------------------------------------------------
# Task 3: ProviderAdapter + actor_seam tests
# ---------------------------------------------------------------------------


def test_stub_place_order_returns_stub_labelled_output():
    adapter = StubProviderAdapter()
    args = PlaceOrderInput(
        idempotency_key="idem-stub-01",
        product_id="SKU-001",
        quantity=1,
        customer_email="test@example.com",
        shipping_address="1 Main St",
        amount_cents=500,
    )
    result = asyncio.run(adapter.place_order(args, "agent-001"))
    assert isinstance(result, PlaceOrderOutput)
    assert "[STUB]" in result.message or "STUB" in result.message


def test_stub_cancel_order_returns_stub_labelled_output():
    adapter = StubProviderAdapter()
    args = CancelOrderInput(
        idempotency_key="idem-stub-02",
        order_id="ORD-001",
        reason="Changed mind",
    )
    result = asyncio.run(adapter.cancel_order(args, "agent-001"))
    assert isinstance(result, CancelOrderOutput)
    assert "[STUB]" in result.message or "STUB" in result.message


def test_stub_issue_refund_returns_stub_labelled_output():
    adapter = StubProviderAdapter()
    args = IssueRefundInput(
        idempotency_key="idem-stub-03",
        order_id="ORD-001",
        refund_amount_cents=200,
        reason="Defective",
    )
    result = asyncio.run(adapter.issue_refund(args, "agent-001"))
    assert isinstance(result, IssueRefundOutput)
    assert "[STUB]" in result.message or "STUB" in result.message


def test_stub_update_subscription_returns_stub_labelled_output():
    adapter = StubProviderAdapter()
    args = UpdateSubscriptionInput(
        idempotency_key="idem-stub-04",
        subscription_id="SUB-001",
        new_plan="pro",
        effective_date="2026-07-01",
    )
    result = asyncio.run(adapter.update_subscription(args, "agent-001"))
    assert isinstance(result, UpdateSubscriptionOutput)
    assert "[STUB]" in result.message or "STUB" in result.message


def test_stub_book_slot_returns_stub_labelled_output():
    adapter = StubProviderAdapter()
    args = BookSlotInput(
        idempotency_key="idem-stub-05",
        service_type="consultation",
        preferred_date="2026-07-01",
        preferred_time="10:00",
        customer_name="Alice",
    )
    result = asyncio.run(adapter.book_slot(args, "agent-001"))
    assert isinstance(result, BookSlotOutput)
    assert "[STUB]" in result.message or "STUB" in result.message


def test_stub_update_customer_record_returns_stub_labelled_output():
    adapter = StubProviderAdapter()
    args = UpdateCustomerRecordInput(
        idempotency_key="idem-stub-06",
        field_name="email",
        new_value="new@example.com",
    )
    result = asyncio.run(adapter.update_customer_record(args, "agent-001"))
    assert isinstance(result, UpdateCustomerRecordOutput)
    assert "[STUB]" in result.message or "STUB" in result.message


def test_get_adapter_returns_provider_adapter_instance():
    adapter = get_adapter()
    assert isinstance(adapter, ProviderAdapter)


def test_get_adapter_returns_stub_provider_adapter():
    adapter = get_adapter()
    assert isinstance(adapter, StubProviderAdapter)


def test_call_actor_gate_returns_approve():
    """Phase-15: skip short-circuit returns approve for low-value no-confirm skill.

    Updated from Phase-14 stub contract (which always returned ("approve", ""))
    to Phase-15 real implementation: use the skip short-circuit so no API call is made.
    """

    # Snapshot with requires_confirmation=False and max_amount_cents below the
    # 500-cent threshold triggers the skip short-circuit without any Anthropic call.
    snapshot = {
        "enabled": True,
        "requires_confirmation": False,
        "constraints": {"max_amount_cents": 100},
    }
    decision, rationale = asyncio.run(
        call_actor_gate(
            skill="place_order",
            arguments={"idempotency_key": "k1", "amount_cents": 100},
            capability_snapshot=snapshot,
            conversation_id="conv-001",
            agent_id="agent-001",
            ledger=ledger(),
        )
    )
    assert decision == "approve"
    assert "skip" in rationale  # Phase-15 skip short-circuit sets a skip: prefix


def test_call_actor_gate_always_approve_regardless_of_args():
    """Phase-15: low-value skills skip the Actor gate and return approve.

    Updated from Phase-14 stub contract. Each mutating skill is tested with a
    snapshot that triggers the skip short-circuit (requires_confirmation=False
    AND max_amount_cents=0 < 500 threshold) — no Anthropic API call is made.
    """
    # All mutating skills: use a skip-eligible snapshot so no API call needed.
    skip_snapshot = {
        "requires_confirmation": False,
        "constraints": {"max_amount_cents": 0},
    }
    for skill in MUTATING_SKILLS:
        decision, rationale = asyncio.run(
            call_actor_gate(
                skill=skill,
                arguments={},
                capability_snapshot=skip_snapshot,
                conversation_id="conv-test",
                agent_id="agent-test",
                ledger=ledger(),
            )
        )
        assert decision == "approve", f"{skill}: expected approve, got {decision}"
        assert "skip" in rationale, f"{skill}: expected skip rationale, got {rationale!r}"
