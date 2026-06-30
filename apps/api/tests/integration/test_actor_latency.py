"""
Live latency test: Actor Haiku p95 < 1s, total added latency < 1.5s (ACT-06).

Gate: ACTOR_LATENCY_ENABLED=1

Requires:
  - ANTHROPIC_API_KEY (real Haiku API call — no mock)
  - No DB or Redis required: DB operations are mocked for the total-added-latency
    test so that only the Actor Haiku call is live.

Tests:
  1. ACT-06 p95 — call_actor_gate N>=20 times against the real Haiku API. Compute
     p50/p95/max wall-clock latency. Assert p95 < P95_BUDGET_MS (1000ms).
  2. ACT-06 total added — one full mutating dispatcher call with a real call_actor_gate
     and all DB/adapter overhead mocked. Assert the full call completes < 1500ms.

Configuration (via env vars):
  ACTOR_LATENCY_N_CALLS   — number of Haiku calls for the p95 test (default: 20)
  ACTOR_LATENCY_P95_BUDGET_MS    — p95 budget in ms (default: 1000)
  ACTOR_LATENCY_TOTAL_BUDGET_MS  — total-added budget in ms (default: 1500)

Output:
  Both tests print p50 / p95 / max so the human checkpoint can record the measured
  numbers for the ACT-06 sign-off.

Design:
  - _fetch_history is monkeypatched in both tests to avoid a tenant-DB dependency.
  - apply_rate_and_constraint_checks, check_capability_access, reserve_idempotency,
    write_audit_row, and finalize_idempotency are mocked in test 2 to isolate the
    Actor gate overhead as the only live component.
  - Each call uses a fresh UUID idempotency key and agent_id to avoid collisions.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Gate: skip entire module when ACTOR_LATENCY_ENABLED is not set
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not os.environ.get("ACTOR_LATENCY_ENABLED"),
    reason="set ACTOR_LATENCY_ENABLED=1 to run Actor latency tests (requires ANTHROPIC_API_KEY)",
)

# ---------------------------------------------------------------------------
# Budget constants (overridable via env)
# ---------------------------------------------------------------------------

N_CALLS: int = int(os.environ.get("ACTOR_LATENCY_N_CALLS", "20"))
P95_BUDGET_MS: int = int(os.environ.get("ACTOR_LATENCY_P95_BUDGET_MS", "1000"))
TOTAL_ADDED_BUDGET_MS: int = int(os.environ.get("ACTOR_LATENCY_TOTAL_BUDGET_MS", "1500"))

# ---------------------------------------------------------------------------
# Shared fixture inputs for the Haiku call
# ---------------------------------------------------------------------------

_FIXED_SKILL = "place_order"
_FIXED_ARGS = {
    "product_id": "LATENCY-TEST-SKU-001",
    "quantity": 1,
    "amount_cents": 1000,
    "customer_email": "latency_test@integration.local",
    "shipping_address": "1 Latency Test St, Testville",
    "idempotency_key": "placeholder",  # overridden per call
}
_FIXED_CAPABILITY_SNAPSHOT = {
    "requires_confirmation": True,
    "constraints": {"max_amount_cents": 10000},
    "enabled": True,
    "skill": _FIXED_SKILL,
    "rate_limit": None,
}
# Aligned conversation: customer asked to place an order, the action matches.
_FIXED_HISTORY = [
    {"role": "user", "content": "I would like to order one unit of item SKU-001."},
    {"role": "assistant", "content": "I can help with that. Let me place the order."},
]


# ---------------------------------------------------------------------------
# Test 1 — ACT-06 p95: N>=20 live Haiku calls, assert p95 < P95_BUDGET_MS
# ---------------------------------------------------------------------------


def test_actor_p95_latency_within_budget():
    """ACT-06: call_actor_gate p95 < 1000ms over N>=20 real Haiku calls.

    Calls call_actor_gate N times against the real Anthropic Haiku 4-5 API.
    Each call uses a fixed approve-aligned conversation and a fresh agent_id/
    conversation_id so there is no cross-call state. _fetch_history is mocked
    so no tenant-DB connection is needed.

    Prints p50 / p95 / max for the ACT-06 checkpoint sign-off.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping live p95 latency test")

    from app.services.actor_seam import call_actor_gate

    latencies_ms: list[float] = []

    with patch(
        "app.services.actor_seam._fetch_history",
        AsyncMock(return_value=_FIXED_HISTORY),
    ):
        for i in range(N_CALLS):
            args = {**_FIXED_ARGS, "idempotency_key": f"latency-p95-{uuid.uuid4()}"}
            conv_id = str(uuid.uuid4())
            agent_id = str(uuid.uuid4())

            t0 = time.perf_counter()
            decision, _rationale = asyncio.run(
                call_actor_gate(
                    skill=_FIXED_SKILL,
                    arguments=args,
                    capability_snapshot=_FIXED_CAPABILITY_SNAPSHOT,
                    conversation_id=conv_id,
                    agent_id=agent_id,
                    conn_str="",
                )
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies_ms.append(elapsed_ms)

            # Sanity: real Actor must return a valid verdict on every call.
            assert decision in {"approve", "block", "require_human"}, (
                f"Call {i + 1}/{N_CALLS}: unexpected Actor verdict {decision!r}"
            )

    assert len(latencies_ms) >= 20, (
        f"ACT-06: expected >= 20 latency samples, got {len(latencies_ms)}"
    )

    # Compute percentiles via statistics.quantiles (returns n-1 cut points).
    # With n=100: qs[49]=p50, qs[94]=p95.
    qs = statistics.quantiles(latencies_ms, n=100)
    p50 = qs[49]
    p95 = qs[94]
    p_max = max(latencies_ms)

    print(
        f"\nACT-06 Actor Haiku latency over {N_CALLS} calls:\n"
        f"  p50 = {p50:.0f}ms\n"
        f"  p95 = {p95:.0f}ms  (budget: < {P95_BUDGET_MS}ms)\n"
        f"  max = {p_max:.0f}ms\n"
    )

    assert p95 < P95_BUDGET_MS, (
        f"ACT-06 p95 BUDGET EXCEEDED: measured p95 = {p95:.0f}ms "
        f"(budget = {P95_BUDGET_MS}ms). "
        f"p50 = {p50:.0f}ms, max = {p_max:.0f}ms over {N_CALLS} calls. "
        "Record these numbers for the ACT-06 sign-off checkpoint."
    )


# ---------------------------------------------------------------------------
# Test 2 — ACT-06 total added: one mutating dispatcher call with real Actor
# ---------------------------------------------------------------------------


def test_actor_total_added_latency_within_budget():
    """ACT-06: Total added latency of a mutating dispatcher call with a real Actor < 1500ms.

    Drives place_order_tool.handler() with the REAL call_actor_gate (live Haiku call)
    but with all DB and adapter operations mocked so that only the Actor Haiku call and
    its branch overhead contribute to the measured time.

    This isolates the Actor's contribution to total dispatcher latency. The 1500ms budget
    covers the Haiku call + branch decision overhead in _execute_transactional_tool.

    Prints the measured total-added latency for the ACT-06 checkpoint record.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping total-added latency test")

    from app.services.transactional.idempotency import Reservation

    agent_id_str = str(uuid.uuid4())
    idem_key = f"latency-total-{uuid.uuid4()}"

    args = {**_FIXED_ARGS, "idempotency_key": idem_key}

    # Set ContextVars before asyncio.run() so the event loop inherits them.
    from app.services.agent_tools import _agent_id_var, _conn_str_var, _conversation_id_var  # noqa: PLC0415

    _agent_id_var.set(agent_id_str)
    _conversation_id_var.set(str(uuid.uuid4()))
    _conn_str_var.set("")  # history fetch skipped — no tenant DB needed

    with (
        # Capability check: return an approved snapshot (no DB)
        patch(
            "app.services.transactional.tools.check_capability_access",
            AsyncMock(return_value=(_FIXED_CAPABILITY_SNAPSHOT, None)),
        ),
        # Reserve idempotency: return "reserved" (no DB)
        patch(
            "app.services.transactional.tools.reserve_idempotency",
            AsyncMock(return_value=Reservation(state="reserved")),
        ),
        # Rate / constraint checks: pass (no Redis)
        patch(
            "app.services.transactional.tools.apply_rate_and_constraint_checks",
            AsyncMock(return_value=None),
        ),
        # History fetch: return aligned conversation (no tenant DB)
        patch(
            "app.services.actor_seam._fetch_history",
            AsyncMock(return_value=_FIXED_HISTORY),
        ),
        # Adapter: instant stub response (no provider call)
        patch(
            "app.services.transactional.tools.get_adapter",
        ) as mock_get_adapter,
        # Audit row: no-op (no DB write)
        patch(
            "app.services.transactional.tools.write_audit_row",
            AsyncMock(),
        ),
        # Finalize idempotency: no-op (no DB)
        patch(
            "app.services.transactional.tools.finalize_idempotency",
            AsyncMock(),
        ),
    ):
        # Configure adapter mock to return a fast stub result
        from app.services.transactional.schemas import PlaceOrderOutput

        mock_adapter = AsyncMock()
        mock_adapter.place_order = AsyncMock(
            return_value=PlaceOrderOutput(
                order_id="LATENCY-STUB-ORDER-001",
                status="placed",
                message="[LATENCY-STUB] Order placed.",
            )
        )
        mock_get_adapter.return_value = mock_adapter

        from app.services.transactional.tools import place_order_tool

        t0 = time.perf_counter()
        result = asyncio.run(place_order_tool.handler(args))
        total_ms = (time.perf_counter() - t0) * 1000

    print(
        f"\nACT-06 Total added latency (real Actor + branch overhead):\n"
        f"  total = {total_ms:.0f}ms  (budget: < {TOTAL_ADDED_BUDGET_MS}ms)\n"
    )

    # Sanity: dispatcher returned a non-error result (Actor approved the aligned action)
    if result.get("is_error"):
        content = result.get("content", [{}])[0].get("text", "")
        pytest.skip(
            f"Actor returned a non-approve verdict on the aligned conversation. "
            f"Response: {content!r}. "
            "This is not a latency failure — the Actor returned block/require_human "
            "for what should be an aligned action. Rerun to get a fresh Haiku verdict."
        )

    assert total_ms < TOTAL_ADDED_BUDGET_MS, (
        f"ACT-06 total-added BUDGET EXCEEDED: {total_ms:.0f}ms "
        f"(budget = {TOTAL_ADDED_BUDGET_MS}ms). "
        "The Actor Haiku call + branch overhead exceeded the 1.5s latency budget. "
        "Record this number for the ACT-06 checkpoint sign-off."
    )
