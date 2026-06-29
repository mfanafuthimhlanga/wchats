"""
transactional.tools — 6 mutating @tool handlers + confirm_action_tool + shared dispatcher.

The single _execute_transactional_tool dispatcher encodes the enforcement order ONCE:
  1. Capability check (fail-closed — any denial is final, no adapter call)
  2. Idempotency lookup (replay short-circuit BEFORE actor seam — replay optimization:
     avoids a redundant Haiku call on replays that execute nothing)
  3. Actor seam (call_actor_gate): block path → audit row + is_error, NO adapter call
  4. Adapter execute (in try/except, capturing latency_ms and error)
  5. Audit row written ALWAYS (on both success and error paths — AUD-01 coverage)
  6. Store idempotency key (on success only)

AUD-01 symmetry (PLAN.md Task 1 note):
  The capability denial path also writes a tool_calls_audit row (error="capability.denial:<reason>").
  This ensures 100% audit coverage — every entry into a transactional tool produces one row,
  regardless of where it fails. This mirrors the actor_block path which already wrote audit rows.

confirm_action_tool (mutating=False):
  Writes a pending_confirmations row (agent_id, skill, arguments, requested_at, expires_at)
  and returns an "awaiting confirmation" response. Calls NO provider adapter and takes NO
  idempotency key. Duplicate-confirm dedup is deferred to Phase 18. PRD DDL unchanged.

Circular import note:
  tools.py imports _agent_id_var / _conversation_id_var from agent_tools via a lazy
  import inside _execute_transactional_tool (function body, not module level). This
  breaks the circular dependency that would occur if agent_tools.py imported tools.py
  at module level while tools.py imported agent_tools.py at module level.

Registry attachment:
  After all 7 handlers are defined, each decorated SdkMcpTool is attached to its
  TOOL_REGISTRY entry's sdk_tool field so the registry is the single source linking
  metadata <-> SdkMcpTool (Plan-02 sdk_tool field populated by Plan-04 per registry.py).

Security:
  - The Actor seam (call_actor_gate) is ALWAYS called before the adapter on every
    fresh (non-replay) mutating execution — T-14-04-01.
  - Replays short-circuit BEFORE the actor seam (idempotency hit → return cached
    result, no adapter call, no audit row) — T-14-04-02.
  - allowed_tools listing does not grant access; the fail-closed envelope check in the
    handler is the real gate — T-14-04-03.
  - agent_id is sourced from the per-call ContextVar — T-14-04-04.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import structlog
from pydantic import ValidationError

from claude_agent_sdk import tool

from app.core.database import get_sync_db
from app.models.pending_confirmation import PendingConfirmation
from app.services.actor_seam import call_actor_gate
from app.services.transactional.audit import write_audit_row
from app.services.transactional.enforcement import check_capability_envelope
from app.services.transactional.idempotency import check_idempotency, store_idempotency
from app.services.transactional.provider_adapter import get_adapter
from app.services.transactional.registry import TOOL_REGISTRY
from app.services.transactional.schemas import (
    BookSlotInput,
    CancelOrderInput,
    ConfirmActionInput,
    IssueRefundInput,
    PlaceOrderInput,
    UpdateCustomerRecordInput,
    UpdateSubscriptionInput,
)

log = structlog.get_logger(__name__)

# Default TTL for pending_confirmations rows (Phase 18 will extend/configure this).
_CONFIRM_TTL_HOURS: int = 24


# ---------------------------------------------------------------------------
# Shared dispatcher — encodes the enforcement order ONCE
# ---------------------------------------------------------------------------


async def _execute_transactional_tool(
    skill: str,
    validated,  # Pydantic-validated input model for the specific tool
    raw_args: dict,
    adapter_method: str,
) -> dict:
    """Enforce the locked execution order for every mutating transactional tool call.

    Called by each of the 6 mutating @tool handlers after Pydantic validation.
    confirm_action_tool does NOT use this dispatcher (mutating=False, no adapter).

    Enforcement order (documented in Plan-04 objective — this is the single place
    where the order is encoded; changing it here changes it for ALL 6 mutating tools):

      1. Capability check   — fail-closed; any denial: write audit row, return is_error
      2. Idempotency lookup — cache hit: return stored result (short-circuit before seam)
      3. Actor seam         — "block": write audit row, return is_error (no adapter call)
      4. Adapter execute    — try/except; captures latency_ms and error string
      5. Audit row          — written on BOTH success and error paths (AUD-01)
      6. Store idempotency  — on success only (ON CONFLICT DO NOTHING)

    Args:
        skill:          Canonical tool/skill name (e.g. "place_order").
        validated:      Pydantic-validated input model; provides .idempotency_key.
        raw_args:       Original unvalidated args dict (passed to audit row + actor seam).
        adapter_method: Method name on ProviderAdapter to call (e.g. "place_order").

    Returns:
        SDK-compatible tool response dict with "content" key.
        On errors: also contains "is_error": True.
    """
    # Lazy import breaks the circular dependency between tools.py and agent_tools.py.
    # agent_tools.py imports tools.py for registration; tools.py needs agent_tools.py's
    # ContextVars. At call-time, agent_tools is fully initialised so the lazy import works.
    from app.services.agent_tools import _agent_id_var, _conversation_id_var

    agent_id = _agent_id_var.get()
    conversation_id_str = _conversation_id_var.get()
    # ContextVar default is ""; pass None to audit/actor seam when no conversation context.
    conversation_id: str | None = conversation_id_str if conversation_id_str else None

    # ------------------------------------------------------------------ 1. Capability check
    snapshot, denial = await check_capability_envelope(agent_id, skill, validated)
    if denial is not None:
        # AUD-01 symmetry: capability denial writes an audit row so every tool entry
        # is fully audited — matching the actor_block path which already wrote audit rows.
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot=snapshot,
            latency_ms=None,
            error=f"capability.denial:{denial}",
        )
        # capability.denial structlog event is already emitted by check_capability_envelope.
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Access denied: capability envelope denied this request "
                        f"(reason: {denial}). Contact your administrator to enable this tool."
                    ),
                }
            ],
            "is_error": True,
        }

    # ------------------------------------------------------------------ 2. Idempotency lookup
    # Short-circuit BEFORE the actor seam — replay optimization:
    # a replay executes nothing, so there is no need for a Haiku gate call.
    # The capability check still ran above (fail-closed even for replays — T-14-04-03).
    cached = await check_idempotency(agent_id, skill, validated.idempotency_key)
    if cached is not None:
        log.info(
            "transactional_tool.idempotency_replay",
            agent_id=agent_id,
            skill=skill,
        )
        return cached

    # ------------------------------------------------------------------ 3. Actor seam
    decision, rationale = await call_actor_gate(
        skill, raw_args, snapshot, conversation_id or "", agent_id
    )
    if decision == "block":
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision=decision,
            actor_rationale=rationale,
            capability_snapshot=snapshot,
            latency_ms=None,
            error="actor_block",
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Action blocked by security policy. Please contact support.",
                }
            ],
            "is_error": True,
        }

    # ------------------------------------------------------------------ 4. Adapter execute
    adapter = get_adapter(agent_id)
    start_ms = int(time.time() * 1000)
    error_str: str | None = None
    response: dict | None = None

    try:
        result_obj = await getattr(adapter, adapter_method)(validated, agent_id)
        response = result_obj.model_dump()
        latency_ms = int(time.time() * 1000) - start_ms
    except Exception as exc:  # noqa: BLE001
        latency_ms = int(time.time() * 1000) - start_ms
        error_str = str(exc)
        log.error(
            "transactional_tool.adapter_error",
            agent_id=agent_id,
            skill=skill,
            error=error_str,
        )

    # ------------------------------------------------------------------ 5. Audit row (ALWAYS)
    await write_audit_row(
        agent_id=agent_id,
        conversation_id=conversation_id,
        skill=skill,
        arguments=raw_args,
        result=response,
        actor_decision="",
        actor_rationale="",
        capability_snapshot=snapshot,
        latency_ms=latency_ms,
        error=error_str,
    )

    if error_str is not None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Tool execution failed: {error_str}. Please try again.",
                }
            ],
            "is_error": True,
        }

    # ------------------------------------------------------------------ 6. Store idempotency + return
    tool_response: dict = {
        "content": [{"type": "text", "text": response.get("message", str(response))}]
    }
    await store_idempotency(agent_id, skill, validated.idempotency_key, tool_response)

    log.info(
        "transactional_tool.success",
        agent_id=agent_id,
        skill=skill,
        latency_ms=latency_ms,
    )
    return tool_response


# ---------------------------------------------------------------------------
# 1. place_order
# ---------------------------------------------------------------------------


@tool(
    "place_order",
    (
        "Place a customer order through the tenant's connected store. "
        "Requires an idempotency_key to prevent duplicate orders on retry. "
        "Subject to the capability envelope's max_amount_cents and rate_limit constraints."
    ),
    PlaceOrderInput.model_json_schema(),
)
async def place_order_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=place_order."""
    try:
        validated = PlaceOrderInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool("place_order", validated, args, "place_order")


