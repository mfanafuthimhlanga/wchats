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

    compute_args_hash(args) -> str
        sha256 hex of the canonicalized tool arguments, with idempotency_key excluded.
        Excluding idempotency_key makes the hash bind to the *logical request* (same
        business args regardless of which key string the LLM picked).  This lets:
          - The same logical call with the same key replay correctly.
          - The same key reused with different business args be detected (WR-02 fix).

    reserve_idempotency(agent_id, skill, idempotency_key, args_hash) -> Reservation
        Atomic claim before the adapter runs (CR-02 fix).  Single INSERT ...
        ON CONFLICT DO NOTHING RETURNING id; the DB decides the winner:
          "reserved"      — this caller won; proceed to execute.
          "replay"        — prior completed result available; return it.
          "in_progress"   — another worker is executing; caller should wait/retry.
          "args_mismatch" — key reused with different business args; return error.
          "unknown"       — a stale 'in_flight' row exists (CR-01): the adapter may
                            already have been called by a worker that then vanished
                            before it could finalize. Never auto-reclaimed — the
                            caller must surface this for manual reconciliation
                            rather than guess.

    mark_reservation_in_flight(agent_id, skill, idempotency_key) -> None
        Durably record "the adapter call is about to happen" (CR-01 fix)
        immediately before the (possibly irreversible) adapter call. Flips a
        'pending' row to 'in_flight'. This is what lets a later stale-reclaim
        tell "adapter never touched" (safe to reclaim) apart from "adapter
        call may already have run" (never safe to auto re-execute).

    finalize_idempotency(agent_id, skill, idempotency_key, result) -> None
        Mark the pending/in_flight reservation completed and persist the result.

    release_idempotency(agent_id, skill, idempotency_key) -> None
        Delete a *pending or in_flight* reservation so a legitimate retry can
        re-run. Never deletes a completed row. Only called from a live worker
        that has just synchronously observed the outcome (a caught exception,
        a pre-adapter denial) — never from a crash-recovery/reclaim path,
        which must use the stale-reclaim logic in reserve_idempotency instead.

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

RESERVATION CONTRACT (CR-02 anchor):
    The reserve-before-execute engine inserts a 'pending' row BEFORE the adapter
    runs.  Only the insert winner proceeds; losers read the existing row and return
    in_progress / replay / args_mismatch WITHOUT executing.  A 'pending' row that
    goes stale (crashed before the adapter was ever called) is reclaimable after
    _RESERVATION_LEASE_SECONDS via an UPDATE...RETURNING — safe, because the
    adapter was never reached.

    CR-01 anchor: a row is flipped to 'in_flight' immediately before the adapter
    call (mark_reservation_in_flight). A stale 'in_flight' row means the adapter
    call may have already happened before the worker vanished — Celery's
    visibility_timeout (broker redelivery window) is 30x longer than
    _RESERVATION_LEASE_SECONDS, so "stale" does not mean "safe": the reclaim path
    NEVER re-executes a stale 'in_flight' row automatically (that risks a second,
    real provider call for something like a refund). It instead returns
    Reservation(state="unknown") so the caller can surface the row for manual
    reconciliation, fail-closed rather than guessing.

EXECUTOR OFFLOAD (WR-03):
    All functions that touch get_sync_db use asyncio.to_thread to keep the
    synchronous DB calls off the event loop, consistent with the run_in_executor
    pattern in agent_tools.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import structlog
from sqlalchemy import text as sa_text

from app.core.database import get_sync_db

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Reservation lease — stale 'pending' rows older than this are reclaimable
# (the adapter was never called for them, so re-executing is safe). A stale
# 'in_flight' row older than this is NEVER reclaimed (CR-01) — it means the
# adapter call may already have happened, and this constant deliberately
# does NOT need to exceed Celery's broker visibility_timeout (3600s,
# celery_app.py) for correctness anymore: staleness here only decides
# in_progress vs unknown, never whether to re-execute. A 'pending' reservation
# left by a crash-before-the-adapter-call cannot deadlock a key forever; an
# 'in_flight' reservation left by a crash-during-or-after the adapter call is
# deliberately left stuck (state="unknown") for a human to reconcile.
# ---------------------------------------------------------------------------
_RESERVATION_LEASE_SECONDS: int = 120


