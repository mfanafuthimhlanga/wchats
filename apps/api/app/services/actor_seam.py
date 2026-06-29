"""
actor_seam — Phase-15 pre-execution gate for mutating transactional tools.

call_actor_gate is called inside every mutating tool handler (where mutating=True
in TOOL_REGISTRY) AFTER the capability envelope check and BEFORE the idempotency
check.  Its position in the tool handler execution stack is:

  capability check → [call_actor_gate] → idempotency check → execute(adapter) → audit

Phase 14 (this file): always returns ("approve", "").  The seam exists with the
correct signature so Phase 15 can replace the body without touching any tool handler.

Phase 15 contract:
  The body will be replaced with a direct Anthropic Haiku API call that reads the
  conversation context and the proposed tool call, returning one of:
    "approve"        — tool executes normally
    "block"          — tool handler returns is_error without calling the adapter
    "require_human"  — tool handler writes a pending_confirmations row and returns
                       an "awaiting confirmation" response to the agent

Why this is NOT an SDK hook (LANDMINE 1 from 14-RESEARCH.md):
  ClaudeAgentOptions.hooks (PreToolUseHookInput) routes through the CLI subprocess
  control channel.  It cannot access Python ContextVars (agent_id, conversation_id)
  or the control-DB for the capability envelope.  The seam MUST live inside the
  tool handler as a direct async function call.
"""

from __future__ import annotations


async def call_actor_gate(
    skill: str,
    arguments: dict,
    capability_snapshot: dict,
    conversation_id: str,
    agent_id: str,
) -> tuple[str, str]:
    """Pre-execution gate for mutating transactional tools.

    Called for every tool where TOOL_REGISTRY[skill].mutating is True, after the
    capability envelope check passes and before the idempotency lookup.

    Parameters
    ----------
    skill
        Canonical skill name (e.g. "place_order").  Must match a TOOL_REGISTRY key.
    arguments
        Raw tool-call argument dict (pre-Pydantic-validation in Phase 14; post-
        validation dict in Phase 15, where the Haiku call needs the structured args).
    capability_snapshot
        The capability_envelopes row dict captured at check time (contains enabled,
        rate_limit, constraints, requires_identity_verification).
    conversation_id
        UUID of the current conversation (Phase 15 reads conversation history).
    agent_id
        UUID of the agent making the tool call (Phase 15 scopes the Haiku call).

    Returns
    -------
    tuple[str, str]
        (decision, rationale) where decision ∈ {"approve", "block", "require_human"}.
        Phase 14: always returns ("approve", "").
        Phase 15: rationale is the Haiku-generated explanation for block/require_human.
    """
    # Phase 14 stub — Phase 15 replaces this body with a Haiku API call.
    return ("approve", "")
