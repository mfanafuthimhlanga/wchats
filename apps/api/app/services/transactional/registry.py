"""
transactional.registry — TransactionalToolDef dataclass + TOOL_METADATA / TOOL_REGISTRY dict.

TOOL_REGISTRY is the single definition-time source of truth for all 7 transactional
skills.  Every flag (mutating, idempotency_required, requires_identity_verification)
is a literal value set here — never runtime-inferred from the tool name or arguments
(T-14-02-02).

A2A/ACP forward-compat:
  - a2a_input_modes / a2a_output_modes default to ["text", "structured"] for all tools
  - examples: 2-3 plain-English phrasings for each mutating tool
  - to_a2a_skill() produces the dict shape expected by the v1.2 manifest serializer
    (no network, no server — metadata only per Plan-02 prohibitions)

sdk_tool field:
  Left None here; Plan-04 attaches the @tool-decorated SdkMcpTool instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TransactionalToolDef:
    """Wraps a transactional tool definition with Phase-14 metadata + A2A forward-compat fields.

    Fields
    ------
    skill_name
        Canonical identifier matching capability_envelopes.skill. Used as the key
        in TOOL_REGISTRY and as the unique skill identifier enforcement/audit/handlers reference.
    mutating
        True for the 6 tools that execute a side-effecting provider action.
        False for confirm_action (which writes a pending_confirmations row but does
        not directly call a provider).
        Literal definition-time flag — never inferred at runtime (T-14-02-02).
    idempotency_required
        True when the tool must carry an idempotency_key and check/store it in
        tool_idempotency_keys.  Matches mutating for all current tools.
    requires_identity_verification
        True when the tool should block on Phase-17 identity verification before
        executing.  Defaults per research Cluster 2: False for book_slot, True
        for the other 5 mutating tools.
    a2a_input_modes
        A2A Agent Card skill inputModes field — default ["text", "structured"].
    a2a_output_modes
        A2A Agent Card skill outputModes field — default ["text", "structured"].
    examples
        Plain-English example phrasings for the v1.2 A2A manifest serializer.
        Non-empty for all mutating tools; empty is acceptable for confirm_action.
    sdk_tool
        The @tool-decorated SdkMcpTool instance.  Left None here; Plan-04 attaches it.
        Optional so this module has zero SDK dependency.
    """

    skill_name: str
    mutating: bool
    idempotency_required: bool
    requires_identity_verification: bool
    a2a_input_modes: list[str] = field(default_factory=lambda: ["text", "structured"])
    a2a_output_modes: list[str] = field(default_factory=lambda: ["text", "structured"])
    examples: list[str] = field(default_factory=list)
    sdk_tool: Any = field(default=None)  # SdkMcpTool | None — Plan-04 fills this


# ---------------------------------------------------------------------------
# TOOL_METADATA / TOOL_REGISTRY
# ---------------------------------------------------------------------------
# Both names are exported; TOOL_METADATA is the authoritative name, TOOL_REGISTRY
# is the alias the rest of the phase imports for conciseness.  They reference the
# same dict object (not a copy), so mutations to one are visible via the other.

TOOL_METADATA: dict[str, TransactionalToolDef] = {
    # ------------------------------------------------------------------
    # 1. place_order — mutating, identity verification required
    # ------------------------------------------------------------------
    "place_order": TransactionalToolDef(
        skill_name="place_order",
        mutating=True,
        idempotency_required=True,
        requires_identity_verification=True,
        examples=[
            "Place an order for 2 units of product SKU-001",
            "I'd like to buy the pro subscription package",
            "Order one bag of Kenyan AA coffee for delivery to 1 Main St",
        ],
    ),

    # ------------------------------------------------------------------
    # 2. cancel_order — mutating, identity verification required
    # ------------------------------------------------------------------
    "cancel_order": TransactionalToolDef(
        skill_name="cancel_order",
        mutating=True,
        idempotency_required=True,
        requires_identity_verification=True,
        examples=[
            "Cancel my order ORD-12345",
            "I want to cancel the order I placed yesterday",
            "Please cancel my most recent purchase",
        ],
    ),

    # ------------------------------------------------------------------
    # 3. issue_refund — mutating, identity verification required
    # ------------------------------------------------------------------
    "issue_refund": TransactionalToolDef(
        skill_name="issue_refund",
        mutating=True,
        idempotency_required=True,
        requires_identity_verification=True,
        examples=[
            "Refund my order ORD-12345 for $25.00",
            "I'd like a refund on the defective item I received",
            "Process a partial refund of R150 for order ORD-99",
        ],
    ),

    # ------------------------------------------------------------------
    # 4. update_subscription — mutating, identity verification required
    # ------------------------------------------------------------------
    "update_subscription": TransactionalToolDef(
        skill_name="update_subscription",
        mutating=True,
        idempotency_required=True,
        requires_identity_verification=True,
        examples=[
            "Upgrade my subscription from basic to pro",
            "Downgrade my plan to the free tier starting next month",
            "Switch my subscription to the annual enterprise plan",
        ],
    ),

    # ------------------------------------------------------------------
    # 5. book_slot — mutating, NO identity verification (lower-risk action)
    # ------------------------------------------------------------------
    "book_slot": TransactionalToolDef(
        skill_name="book_slot",
        mutating=True,
        idempotency_required=True,
        requires_identity_verification=False,  # Cluster 2: False for book_slot
        examples=[
            "Book a consultation for Wednesday 10am",
            "Schedule a delivery for Friday afternoon",
            "Reserve an installation slot for my new product",
        ],
    ),

    # ------------------------------------------------------------------
    # 6. update_customer_record — mutating, identity verification required
    # ------------------------------------------------------------------
    "update_customer_record": TransactionalToolDef(
        skill_name="update_customer_record",
        mutating=True,
        idempotency_required=True,
        requires_identity_verification=True,
        examples=[
            "Update my email address to new@example.com",
            "Change my phone number to 021-555-0100",
            "Update my delivery address to 5 Park Ave",
        ],
    ),

    # ------------------------------------------------------------------
    # 7. confirm_action — NON-mutating (writes pending_confirmations row only)
    # ------------------------------------------------------------------
    "confirm_action": TransactionalToolDef(
        skill_name="confirm_action",
        mutating=False,
        idempotency_required=False,
        requires_identity_verification=False,
        examples=[
            "Yes, confirm my order",
            "Please go ahead with the refund",
        ],
    ),
}

# Alias — the rest of the phase imports TOOL_REGISTRY for conciseness.
TOOL_REGISTRY: dict[str, TransactionalToolDef] = TOOL_METADATA


# ---------------------------------------------------------------------------
# A2A manifest helper
# ---------------------------------------------------------------------------


def to_a2a_skill(tool_def: TransactionalToolDef) -> dict:
    """Return the A2A v1.2 Agent Card skill dict for a TransactionalToolDef.

    No network, no server — metadata only (Plan-02 prohibition).
    The sdk_tool field is optional; when present its .name / .description /
    .input_schema attributes are included.  When absent (Phase-14 unit tests),
    the dict falls back to skill_name for the id field only.
    """
    base: dict = {
        "id": tool_def.skill_name,
        "inputModes": tool_def.a2a_input_modes,
        "outputModes": tool_def.a2a_output_modes,
        "examples": tool_def.examples,
    }
    if tool_def.sdk_tool is not None:
        base["name"] = getattr(tool_def.sdk_tool, "name", tool_def.skill_name)
        base["description"] = getattr(tool_def.sdk_tool, "description", "")
        base["inputSchema"] = getattr(tool_def.sdk_tool, "input_schema", {})
    return base
