"""
transactional.idempotency — Durable idempotency check/store helpers (TXN-02).

Provides:
    check_idempotency(agent_id, skill, idempotency_key) -> dict | None
        Look up a prior execution result from the control-DB tool_idempotency_keys table.
        Returns the stored result dict on hit, or None on miss.

    store_idempotency(agent_id, skill, idempotency_key, result) -> None
        Persist a tool execution result. Uses INSERT ... ON CONFLICT DO NOTHING so
        concurrent retries cannot double-insert (one winner takes it; subsequent
        callers find the row already present).

PER-TOOL vs TURN-LEVEL GUARD:
    This module implements the *tool-level* idempotency guard, which is orthogonal
    to the *turn-level* idempotency guard in run_agent_turn (app/services/agent.py).

    The turn-level guard deduplicates entire agent turns (same conversation + prompt).
    The tool-level guard (this module) deduplicates individual mutating tool calls
    within a turn using a client-provided idempotency_key scoped to (agent_id, skill).

    Both guards are required; they operate at different granularities.

STORAGE: control-DB tool_idempotency_keys (NOT Redis).
    Redis TTL-based keys are lost on restart. With acks_late=True, a Celery task
    redelivered after a Redis restart would not find the idempotency key and would
    re-execute the mutation. The PostgreSQL table with UNIQUE(agent_id, skill,
    idempotency_key) survives restarts and provides correct durability.

    Redis is used ONLY for the rate-limit counter in enforcement.py.
    This module contains no Redis import or usage.

UNIQUE CONTRACT (TXN-02 anchor):
    INSERT ... ON CONFLICT (agent_id, skill, idempotency_key) DO NOTHING
    ensures exactly one row wins in concurrent scenarios. Subsequent store calls
    with the same key silently succeed (DO NOTHING) without raising or overwriting.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import text as sa_text

from app.core.database import get_sync_db

log = structlog.get_logger(__name__)


async def check_idempotency(
    agent_id: Any,
    skill: str,
    idempotency_key: str,
) -> dict | None:
    """Look up a prior execution result from the control-DB idempotency table.

    Args:
        agent_id: UUID of the calling agent.
        skill: Tool/skill name (e.g. "place_order").
        idempotency_key: Client-provided key scoped to (agent_id, skill).

    Returns:
        The stored result dict on a cache hit, or None on a miss.
    """
    agent_id_str = str(agent_id)

    with get_sync_db() as db:
        row = db.execute(
            sa_text(
                "SELECT result "
                "FROM tool_idempotency_keys "
                "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k "
                "LIMIT 1"
            ),
            {"a": agent_id_str, "s": skill, "k": idempotency_key},
        ).scalar_one_or_none()

    if row is None:
        return None

    # row may be a dict already (JSONB) or a string (some drivers return JSON as str)
    if isinstance(row, dict):
        return row
    if isinstance(row, str):
        return json.loads(row)
    return dict(row)


async def store_idempotency(
    agent_id: Any,
    skill: str,
    idempotency_key: str,
    result: dict,
) -> None:
    """Persist a tool execution result using ON CONFLICT DO NOTHING.

    Concurrent retries (acks_late=True) calling this with the same key will
    all succeed: the first wins and inserts the row; subsequent calls hit
    the UNIQUE constraint and do nothing (no error, no overwrite).

    Args:
        agent_id: UUID of the calling agent.
        skill: Tool/skill name (e.g. "place_order").
        idempotency_key: Client-provided key scoped to (agent_id, skill).
        result: Full tool response dict to store for replay.
    """
    agent_id_str = str(agent_id)

    with get_sync_db() as db:
        db.execute(
            sa_text(
                "INSERT INTO tool_idempotency_keys "
                "(agent_id, skill, idempotency_key, result) "
                "VALUES (:a, :s, :k, :r::jsonb) "
                "ON CONFLICT (agent_id, skill, idempotency_key) DO NOTHING"
            ),
            {
                "a": agent_id_str,
                "s": skill,
                "k": idempotency_key,
                "r": json.dumps(result),
            },
        )
        db.commit()

    log.info(
        "tool_idempotency.stored",
        agent_id=agent_id_str,
        skill=skill,
    )
