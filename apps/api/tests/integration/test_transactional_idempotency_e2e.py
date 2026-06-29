"""
Integration test — end-to-end exactly-once replay through the real dispatcher (UAT item 2).

Gates:
    INTEGRATION_TESTS_ENABLED=1  (entire module skips when unset)

Requires:
    - Local PostgreSQL with the wchats_control schema and migrations applied
      (capability_envelopes, tool_idempotency_keys, tool_calls_audit tables must exist).
    - INTEGRATION_DB_URL env var, or the default postgresql://wchats:wchats@localhost:5432/wchats_control.
    - The 14-08 dispatcher must be in place (reserve-before-execute order).

Purpose:
    Prove that a transactional tool called twice with the same idempotency_key
    invokes the underlying stub adapter EXACTLY ONCE end-to-end — using the REAL
    Postgres reserve/finalize engine (not mocked). This closes UAT item 2 and
    the CR-02 BLOCKER end-to-end.

    The test spies on StubProviderAdapter.place_order to count invocations, drives
    place_order_tool.handler() twice with the same key, and asserts:
      - adapter invoked exactly once (second call was a replay)
      - exactly one completed row in tool_idempotency_keys
      - no is_error on either call

Design notes:
    - The test patches call_actor_gate (→ approve) and write_audit_row (→ spy)
      to avoid needing a live Claude Haiku and tool_calls_audit table schema
      — the key invariant is the idempotency key lifecycle, not those side-paths.
    - ContextVars are set synchronously before each asyncio.run() call so the
      event loop inherits them correctly (asyncio.run copies the current context).
    - Each asyncio.run() creates a new event loop, which is correct for calling
      asyncio.to_thread-based DB work sequentially between separate calls.
    - Teardown uses the db_session fixture (sync Session) to delete all test rows
      by agent_id (T-07-01 pattern).
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch
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
        "Skipping live-Postgres e2e idempotency test — "
        "set INTEGRATION_TESTS_ENABLED=1 to run (UAT item 2)"
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_place_order_args(idempotency_key: str) -> dict:
    return {
        "idempotency_key": idempotency_key,
        "product_id": "E2E-SKU-001",
        "quantity": 1,
        "customer_email": "e2e@test.local",
        "shipping_address": "1 Integration St, Testville",
        "amount_cents": 1000,
    }


def _set_ctx(agent_id_str: str, conv_id: str = "conv-e2e-001") -> None:
    """Set ContextVars synchronously so the next asyncio.run() inherits them."""
    from app.services.agent_tools import _agent_id_var, _conversation_id_var  # noqa: PLC0415

    _agent_id_var.set(agent_id_str)
    _conversation_id_var.set(conv_id)


def _seed_capability_envelope(db_session, agent_id_str: str, skill: str = "place_order") -> None:
    """Insert an enabled capability_envelopes row for (agent_id, skill)."""
    db_session.execute(
        text(
            """
            INSERT INTO capability_envelopes
                (id, agent_id, skill, enabled, rate_limit, constraints,
                 requires_confirmation, requires_identity_verification, updated_at)
            VALUES
                (:id, :a, :s, True, NULL, NULL::jsonb, False, False, now())
            ON CONFLICT (agent_id, skill) DO UPDATE SET enabled = True
            """
        ),
        {"id": str(uuid4()), "a": agent_id_str, "s": skill},
    )
    db_session.commit()


def _cleanup_rows(db_session, agent_id_str: str) -> None:
    """Remove all test rows keyed by agent_id in dependency order."""
    for table in ("tool_idempotency_keys", "tool_calls_audit", "capability_envelopes"):
        try:
            db_session.execute(
                text(f"DELETE FROM {table} WHERE agent_id = :a"),  # noqa: S608
                {"a": agent_id_str},
            )
        except Exception:  # noqa: BLE001
            db_session.rollback()
            break
    db_session.commit()


# ---------------------------------------------------------------------------
# Test — two place_order calls, same idempotency key, adapter invoked once
# ---------------------------------------------------------------------------


def test_place_order_exactly_once_replay_e2e(db_session):
    """UAT item 2 / CR-02 BLOCKER: real Postgres, adapter invoked exactly once.

    Two consecutive calls with the same idempotency_key go through the full
    dispatcher (check_capability_access, reserve_idempotency, finalize_idempotency).
    The second call must return the replay result without executing the adapter again.
    """
    from app.services.transactional.provider_adapter import StubProviderAdapter

    agent_id_str = str(uuid4())
    idem_key = f"e2e-replay-{uuid4()}"
    call_counter = {"n": 0}

    # Seed an enabled capability envelope so check_capability_access passes
    _seed_capability_envelope(db_session, agent_id_str, "place_order")

    # Spy on StubProviderAdapter.place_order to count real invocations.
    # We patch the method on the class so the module-level singleton is affected.
    _original_place_order = StubProviderAdapter.place_order

    async def _spy_place_order(self, args, agent_id_arg):
        call_counter["n"] += 1
        return await _original_place_order(self, args, agent_id_arg)

    StubProviderAdapter.place_order = _spy_place_order

    try:
        args = _valid_place_order_args(idem_key)

        with (
            patch(
                "app.services.transactional.tools.call_actor_gate",
                AsyncMock(return_value=("approve", "")),
            ),
            patch(
                "app.services.transactional.tools.write_audit_row",
                AsyncMock(),
            ),
        ):
            from app.services.transactional.tools import place_order_tool

            # --- First call: should execute the adapter and finalize the key ---
            _set_ctx(agent_id_str)
            result1 = asyncio.run(place_order_tool.handler(args))

            # --- Second call: same key — must replay without calling the adapter ---
            _set_ctx(agent_id_str)
            result2 = asyncio.run(place_order_tool.handler(args))

        # ---- Assertions ----

        # 1. Neither call should be an error
        assert result1.get("is_error") is not True, (
            f"First call unexpectedly returned is_error: {result1}"
        )
        assert result2.get("is_error") is not True, (
            f"Second call (replay) unexpectedly returned is_error: {result2}"
        )

        # 2. Adapter invoked exactly once (second call was a replay)
        assert call_counter["n"] == 1, (
            f"Expected adapter invoked once; got {call_counter['n']} "
            f"(CR-02 exactly-once invariant broken)"
        )

        # 3. Exactly one completed row in tool_idempotency_keys
        row = db_session.execute(
            text(
                "SELECT status, count(*) as cnt "
                "FROM tool_idempotency_keys "
                "WHERE agent_id = :a AND skill = 'place_order' AND idempotency_key = :k "
                "GROUP BY status"
            ),
            {"a": agent_id_str, "k": idem_key},
        ).mappings().first()

        assert row is not None, "No idempotency row found after two calls"
        assert row["cnt"] == 1, (
            f"Expected exactly one idempotency row; got {row['cnt']}"
        )
        assert row["status"] == "completed", (
            f"Expected status='completed'; got {row['status']!r}"
        )

    finally:
        StubProviderAdapter.place_order = _original_place_order
        _cleanup_rows(db_session, agent_id_str)


# ---------------------------------------------------------------------------
# Test — replay returns the same result as the original call
# ---------------------------------------------------------------------------


def test_replay_returns_original_result_e2e(db_session):
    """The replay response content must match the original call's response content."""
    from app.services.transactional.provider_adapter import StubProviderAdapter

    agent_id_str = str(uuid4())
    idem_key = f"e2e-result-{uuid4()}"

    _seed_capability_envelope(db_session, agent_id_str, "place_order")

    try:
        args = _valid_place_order_args(idem_key)

        with (
            patch(
                "app.services.transactional.tools.call_actor_gate",
                AsyncMock(return_value=("approve", "")),
            ),
            patch(
                "app.services.transactional.tools.write_audit_row",
                AsyncMock(),
            ),
        ):
            from app.services.transactional.tools import place_order_tool

            _set_ctx(agent_id_str)
            result1 = asyncio.run(place_order_tool.handler(args))

            _set_ctx(agent_id_str)
            result2 = asyncio.run(place_order_tool.handler(args))

        # The replay must return the same content as the original
        assert result1 == result2, (
            f"Replay result differs from original:\n  first: {result1}\n  replay: {result2}"
        )

    finally:
        _cleanup_rows(db_session, agent_id_str)
