"""
transactional.audit — Audit row writer (AUD-01).

Provides:
    write_audit_row(...) -> None
        Write one tool_calls_audit row per tool execution, on BOTH success and
        error paths.

Design:
    - Called unconditionally inside every tool handler — never skipped.
    - On the error path (result=None, error set) the row is still written;
      the error column captures the adapter failure message.
    - actor_decision and actor_rationale are written as empty strings in Phase 14.
      Phase 15 (Actor validator) will pass "approve"|"block"|"require_human"
      and the Haiku rationale text.
    - capability_snapshot MUST be a plain dict (ORM/Row objects are rejected per
      Pitfall 4 in 14-RESEARCH.md). The snapshot is stored as JSONB so the
      audit record is self-contained even if the envelope changes later.

AUDIT COVERAGE CONTRACT (T-14-03-04):
    write_audit_row is the single write point for tool_calls_audit rows.
    It is called on both the adapter-success and adapter-error paths.
    An audit gap defeats retrospective alerting and accountability.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from app.core.database import get_sync_db
from app.models.tool_calls_audit import ToolCallsAudit

log = structlog.get_logger(__name__)


async def write_audit_row(
    *,
    agent_id: Any,
    conversation_id: Any,
    skill: str,
    arguments: dict | None,
    result: dict | None,
    actor_decision: str,
    actor_rationale: str,
    capability_snapshot: dict,
    latency_ms: int | None,
    error: str | None,
) -> None:
    """Write one tool_calls_audit row for a tool execution.

    Called exactly once per execution, regardless of success or failure.

    Args:
        agent_id: UUID of the calling agent.
        conversation_id: UUID of the current conversation (None if outside conversation).
        skill: Canonical tool name (e.g. "place_order").
        arguments: Validated input dict (may be None if validation failed before capture).
        result: Adapter output dict on success; None on error.
        actor_decision: Phase 14 = "" always; Phase 15 fills "approve"|"block"|"require_human".
        actor_rationale: Phase 14 = "" always; Phase 15 fills Haiku rationale text.
        capability_snapshot: Plain dict copy of the capability_envelope row at call time.
            MUST be a dict — ORM/Row objects are rejected (Pitfall 4 in RESEARCH.md).
        latency_ms: Wall-clock ms from start of adapter call to result.
        error: Exception message on adapter error; None on success.

    Raises:
        TypeError: If capability_snapshot is not a dict.
    """
    if not isinstance(capability_snapshot, dict):
        raise TypeError(
            f"capability_snapshot must be a plain dict, got {type(capability_snapshot).__name__}. "
            "Never pass ORM/Row objects — convert with dict(row) before calling write_audit_row."
        )

    row = ToolCallsAudit(
        agent_id=agent_id,
        conversation_id=conversation_id,
        skill=skill,
        arguments=arguments,
        result=result,
        actor_decision=actor_decision,
        actor_rationale=actor_rationale,
        capability_snapshot=capability_snapshot,
        latency_ms=latency_ms,
        error=error,
    )

    with get_sync_db() as db:
        db.add(row)
        db.commit()

    log.info(
        "tool_calls_audit.written",
        agent_id=str(agent_id),
        skill=skill,
        has_error=error is not None,
        latency_ms=latency_ms,
    )