# ---------------------------------------------------------------------------
# 2. cancel_order
# ---------------------------------------------------------------------------


@tool(
    "cancel_order",
    (
        "Cancel an existing customer order. "
        "Requires an idempotency_key for replay safety. "
        "Subject to the capability envelope's rate_limit constraints."
    ),
    CancelOrderInput.model_json_schema(),
)
async def cancel_order_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=cancel_order."""
    try:
        validated = CancelOrderInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool("cancel_order", validated, args, "cancel_order")


# ---------------------------------------------------------------------------
# 3. issue_refund
# ---------------------------------------------------------------------------


@tool(
    "issue_refund",
    (
        "Issue a refund for a customer order. "
        "Requires an idempotency_key for replay safety. "
        "Subject to the capability envelope's max_amount_cents constraint (refund_amount_cents field)."
    ),
    IssueRefundInput.model_json_schema(),
)
async def issue_refund_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=issue_refund."""
    try:
        validated = IssueRefundInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool("issue_refund", validated, args, "issue_refund")


# ---------------------------------------------------------------------------
# 4. update_subscription
# ---------------------------------------------------------------------------


@tool(
    "update_subscription",
    (
        "Update a customer's subscription plan. "
        "Requires an idempotency_key for replay safety. "
        "Subject to the capability envelope's rate_limit and constraint checks."
    ),
    UpdateSubscriptionInput.model_json_schema(),
)
async def update_subscription_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=update_subscription."""
    try:
        validated = UpdateSubscriptionInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool(
        "update_subscription", validated, args, "update_subscription"
    )


# ---------------------------------------------------------------------------
# 5. book_slot
# ---------------------------------------------------------------------------


@tool(
    "book_slot",
    (
        "Book a time slot for a service (consultation, delivery, installation, etc.). "
        "Requires an idempotency_key for replay safety. "
        "Does not require identity verification (lower-risk booking action)."
    ),
    BookSlotInput.model_json_schema(),
)
async def book_slot_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=book_slot."""
    try:
        validated = BookSlotInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool("book_slot", validated, args, "book_slot")


