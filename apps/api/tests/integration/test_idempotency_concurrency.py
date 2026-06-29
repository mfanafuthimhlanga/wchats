"""
Integration test — CR-02 exactly-once proof under concurrent same-key reservation.

Gates:
    INTEGRATION_TESTS_ENABLED=1  (entire module skips when unset)

Requires:
    - Local PostgreSQL with the wchats_control schema and migration 0015 applied
      (postgresql://wchats:wchats@localhost:5432/wchats_control by default, or
      override via INTEGRATION_DB_URL env var).
    - The reservation engine (plan 14-06) must be implemented (reserve/finalize/
      release/compute_args_hash symbols present in idempotency.py).

Purpose:
    Prove that the atomic INSERT ... ON CONFLICT DO NOTHING RETURNING guard makes
    "exactly one execution per key" a DB-enforced invariant, not an application
    check-then-act race.  Under concurrent double-delivery of the same (agent_id,
    skill, idempotency_key), the UNIQUE constraint decides the single winner; losers
    receive 'in_progress' (or 'replay' if the winner finalized first) and NEVER
    execute the mutation.

    This test uses concurrent.futures.ThreadPoolExecutor so the race hits the real
    ON CONFLICT — mocked unit tests cannot prove this invariant.

Design notes:
    - Each thread calls asyncio.run(reserve_idempotency(...)) which creates its own
      event loop.  The asyncio.to_thread offload inside reserve_idempotency submits
      the blocking DB work to that loop's thread pool — no cross-loop sharing.
    - get_sync_db uses the app.core.database.SyncSessionFactory (url from
      CONTROL_DB_SYNC_URL, set to the local postgres url at the top of this conftest).
    - Rows are identified by UUID-keyed (agent_id, skill, idempotency_key) tuples and
      cleaned up in finally blocks (T-07-01 pattern).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
from uuid import uuid4

import pytest
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Gate: entire module skips when INTEGRATION_TESTS_ENABLED != "1"
# ---------------------------------------------------------------------------
INTEGRATION_TESTS_ENABLED = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

pytestmark = pytest.mark.skipif(
    not INTEGRATION_TESTS_ENABLED,
    reason=(
        "Skipping live-Postgres concurrency tests — "
        "set INTEGRATION_TESTS_ENABLED=1 to run"
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reserve_in_thread(agent_id_str: str, skill: str, key: str, args_hash: str):
    """Sync entry point for ThreadPoolExecutor workers.

    Each worker creates its own asyncio event loop via asyncio.run so there
    is no cross-thread event-loop sharing.
    """
    from app.services.transactional.idempotency import reserve_idempotency

    return asyncio.run(reserve_idempotency(agent_id_str, skill, key, args_hash))


def _finalize_in_thread(agent_id_str: str, skill: str, key: str, result: dict):
    from app.services.transactional.idempotency import finalize_idempotency

    asyncio.run(finalize_idempotency(agent_id_str, skill, key, result))


def _release_in_thread(agent_id_str: str, skill: str, key: str):
    from app.services.transactional.idempotency import release_idempotency

    asyncio.run(release_idempotency(agent_id_str, skill, key))


def _make_result() -> dict:
    return {"content": [{"type": "text", "text": "Order confirmed"}], "is_error": False}


# ---------------------------------------------------------------------------
# Test 1 — concurrent double-delivery: exactly one winner, one row, replay
# ---------------------------------------------------------------------------


def test_concurrent_same_key_exactly_one_winner(db_session):
    """Two concurrent reservations for the same key: exactly one is 'reserved'.

    The winner proceeds to execute the mutation; the loser gets 'in_progress'.
    After the winner finalizes, a third reservation sees 'replay' with the stored
    result.  A single row exists in tool_idempotency_keys throughout.
    """
    from app.services.transactional.idempotency import compute_args_hash

    agent_id = uuid4()
    agent_id_str = str(agent_id)
    skill = "test_concurrent_place_order"
    key = f"concurrent-key-{uuid4()}"
    args_hash = compute_args_hash({"product_id": "PROD-A", "quantity": 2})

    try:
        # --- Fire two concurrent reservations against the same key ---
        # Use a threading.Barrier to maximize overlap.
        barrier = threading.Barrier(2)

        def _reserve_synchronized():
            barrier.wait()  # both threads enter the DB path at the same time
            return _reserve_in_thread(agent_id_str, skill, key, args_hash)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as exc:
            futures = [exc.submit(_reserve_synchronized) for _ in range(2)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        states = sorted(r.state for r in results)

        # Exactly one winner
        assert states.count("reserved") == 1, (
            f"Expected exactly one 'reserved'; got states={states}"
        )
        # The loser must be in_progress (pending row visible) or replay (if the
        # winner committed very quickly before the loser's SELECT ran)
        loser_state = [s for s in states if s != "reserved"][0]
        assert loser_state in ("in_progress", "replay"), (
            f"Loser state must be 'in_progress' or 'replay', got '{loser_state}'"
        )

        # --- DB must have exactly one row ---
        row_count = db_session.execute(
            text(
                "SELECT count(*) FROM tool_idempotency_keys "
                "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k"
            ),
            {"a": agent_id_str, "s": skill, "k": key},
        ).scalar()
        assert row_count == 1, f"Expected 1 row, found {row_count}"

        # --- Winner finalizes ---
        result = _make_result()
        _finalize_in_thread(agent_id_str, skill, key, result)

        # --- Third reservation must be a replay ---
        third = asyncio.run(
            __import__(
                "app.services.transactional.idempotency", fromlist=["reserve_idempotency"]
            ).reserve_idempotency(agent_id_str, skill, key, args_hash)
        )
        assert third.state == "replay", (
            f"Third reservation after finalize must be 'replay', got '{third.state}'"
        )
        assert third.result is not None, "Replay reservation must carry the stored result"

        # --- Row count still 1 after finalize ---
        row_count_after = db_session.execute(
            text(
                "SELECT count(*) FROM tool_idempotency_keys "
                "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k"
            ),
            {"a": agent_id_str, "s": skill, "k": key},
        ).scalar()
        assert row_count_after == 1, f"Expected 1 row after finalize, found {row_count_after}"

    finally:
        try:
            db_session.execute(
                text(
                    "DELETE FROM tool_idempotency_keys "
                    "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k"
                ),
                {"a": agent_id_str, "s": skill, "k": key},
            )
            db_session.commit()
        except Exception:
            db_session.rollback()


# ---------------------------------------------------------------------------
# Test 2 — release allows re-reservation (no orphan after crash)
# ---------------------------------------------------------------------------


def test_release_allows_re_reservation(db_session):
    """A reserved-then-released key allows a subsequent reserve to win (no orphan row)."""
    from app.services.transactional.idempotency import (
        compute_args_hash,
        reserve_idempotency,
    )

    agent_id = uuid4()
    agent_id_str = str(agent_id)
    skill = "test_release_reservice"
    key = f"release-key-{uuid4()}"
    args_hash = compute_args_hash({"product_id": "PROD-B", "quantity": 1})

    try:
        # First reserve — should win
        first = asyncio.run(reserve_idempotency(agent_id_str, skill, key, args_hash))
        assert first.state == "reserved", f"Expected 'reserved', got '{first.state}'"

        # Release (simulates crash / rollback before finalize)
        _release_in_thread(agent_id_str, skill, key)

        # Row should be gone
        row_count = db_session.execute(
            text(
                "SELECT count(*) FROM tool_idempotency_keys "
                "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k"
            ),
            {"a": agent_id_str, "s": skill, "k": key},
        ).scalar()
        assert row_count == 0, (
            f"release_idempotency must delete the pending row; found {row_count} rows"
        )

        # Re-reserve — should win again
        second = asyncio.run(reserve_idempotency(agent_id_str, skill, key, args_hash))
        assert second.state == "reserved", (
            f"Re-reservation after release must win; got '{second.state}'"
        )

    finally:
        try:
            db_session.execute(
                text(
                    "DELETE FROM tool_idempotency_keys "
                    "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k"
                ),
                {"a": agent_id_str, "s": skill, "k": key},
            )
            db_session.commit()
        except Exception:
            db_session.rollback()


# ---------------------------------------------------------------------------
# Test 3 — args_mismatch returns error, not stale replay
# ---------------------------------------------------------------------------


def test_args_mismatch_returns_error_not_stale_replay(db_session):
    """Same key with different business args returns args_mismatch, not the stale result."""
    from app.services.transactional.idempotency import (
        compute_args_hash,
        finalize_idempotency,
        reserve_idempotency,
    )

    agent_id = uuid4()
    agent_id_str = str(agent_id)
    skill = "test_args_mismatch"
    key = f"mismatch-key-{uuid4()}"
    hash_a = compute_args_hash({"product_id": "PROD-A", "quantity": 1})
    hash_b = compute_args_hash({"product_id": "PROD-B", "quantity": 1})

    try:
        # First call with product A
        first = asyncio.run(reserve_idempotency(agent_id_str, skill, key, hash_a))
        assert first.state == "reserved"

        result_a = {"content": [{"type": "text", "text": "Product A ordered"}], "is_error": False}
        asyncio.run(finalize_idempotency(agent_id_str, skill, key, result_a))

        # Second call with SAME key but DIFFERENT business args (product B)
        second = asyncio.run(reserve_idempotency(agent_id_str, skill, key, hash_b))
        assert second.state == "args_mismatch", (
            f"Expected args_mismatch for different business args; got '{second.state}'"
        )
        assert second.result is None, "args_mismatch must not carry the stale result"

    finally:
        try:
            db_session.execute(
                text(
                    "DELETE FROM tool_idempotency_keys "
                    "WHERE agent_id = :a AND skill = :s AND idempotency_key = :k"
                ),
                {"a": agent_id_str, "s": skill, "k": key},
            )
            db_session.commit()
        except Exception:
            db_session.rollback()
