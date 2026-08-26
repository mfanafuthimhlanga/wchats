"""One table per dispatcher function, driving every branch and reading its Outcome.

WHY THIS FILE EXISTS
    `_execute_transactional_tool` returns from sixteen places. Fourteen refuse
    the call and two let it through, and until this table existed a mapping
    could be flipped from `denied` to `error`, or back, without a single test
    going red. The suite covered the three branches `ToolResult` was written for
    and left the other thirteen to the reader.

    The outcome is not decoration. `denied` means a gate said no and `error`
    means something broke, so the two answer different questions: whether to
    page an engineer, and whether the red-team probe counts an attack as
    stopped. A branch that drifts between them changes both answers silently.

WHAT THE TABLES HOLD
    `DISPATCHER_BRANCHES` names every branch `_execute_transactional_tool`
    returns from, and `ADAPTER_BRANCHES` does the same for
    `_execute_adapter_and_audit`, which the dispatcher delegates steps 6 and 7
    to and which the human-approval resolver calls on its own. Each row carries
    the smallest set of fakes that reaches its branch and the outcome that
    branch owes.

    Every branch is drivable here. The one sub-branch the tables fold into its
    parent is the duplicate suppression inside `actor_require_human`. A
    pre-existing unresolved row and a lost unique-index race both return the
    same `requires_human` verdict the fresh row returns, so they are one
    outcome rather than three.

Every boundary is mocked. No Postgres, no Redis, no provider call.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.tool_result import Outcome

_T = "app.services.transactional.tools"

TEST_AGENT_ID = "agent-branch-0001"
TEST_CONV_ID = "conv-branch-0001"

PLACE_ORDER_ARGS = {
    "idempotency_key": "idem-branch-001",
    "product_id": "SKU-001",
    "quantity": 1,
    "customer_email": "test@example.com",
    "shipping_address": "1 Main St, Cape Town",
    "amount_cents": 500,
}

#: The step-2 snapshot every branch reads, and the one that turns the IDV gate on.
PASS_SNAPSHOT = {"enabled": True, "skill": "place_order"}
IDV_SNAPSHOT = {"enabled": True, "skill": "place_order", "requires_identity_verification": True}

#: What an earlier completed call stored, which the replay branch hands back.
STORED_WIRE = {"content": [{"type": "text", "text": "Order placed [STUB]"}]}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _reservation(state: str, result: dict | None = None):
    from app.services.transactional.idempotency import Reservation  # noqa: PLC0415

    return Reservation(state=state, result=result)


def _adapter(raises: bool = False) -> MagicMock:
    from app.domain.transactional_schemas import PlaceOrderOutput  # noqa: PLC0415

    adapter = MagicMock()
    if raises:
        adapter.place_order = AsyncMock(side_effect=RuntimeError("provider timed out"))
    else:
        adapter.place_order = AsyncMock(
            return_value=PlaceOrderOutput(
                order_id="ORD-stub",
                status="pending_confirmation",
                message="Order placed [STUB]",
            )
        )
    return adapter


def _db():
    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

    @contextmanager
    def _ctx():
        yield session

    return _ctx


@contextmanager
def _dispatcher_context(*, agent_id: str, recorded: bool, vst: str):
    """Set the per-call ContextVars the dispatcher reads, and reset every one.

    ContextVars live for the whole pytest session, so a leaked "recorded" would
    stop every later transactional test from reaching its adapter mock while its
    own assertions still passed.
    """
    from app.services import agent_tools  # noqa: PLC0415

    wanted = [
        (agent_tools._agent_id_var, agent_id),
        (agent_tools._conversation_id_var, TEST_CONV_ID),
        (agent_tools._conn_str_var, "postgresql://unused"),
        (agent_tools._verified_session_token_var, vst),
        (agent_tools._side_effects_var, "recorded" if recorded else "live"),
        (agent_tools._recorded_side_effects_var, []),
    ]
    tokens = [(var, var.set(value)) for var, value in wanted]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def _drive(
    *,
    agent_id: str = TEST_AGENT_ID,
    snapshot: dict | None = None,
    denial: str | None = None,
    vst: str = "",
    idv_valid: bool = True,
    idv_raises: bool = False,
    reservation: str = "reserved",
    stored: dict | None = None,
    rate_denial: str | None = None,
    decision: str = "approve",
    recorded: bool = False,
):
    """Run the dispatcher with exactly the fakes one branch needs."""
    access = AsyncMock(return_value=(snapshot or PASS_SNAPSHOT, denial))
    reserve = AsyncMock(return_value=_reservation(reservation, stored))
    identity = (
        AsyncMock(side_effect=RuntimeError("neon cold start"))
        if idv_raises
        else AsyncMock(return_value=idv_valid)
    )

    with (
        _dispatcher_context(agent_id=agent_id, recorded=recorded, vst=vst),
        patch(f"{_T}.check_capability_access", access),
        patch(f"{_T}.reserve_idempotency", reserve),
        patch(f"{_T}.mark_reservation_in_flight", AsyncMock()),
        patch(f"{_T}.apply_rate_and_constraint_checks", AsyncMock(return_value=rate_denial)),
        patch(f"{_T}.finalize_idempotency", AsyncMock()),
        patch(f"{_T}.release_idempotency", AsyncMock()),
        patch(f"{_T}.compute_args_hash", MagicMock(return_value="fakehash")),
        patch(f"{_T}.call_actor_gate", AsyncMock(return_value=(decision, "because"))),
        patch(f"{_T}.write_audit_row", AsyncMock()),
        patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_adapter())),
        patch(f"{_T}.get_sync_db", _db()),
        patch("app.services.identity_service.check_verified_session", identity),
    ):
        from app.services.transactional.tools import run_transactional_skill  # noqa: PLC0415

        return asyncio.run(run_transactional_skill("place_order", dict(PLACE_ORDER_ARGS)))


def _drive_adapter(*, adapter_exc: Exception | None = None, adapter_raises: bool = False):
    """Call `_execute_adapter_and_audit` directly, the way the resolver calls it."""
    from app.domain.transactional_schemas import PlaceOrderInput  # noqa: PLC0415

    get_adapter = (
        AsyncMock(side_effect=adapter_exc)
        if adapter_exc is not None
        else AsyncMock(return_value=_adapter(raises=adapter_raises))
    )

    with (
        patch(f"{_T}.mark_reservation_in_flight", AsyncMock()),
        patch(f"{_T}.finalize_idempotency", AsyncMock()),
        patch(f"{_T}.release_idempotency", AsyncMock()),
        patch(f"{_T}.write_audit_row", AsyncMock()),
        patch(f"{_T}.get_adapter_for_skill", get_adapter),
    ):
        from app.services.transactional.tools import (  # noqa: PLC0415
            _execute_adapter_and_audit,
        )

        return asyncio.run(
            _execute_adapter_and_audit(
                skill="place_order",
                validated=PlaceOrderInput(**PLACE_ORDER_ARGS),
                raw_args=dict(PLACE_ORDER_ARGS),
                adapter_method="place_order",
                agent_id=TEST_AGENT_ID,
                conn_str="postgresql://unused",
                conversation_id=TEST_CONV_ID,
                snapshot=PASS_SNAPSHOT,
                decision="approve",
                rationale="because",
            )
        )


# ---------------------------------------------------------------------------
# The tables
# ---------------------------------------------------------------------------

DISPATCHER_BRANCHES: list[tuple[str, dict, Outcome]] = [
    ("agent_id_missing", {"agent_id": ""}, Outcome.error),
    ("capability_denied", {"denial": "disabled"}, Outcome.denied),
    ("identity_token_absent", {"snapshot": IDV_SNAPSHOT}, Outcome.denied),
    (
        "identity_check_failed",
        {"snapshot": IDV_SNAPSHOT, "vst": "token-1", "idv_raises": True},
        Outcome.error,
    ),
    (
        "identity_token_expired",
        {"snapshot": IDV_SNAPSHOT, "vst": "token-1", "idv_valid": False},
        Outcome.denied,
    ),
    ("idempotency_replay", {"reservation": "replay", "stored": STORED_WIRE}, Outcome.ok),
    (
        "idempotency_replay_recorded",
        {"reservation": "replay", "stored": STORED_WIRE, "recorded": True},
        Outcome.denied,
    ),
    ("idempotency_args_mismatch", {"reservation": "args_mismatch"}, Outcome.denied),
    ("idempotency_in_progress", {"reservation": "in_progress"}, Outcome.denied),
    ("idempotency_stranded", {"reservation": "unknown"}, Outcome.error),
    ("rate_or_constraint_denied", {"rate_denial": "rate_limit"}, Outcome.denied),
    ("actor_block", {"decision": "block"}, Outcome.denied),
    ("actor_require_human", {"decision": "require_human"}, Outcome.requires_human),
    (
        "actor_require_human_recorded",
        {"decision": "require_human", "recorded": True},
        Outcome.denied,
    ),
    ("recorded_mode_suppressed_the_adapter", {"recorded": True}, Outcome.denied),
    ("adapter_ran", {}, Outcome.ok),
]

ADAPTER_BRANCHES: list[tuple[str, dict, Outcome]] = [
    ("provider_not_configured", {"adapter_exc": "provider"}, Outcome.error),
    ("credential_would_not_decrypt", {"adapter_exc": "credential"}, Outcome.error),
    ("adapter_raised", {"adapter_raises": True}, Outcome.error),
    ("adapter_returned", {}, Outcome.ok),
]


def _adapter_exception(name: str) -> Exception:
    from app.services.transactional.credential_service import (  # noqa: PLC0415
        CredentialDecryptionError,
        ProviderNotConfiguredError,
    )

    if name == "provider":
        return ProviderNotConfiguredError("No integration credential configured")
    return CredentialDecryptionError("Failed to decrypt credential")


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "branch,fakes,expected",
    DISPATCHER_BRANCHES,
    ids=[row[0] for row in DISPATCHER_BRANCHES],
)
def test_every_dispatcher_branch_returns_the_outcome_it_owes(branch, fakes, expected):
    result = _drive(**fakes)

    assert result.outcome is expected, (
        f"{branch} returned {result.outcome.value}, expected {expected.value}. "
        f"Text: {result.text!r}"
    )


@pytest.mark.parametrize(
    "branch,fakes,expected",
    ADAPTER_BRANCHES,
    ids=[row[0] for row in ADAPTER_BRANCHES],
)
def test_every_adapter_branch_returns_the_outcome_it_owes(branch, fakes, expected):
    fakes = dict(fakes)
    if "adapter_exc" in fakes:
        fakes["adapter_exc"] = _adapter_exception(fakes["adapter_exc"])

    result = _drive_adapter(**fakes)

    assert result.outcome is expected, (
        f"{branch} returned {result.outcome.value}, expected {expected.value}. "
        f"Text: {result.text!r}"
    )


@pytest.mark.parametrize("branch", ["idempotency_args_mismatch", "idempotency_in_progress"])
def test_the_two_remapped_branches_send_the_same_bytes_they_sent_before(branch):
    """These two moved from `error` to `denied`, and the wire did not move with them.

    `denied` and `error` spend the same bit, so the SDK dict is what it was.
    The agent reads the same text and the same is_error it read at HEAD, which
    is what makes this remap a change to what the SYSTEM knows and nothing else.
    """
    from app.domain.tool_result import to_wire  # noqa: PLC0415

    fakes = next(dict(row[1]) for row in DISPATCHER_BRANCHES if row[0] == branch)

    result = _drive(**fakes)

    assert to_wire(result) == {
        "content": [{"type": "text", "text": result.text}],
        "is_error": True,
    }


def test_the_table_drives_every_reservation_state():
    """A sixth state added to `Reservation` arrives here with no row to sit in."""
    driven = {fakes.get("reservation", "reserved") for _, fakes, _ in DISPATCHER_BRANCHES}

    assert driven == {"reserved", "replay", "in_progress", "args_mismatch", "unknown"}


def test_the_table_drives_every_actor_verdict():
    """The Actor seam returns one of three, and each one leaves by its own branch."""
    driven = {fakes.get("decision", "approve") for _, fakes, _ in DISPATCHER_BRANCHES}

    assert driven == {"approve", "block", "require_human"}


def test_a_refusal_and_a_fault_never_share_an_outcome():
    """The distinction the table exists to hold, stated once as a rule.

    Every branch a gate owns returns `denied`, every branch a fault owns returns
    `error`, and the wire cannot tell the two apart because both spend
    `is_error`.
    """
    outcomes = {branch: expected for branch, _, expected in DISPATCHER_BRANCHES}

    refusals = {
        "capability_denied",
        "identity_token_absent",
        "identity_token_expired",
        "idempotency_args_mismatch",
        "idempotency_in_progress",
        "idempotency_replay_recorded",
        "rate_or_constraint_denied",
        "actor_block",
        "actor_require_human_recorded",
        "recorded_mode_suppressed_the_adapter",
    }
    faults = {
        "agent_id_missing",
        "identity_check_failed",
        "idempotency_stranded",
    }

    assert {outcomes[name] for name in refusals} == {Outcome.denied}
    assert {outcomes[name] for name in faults} == {Outcome.error}