# ---------------------------------------------------------------------------
# Reservation result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Reservation:
    """Result of reserve_idempotency.

    state:
        "reserved"      — this caller won the INSERT; proceed to execute the adapter.
        "replay"        — a prior completed row exists; result carries the stored value.
        "in_progress"   — another worker is executing; caller should wait/retry.
        "args_mismatch" — stored args_hash differs from incoming; key reused with
                          different business arguments; return an error to the caller.
        "unknown"       — (CR-01) a stale 'in_flight' row exists: the adapter may
                          already have run. Never reclaimed automatically — the
                          caller must treat this as a denial and surface it for
                          manual reconciliation, never retry the adapter itself.

    result:
        None unless state == "replay", in which case it is the previously stored
        tool response dict (already JSON-decoded).
    """

    state: Literal["reserved", "replay", "in_progress", "args_mismatch", "unknown"]
    result: dict | None = None


# ---------------------------------------------------------------------------
# compute_args_hash
# ---------------------------------------------------------------------------

def compute_args_hash(args: dict) -> str:
    """sha256 hex of the canonicalized tool arguments, with idempotency_key excluded.

    Excluding idempotency_key binds the hash to the *logical request* so that:
      - The same business args with different key values hash the same (correct replay).
      - The same key value with different business args hash differently (detects WR-02
        key-reuse-with-different-args and surfaces an args_mismatch error).

    Args:
        args: Raw tool argument dict (may include "idempotency_key").

    Returns:
        64-char lowercase hex string (sha256).
    """
    filtered = {k: v for k, v in args.items() if k != "idempotency_key"}
    canonical = json.dumps(filtered, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# reserve_idempotency
# ---------------------------------------------------------------------------

async def reserve_idempotency(
    agent_id: Any,
    skill: str,
    idempotency_key: str,
    args_hash: str,
) -> Reservation:
    """Atomic reserve-before-execute claim (CR-02 fix).

    Performs a single INSERT ... ON CONFLICT (agent_id, skill, idempotency_key)
    DO NOTHING RETURNING id.  The DB decides the single winner:
      - RETURNING returns a row  → this caller is the winner → Reservation("reserved").
      - RETURNING returns nothing → conflict; read the existing row to decide outcome.

    Conflict outcomes:
      args_hash mismatch → Reservation("args_mismatch")   — WR-02: key reused differently.
      status = 'completed' → Reservation("replay", result) — prior result available.
      status = 'pending', recent → Reservation("in_progress") — other worker running.
      status = 'pending', stale (> lease) → attempt a reclaim UPDATE:
          UPDATE success → Reservation("reserved")         — this agent reclaims.
          UPDATE miss    → Reservation("in_progress")      — someone else reclaimed first.
          (Safe: a 'pending' row means the adapter was never called.)
      status = 'in_flight', recent → Reservation("in_progress") — adapter call may
          be genuinely running right now; wait/retry.
      status = 'in_flight', stale (> lease) → Reservation("unknown") — CR-01: NEVER
          reclaimed. The adapter may already have been called by a worker that
          then vanished before finalize_idempotency ran. Re-executing here could
          be a second, real provider call; the row is surfaced instead for manual
          reconciliation.

    The blocking get_sync_db calls are offloaded via asyncio.to_thread (WR-03).
    """
    agent_id_str = str(agent_id)

    def _inner() -> Reservation:
        with get_sync_db() as db:
            # --- Atomic INSERT ---
            insert_row = db.execute(
                sa_text(
                    "INSERT INTO tool_idempotency_keys "
                    "(agent_id, skill, idempotency_key, args_hash, status, reserved_at, result) "
                    "VALUES (:a, :s, :k, :h, 'pending', now(), NULL) "
                    "ON CONFLICT (agent_id, skill, idempotency_key) DO NOTHING "
                    "RETURNING id"
                ),
                {
                    "a": agent_id_str,
                    "s": skill,
                    "k": idempotency_key,
                    "h": args_hash,
                },
            ).first()

            if insert_row is not None:
                # This caller won the INSERT — commit and return reserved.
                db.commit()
                log.info(
                    "reserve_idempotency.reserved",
                    agent_id=agent_id_str,
                    skill=skill,
                )
                return Reservation(state="reserved")

            # --- Conflict: inspect the existing row ---
            existing = db.execute(
                sa_text(
                    "SELECT status, result, args_hash, reserved_at "
                    "FROM tool_idempotency_keys "
                    "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k"
                ),
                {"a": agent_id_str, "s": skill, "k": idempotency_key},
            ).mappings().first()

            if existing is None:
                # Extremely rare: conflict but row vanished (e.g. deleted between
                # the INSERT and the SELECT).  Treat as in_progress.
                return Reservation(state="in_progress")

            stored_hash: str | None = existing["args_hash"]

            # WR-02: check args_hash mismatch first (before status).
            # NULL stored hash means a legacy row without a hash → skip mismatch check.
            if stored_hash is not None and stored_hash != args_hash:
                log.warning(
                    "reserve_idempotency.args_mismatch",
                    agent_id=agent_id_str,
                    skill=skill,
                )
                return Reservation(state="args_mismatch")

            status: str = existing["status"]

            if status == "completed":
                raw_result = existing["result"]
                if isinstance(raw_result, str):
                    raw_result = json.loads(raw_result)
                elif raw_result is not None and not isinstance(raw_result, dict):
                    raw_result = dict(raw_result)
                log.info(
                    "reserve_idempotency.replay",
                    agent_id=agent_id_str,
                    skill=skill,
                )
                return Reservation(state="replay", result=raw_result)

            # status == "pending" or "in_flight": check staleness.
            threshold = datetime.now(timezone.utc) - timedelta(
                seconds=_RESERVATION_LEASE_SECONDS
            )
            reserved_at: datetime = existing["reserved_at"]
            if reserved_at.tzinfo is None:
                reserved_at = reserved_at.replace(tzinfo=timezone.utc)
            is_stale = reserved_at < threshold

            if status == "in_flight":
                # CR-01: the adapter call may already have been made by the
                # worker that owns this reservation. Never safe to auto-reclaim,
                # stale or not — a reclaim-and-retry here risks a second, real
                # provider call (e.g. a duplicate refund).
                if is_stale:
                    log.error(
                        "reserve_idempotency.stranded_in_flight",
                        agent_id=agent_id_str,
                        skill=skill,
                    )
                    return Reservation(state="unknown")
                # Recent in_flight row — the owning worker is (most likely)
                # still genuinely mid-call. Same caller-facing behaviour as a
                # recent pending row: wait/retry, do not execute.
                log.info(
                    "reserve_idempotency.in_progress",
                    agent_id=agent_id_str,
                    skill=skill,
                )
                return Reservation(state="in_progress")

            # status == "pending": the adapter has never been called for this
            # reservation, so a stale row is safe to reclaim and re-execute.
            if is_stale:
                # Stale pending row — try to reclaim it atomically. The WHERE
                # clause is scoped to status = 'pending' specifically (not
                # 'in_flight') so this UPDATE can never touch an in-flight row.
                reclaimed = db.execute(
                    sa_text(
                        "UPDATE tool_idempotency_keys "
                        "SET reserved_at = now(), args_hash = :h "
                        "WHERE agent_id = :a AND skill = :s "
                        "  AND idempotency_key = :k "
                        "  AND status = 'pending' "
                        "  AND reserved_at < :threshold "
                        "RETURNING id"
                    ),
                    {
                        "a": agent_id_str,
                        "s": skill,
                        "k": idempotency_key,
                        "h": args_hash,
                        "threshold": threshold,
                    },
                ).first()

                if reclaimed is not None:
                    db.commit()
                    log.info(
                        "reserve_idempotency.stale_reclaimed",
                        agent_id=agent_id_str,
                        skill=skill,
                    )
                    return Reservation(state="reserved")
                # Someone else reclaimed while we were checking.
                log.info(
                    "reserve_idempotency.reclaim_lost",
                    agent_id=agent_id_str,
                    skill=skill,
                )
                return Reservation(state="in_progress")

            # Recent pending row — another worker is executing.
            log.info(
                "reserve_idempotency.in_progress",
                agent_id=agent_id_str,
                skill=skill,
            )
            return Reservation(state="in_progress")

    return await asyncio.to_thread(_inner)


# ---------------------------------------------------------------------------
# mark_reservation_in_flight (CR-01)
# ---------------------------------------------------------------------------

async def mark_reservation_in_flight(
    agent_id: Any,
    skill: str,
    idempotency_key: str,
) -> None:
    """Flip a 'pending' reservation to 'in_flight' immediately before the adapter call.

    CR-01: this durably records "an adapter call is about to happen" BEFORE
    the potentially-irreversible provider call, so that if the worker crashes
    between this call and finalize_idempotency, a later reclaim can tell the
    difference between "adapter never touched" (a 'pending' row — safe to
    reclaim) and "adapter call may already have run" (an 'in_flight' row —
    never safe to auto re-execute; reserve_idempotency's stale-reclaim path
    returns "unknown" for it instead).

    Only updates a row in status='pending' — a no-op (not an error) if the
    row is already 'in_flight' or 'completed'.

    Args:
        agent_id: UUID of the calling agent.
        skill: Tool/skill name.
        idempotency_key: The reservation key to mark.
    """
    agent_id_str = str(agent_id)

    def _inner() -> None:
        with get_sync_db() as db:
            db.execute(
                sa_text(
                    "UPDATE tool_idempotency_keys "
                    "SET status = 'in_flight' "
                    "WHERE agent_id = :a AND skill = :s "
                    "  AND idempotency_key = :k "
                    "  AND status = 'pending'"
                ),
                {
                    "a": agent_id_str,
                    "s": skill,
                    "k": idempotency_key,
                },
            )
            db.commit()

    await asyncio.to_thread(_inner)
    log.info(
        "mark_reservation_in_flight.committed",
        agent_id=agent_id_str,
        skill=skill,
    )


# ---------------------------------------------------------------------------
# finalize_idempotency
# ---------------------------------------------------------------------------

async def finalize_idempotency(
    agent_id: Any,
    skill: str,
    idempotency_key: str,
    result: dict,
) -> None:
    """Mark the pending/in_flight reservation as completed and persist the result.

    Only updates a row in status IN ('pending', 'in_flight') — safe to call
    idempotently if a prior finalize already committed (the UPDATE matches no
    rows, which is a no-op, not an error). 'pending' is matched too for
    back-compat with any reservation that reached this call without going
    through mark_reservation_in_flight first.

    Args:
        agent_id: UUID of the calling agent.
        skill: Tool/skill name.
        idempotency_key: The reservation key to finalize.
        result: Full tool response dict (JSON-serializable).
    """
    agent_id_str = str(agent_id)

    def _inner() -> None:
        with get_sync_db() as db:
            db.execute(
                sa_text(
                    "UPDATE tool_idempotency_keys "
                    "SET result = CAST(:r AS JSONB), status = 'completed' "
                    "WHERE agent_id = :a AND skill = :s "
                    "  AND idempotency_key = :k "
                    "  AND status IN ('pending', 'in_flight')"
                ),
                {
                    "a": agent_id_str,
                    "s": skill,
                    "k": idempotency_key,
                    "r": json.dumps(result),
                },
            )
            db.commit()

    await asyncio.to_thread(_inner)
    log.info(
        "finalize_idempotency.committed",
        agent_id=agent_id_str,
        skill=skill,
    )


# ---------------------------------------------------------------------------
# release_idempotency
# ---------------------------------------------------------------------------

async def release_idempotency(
    agent_id: Any,
    skill: str,
    idempotency_key: str,
) -> None:
    """Delete a *pending or in_flight* reservation so a legitimate retry can re-run.

    Scoped to status IN ('pending', 'in_flight') so a completed row is NEVER
    deleted. Safe to call after a denial, actor block, or an adapter call
    that raised synchronously within the SAME live worker (CR-01: this is a
    worker that is still alive and has just observed the outcome directly —
    not a crash-recovery/reclaim path. A reservation abandoned by a worker
    that vanished must go through reserve_idempotency's stale-reclaim logic
    instead, which refuses to auto-release/reclaim an 'in_flight' row).

    Args:
        agent_id: UUID of the calling agent.
        skill: Tool/skill name.
        idempotency_key: The reservation key to release.
    """
    agent_id_str = str(agent_id)

    def _inner() -> None:
        with get_sync_db() as db:
            db.execute(
                sa_text(
                    "DELETE FROM tool_idempotency_keys "
                    "WHERE agent_id = :a AND skill = :s "
                    "  AND idempotency_key = :k "
                    "  AND status IN ('pending', 'in_flight')"
                ),
                {"a": agent_id_str, "s": skill, "k": idempotency_key},
            )
            db.commit()

    await asyncio.to_thread(_inner)
    log.info(
        "release_idempotency.released",
        agent_id=agent_id_str,
        skill=skill,
    )


# ---------------------------------------------------------------------------
# check_idempotency — FACADE (retained unchanged for 14-04 dispatcher + tests)
# ---------------------------------------------------------------------------

async def check_idempotency(
    agent_id: Any,
    skill: str,
    idempotency_key: str,
) -> dict | None:
    """Look up a prior execution result from the control-DB idempotency table.

    FACADE: retained for the 14-04 dispatcher (tools.py) and existing unit tests.
    The reserve-before-execute engine (reserve_idempotency) supersedes this
    function; the dispatcher cutover happens in plan 14-08.

    Args:
        agent_id: UUID of the calling agent.
        skill: Tool/skill name (e.g. "place_order").
        idempotency_key: Client-provided key scoped to (agent_id, skill).

    Returns:
        The stored result dict on a cache hit, or None on a miss.
    """
    agent_id_str = str(agent_id)

    def _inner():
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
        return row

    row = await asyncio.to_thread(_inner)

    if row is None:
        return None

    # row may be a dict already (JSONB) or a string (some drivers return JSON as str)
    if isinstance(row, dict):
        return row
    if isinstance(row, str):
        return json.loads(row)
    return dict(row)


# ---------------------------------------------------------------------------
# store_idempotency — FACADE (retained unchanged for 14-04 dispatcher + tests)
# ---------------------------------------------------------------------------

async def store_idempotency(
    agent_id: Any,
    skill: str,
    idempotency_key: str,
    result: dict,
) -> None:
    """Persist a tool execution result using ON CONFLICT DO NOTHING.

    FACADE: retained for the 14-04 dispatcher (tools.py) and existing unit tests.
    The finalize_idempotency function supersedes this; the dispatcher cutover
    happens in plan 14-08.

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

    def _inner() -> None:
        with get_sync_db() as db:
            db.execute(
                sa_text(
                    "INSERT INTO tool_idempotency_keys "
                    "(agent_id, skill, idempotency_key, result) "
                    "VALUES (:a, :s, :k, CAST(:r AS JSONB)) "
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

    await asyncio.to_thread(_inner)
    log.info(
        "tool_idempotency.stored",
        agent_id=agent_id_str,
        skill=skill,
    )