# ---------------------------------------------------------------------------
# 6. update_customer_record
# ---------------------------------------------------------------------------


@tool(
    "update_customer_record",
    (
        "Update a field on a customer record (email, phone, address, name). "
        "Requires an idempotency_key for replay safety. "
        "Subject to the capability envelope's rate_limit constraints."
    ),
    UpdateCustomerRecordInput.model_json_schema(),
)
async def update_customer_record_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=update_customer_record."""
    try:
        validated = UpdateCustomerRecordInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool(
        "update_customer_record", validated, args, "update_customer_record"
    )


# ---------------------------------------------------------------------------
# 7. confirm_action (mutating=False — no provider adapter, no idempotency key)
# ---------------------------------------------------------------------------


@tool(
    "confirm_action",
    (
        "Submit a confirmation request for a pending transactional action that requires "
        "human approval. Creates a pending_confirmations row for Phase-18 resolution. "
        "Does NOT execute the underlying action and does NOT require an idempotency_key "
        "(mutating=False). Duplicate confirmation dedup is deferred to Phase 18."
    ),
    ConfirmActionInput.model_json_schema(),
)
async def confirm_action_tool(args: dict) -> dict:
    """Write a pending_confirmations row — no provider adapter, no idempotency key.

    mutating=False (TOOL_REGISTRY): confirm_action only creates a confirmation row;
    it does NOT execute the underlying provider action. The Phase-18 admin UI will
    resolve pending rows. PRD DDL unchanged.

    Duplicate-confirm dedup is a Phase-18 concern. For now, duplicate calls will
    write duplicate rows (accepted risk — T-14-04-05).
    """
    try:
        validated = ConfirmActionInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }

    # Lazy import to access the ContextVar set by build_tool_server.
    from app.services.agent_tools import _agent_id_var

    agent_id = _agent_id_var.get()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=_CONFIRM_TTL_HOURS)

    # Client-generate the UUID so the ID is set on the Python object without needing
    # a DB flush/refresh (enables str(row.id) immediately after db.add in test and prod).
    confirmation_id = uuid4()

    row = PendingConfirmation(
        id=confirmation_id,
        agent_id=agent_id,
        skill=validated.skill,
        arguments={"action_reference": validated.action_reference},
        requested_at=now,
        expires_at=expires_at,
        # resolved_at and resolution left NULL until Phase 18 resolves the row.
    )

    with get_sync_db() as db:
        db.add(row)
        db.commit()

    log.info(
        "confirm_action.pending_row_written",
        agent_id=agent_id,
        skill=validated.skill,
        confirmation_id=str(confirmation_id),
        action_reference=validated.action_reference,
    )

    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Confirmation request submitted for '{validated.skill}' action "
                    f"(reference: {validated.action_reference}). "
                    f"Awaiting human approval. Confirmation ID: {confirmation_id}."
                ),
            }
        ]
    }


# ---------------------------------------------------------------------------
# Registry attachment — attach each SdkMcpTool to its TOOL_REGISTRY entry.
# This is the module-level side-effect that makes TOOL_REGISTRY the single
# source linking metadata <-> SdkMcpTool (Plan-02 sdk_tool field intent).
# ---------------------------------------------------------------------------

TOOL_REGISTRY["place_order"].sdk_tool = place_order_tool
TOOL_REGISTRY["cancel_order"].sdk_tool = cancel_order_tool
TOOL_REGISTRY["issue_refund"].sdk_tool = issue_refund_tool
TOOL_REGISTRY["update_subscription"].sdk_tool = update_subscription_tool
TOOL_REGISTRY["book_slot"].sdk_tool = book_slot_tool
TOOL_REGISTRY["update_customer_record"].sdk_tool = update_customer_record_tool
TOOL_REGISTRY["confirm_action"].sdk_tool = confirm_action_tool
