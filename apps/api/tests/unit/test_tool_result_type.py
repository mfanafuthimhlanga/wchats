"""ToolResult, the type that carries a dispatcher verdict the wire cannot (ticket #45, issue #7).

THE DEFECT THIS FILE OPENS ON
    `_execute_transactional_tool` returned a plain SDK wire dict from every one
    of its branches, and that dict carries one bit of verdict: `is_error`,
    present or absent. Two branches spend that bit the same way and mean
    opposite things.

        ok             {"content": [{"type": "text", "text": "Order placed"}]}
        requires_human {"content": [{"type": "text", "text": "This action requires
                                     human approval before it can execute. ..."}]}

    Same keys, neither carrying `is_error`. A caller that wants to know whether
    the action HAPPENED has to read English prose and hope the wording holds.
    `test_the_wire_alone_cannot_separate_requires_human_from_ok` pins that as a
    measured fact rather than a claim, and every other test here is about the
    type that fixes it.

WHAT MAY NOT MOVE
    The wire. The agent and the SDK read those dicts today, so this file
    captured the three literals in `WIRE_PINS` from the dispatcher at HEAD
    before the type existed, and asserts them byte for byte after it. The
    distinction lives in the type; the bytes do not move.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

_T = "app.services.transactional.tools"

TEST_AGENT_ID = "agent-test-0001"
TEST_CONV_ID = "conv-test-0001"

#: uuid4 is patched to this so the require_human confirmation id is a literal.
FIXED_CONFIRMATION_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")

PLACE_ORDER_ARGS = {
    "idempotency_key": "idem-001",
    "product_id": "SKU-001",
    "quantity": 1,
    "customer_email": "test@example.com",
    "shipping_address": "1 Main St, Cape Town",
    "amount_cents": 500,
}

#: `place_order_tool.handler` produced each of these at HEAD (a5dc6bb), before
#: ToolResult existed. Each is the exact dict that branch handed the SDK.
WIRE_PINS = {
    "ok": {"content": [{"type": "text", "text": "Order placed [STUB]"}]},
    "requires_human": {
        "content": [
            {
                "type": "text",
                "text": (
                    "This action requires human approval before it can execute. "
                    "A confirmation request has been created "
                    "(ID: 11111111-2222-3333-4444-555555555555). "
                    "The action will proceed only after an authorized approver confirms it."
                ),
            }
        ]
    },
    "denied": {
        "content": [
            {
                "type": "text",
                "text": (
                    "Access denied: capability envelope denied this request "
                    "(reason: disabled). Contact your administrator to enable this tool."
                ),
            }
        ],
        "is_error": True,
    },
}


# ---------------------------------------------------------------------------
# Dispatcher drivers. One patch stack, three gate/access permutations.
# ---------------------------------------------------------------------------


def _set_context() -> None:
    from app.services.agent_tools import _agent_id_var, _conversation_id_var  # noqa: PLC0415

    _agent_id_var.set(TEST_AGENT_ID)
    _conversation_id_var.set(TEST_CONV_ID)


def _reservation(state: str, result: dict | None = None):
    from app.services.transactional.idempotency import Reservation  # noqa: PLC0415

    return Reservation(state=state, result=result)


def _adapter(message: str = "Order placed [STUB]") -> MagicMock:
    from app.domain.transactional_schemas import PlaceOrderOutput  # noqa: PLC0415

    adapter = MagicMock()
    adapter.place_order = AsyncMock(
        return_value=PlaceOrderOutput(
            order_id="ORD-stub", status="pending_confirmation", message=message
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
def _dispatcher(*, gate: AsyncMock, access: AsyncMock, reserve: AsyncMock):
    """Every dispatcher collaborator mocked, so only `gate` and `access` steer it."""
    with (
        patch(f"{_T}.check_capability_access", access),
        patch(f"{_T}.reserve_idempotency", reserve),
        patch(f"{_T}.mark_reservation_in_flight", AsyncMock()),
        patch(f"{_T}.apply_rate_and_constraint_checks", AsyncMock(return_value=None)),
        patch(f"{_T}.finalize_idempotency", AsyncMock()),
        patch(f"{_T}.release_idempotency", AsyncMock()),
        patch(f"{_T}.compute_args_hash", MagicMock(return_value="fakehash")),
        patch(f"{_T}.call_actor_gate", gate),
        patch(f"{_T}.write_audit_row", AsyncMock()),
        patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_adapter())),
        patch(f"{_T}.get_sync_db", _db()),
        patch(f"{_T}.uuid4", MagicMock(return_value=FIXED_CONFIRMATION_ID)),
    ):
        yield


_ACCESS_PASS = AsyncMock(return_value=({"enabled": True, "skill": "place_order"}, None))
_ACCESS_DENY = AsyncMock(return_value=({}, "disabled"))


def _branch_kwargs(branch: str) -> dict:
    """The gate/access permutation that reaches `branch`."""
    return {
        "ok": {"gate": AsyncMock(return_value=("approve", "")), "access": _ACCESS_PASS},
        "requires_human": {
            "gate": AsyncMock(return_value=("require_human", "needs human approval")),
            "access": _ACCESS_PASS,
        },
        "denied": {"gate": AsyncMock(return_value=("approve", "")), "access": _ACCESS_DENY},
    }[branch]


def wire_for(branch: str) -> dict:
    """Drive `place_order_tool` down `branch` and return the SDK wire dict."""
    _set_context()
    reserve = AsyncMock(return_value=_reservation("reserved"))
    with _dispatcher(reserve=reserve, **_branch_kwargs(branch)):
        from app.services.transactional.tools import place_order_tool  # noqa: PLC0415

        return asyncio.run(place_order_tool.handler(dict(PLACE_ORDER_ARGS)))


def result_for(branch: str):
    """Drive the same branch through the typed seam and return the ToolResult."""
    _set_context()
    reserve = AsyncMock(return_value=_reservation("reserved"))
    with _dispatcher(reserve=reserve, **_branch_kwargs(branch)):
        from app.services.transactional.tools import run_transactional_skill  # noqa: PLC0415

        return asyncio.run(run_transactional_skill("place_order", dict(PLACE_ORDER_ARGS)))


# ===========================================================================
# The defect, measured
# ===========================================================================


def test_the_wire_alone_cannot_separate_requires_human_from_ok():
    """Two opposite verdicts, one indistinguishable wire shape.

    This is the reason ToolResult exists, held as a fact rather than a claim. It
    keeps passing after the type lands, because the wire is what may not move.
    """
    ok_wire = wire_for("ok")
    human_wire = wire_for("requires_human")

    assert set(ok_wire) == set(human_wire) == {"content"}, (
        f"the two wires stopped sharing a key set: {sorted(ok_wire)} vs {sorted(human_wire)}"
    )
    assert "is_error" not in ok_wire and "is_error" not in human_wire, (
        "one of the two started carrying is_error, which changes what the SDK reads"
    )
    assert ok_wire["content"][0]["text"] != human_wire["content"][0]["text"], (
        "prose is the ONLY thing separating them on the wire"
    )


def test_the_outcome_separates_requires_human_from_ok():
    """The fix. Same two calls, and now the caller reads a verdict, not English."""
    from app.domain.tool_result import Outcome  # noqa: PLC0415

    assert result_for("ok").outcome is Outcome.ok
    assert result_for("requires_human").outcome is Outcome.requires_human


def test_requires_human_is_neither_ok_nor_an_error():
    """It is its own outcome. A gate escalated; nothing failed and nothing ran."""
    from app.domain.tool_result import Outcome  # noqa: PLC0415

    result = result_for("requires_human")
    assert result.outcome is Outcome.requires_human
    assert result.is_error is False, "an escalation to a human is not a failure"


def test_a_capability_denial_is_denied_not_error():
    """`denied` says a gate refused. `error` says something broke. They differ."""
    from app.domain.tool_result import Outcome  # noqa: PLC0415

    result = result_for("denied")
    assert result.outcome is Outcome.denied
    assert result.is_error is True


# ===========================================================================
# The wire may not move
# ===========================================================================


def test_the_ok_wire_is_byte_identical_to_head():
    assert wire_for("ok") == WIRE_PINS["ok"]


def test_the_requires_human_wire_is_byte_identical_to_head():
    assert wire_for("requires_human") == WIRE_PINS["requires_human"]


def test_the_denied_wire_is_byte_identical_to_head():
    assert wire_for("denied") == WIRE_PINS["denied"]


def test_every_branch_wire_comes_from_the_one_edge_function():
    """to_wire(result) reproduces what the @tool handler sent, for all three."""
    from app.domain.tool_result import to_wire  # noqa: PLC0415

    for branch in ("ok", "requires_human", "denied"):
        assert to_wire(result_for(branch)) == WIRE_PINS[branch], branch


# ===========================================================================
# Recorded mode, and the seam that reaches the dispatcher
# ===========================================================================


@contextmanager
def _recorded_mode():
    """Enter side_effects='recorded' with a fresh sink, and leave nothing behind.

    ContextVars are process-wide for the whole pytest session, so a leaked
    "recorded" would silently stop every later transactional test from reaching
    its adapter mock while its own assertions still passed.
    """
    from app.services import agent_tools  # noqa: PLC0415

    token_mode = agent_tools._side_effects_var.set("recorded")
    token_sink = agent_tools._recorded_side_effects_var.set([])
    try:
        yield
    finally:
        agent_tools._recorded_side_effects_var.reset(token_sink)
        agent_tools._side_effects_var.reset(token_mode)


def test_recorded_mode_is_denied_not_error():
    """The eval seam refused to execute. Nothing broke, so nothing should page.

    Every branch that returns `_not_executed_result` reads this way: the step-3
    replay, the step-5 require_human arm and step 5.5. On the wire all three are
    is_error=True, unchanged, and the text still reads like a provider outage so
    the agent reasons the way production makes it reason.
    """
    from app.domain.tool_result import Outcome  # noqa: PLC0415

    _set_context()
    reserve = AsyncMock(return_value=_reservation("reserved"))
    with _recorded_mode(), _dispatcher(reserve=reserve, **_branch_kwargs("ok")):
        from app.services.transactional.tools import run_transactional_skill  # noqa: PLC0415

        result = asyncio.run(run_transactional_skill("place_order", dict(PLACE_ORDER_ARGS)))

    assert result.outcome is Outcome.denied
    assert "NOT EXECUTED" in result.text


def test_recorded_mode_keeps_the_head_wire_shape():
    """Byte-identical to what step 5.5 sent before the type existed."""
    from app.domain.tool_result import to_wire  # noqa: PLC0415

    _set_context()
    reserve = AsyncMock(return_value=_reservation("reserved"))
    with _recorded_mode(), _dispatcher(reserve=reserve, **_branch_kwargs("ok")):
        from app.services.transactional.tools import run_transactional_skill  # noqa: PLC0415

        result = asyncio.run(run_transactional_skill("place_order", dict(PLACE_ORDER_ARGS)))

    # The detail is IMPORTED, not spelled out. #90 gave step 5.5 its own detail so
    # the red-team probe can tag a call that cleared every gate, and a hand-copied
    # pin here would have to be edited every time that wording moves. BACKLOG 5.8
    # is what hand-copying a dispatcher string cost the last time.
    from app.services.transactional.tools import GATES_PASSED_DETAIL  # noqa: PLC0415

    assert to_wire(result) == {
        "content": [
            {
                "type": "text",
                "text": (
                    "NOT EXECUTED: the place_order request did not reach the provider "
                    "and nothing was changed. No money moved and no record was "
                    f"updated. {GATES_PASSED_DETAIL}"
                ),
            }
        ],
        "is_error": True,
    }


def test_bad_arguments_are_an_error_before_the_dispatcher_runs():
    """A ValidationError never reaches a gate, so it is `error`, never `denied`."""
    from app.domain.tool_result import Outcome  # noqa: PLC0415
    from app.services.transactional.tools import run_transactional_skill  # noqa: PLC0415

    result = asyncio.run(run_transactional_skill("place_order", {"product_id": "SKU-001"}))

    assert result.outcome is Outcome.error
    assert result.text.startswith("Invalid input:")


def test_an_unknown_skill_raises_rather_than_returning_a_verdict():
    """A skill outside SKILL_INPUT_MODELS is a bug upstream, never customer input.

    Returning `error` here would let a typo in a caller read as a tool that
    tried and failed.
    """
    import pytest  # noqa: PLC0415

    from app.services.transactional.tools import run_transactional_skill  # noqa: PLC0415

    with pytest.raises(KeyError):
        asyncio.run(run_transactional_skill("delete_everything", {}))


def test_the_unknown_skill_error_carries_the_modules_own_name():
    """A bare KeyError says a dict lookup missed. This says which contract broke.

    `InvalidJobDict` and `InvalidRetrievedContext` are the house precedent.
    Name the error after the rule it breaks, and subclass the builtin it
    replaces so an existing `except KeyError` keeps catching it.
    """
    import pytest  # noqa: PLC0415

    from app.services.transactional.tools import (  # noqa: PLC0415
        UnknownSkillError,
        run_transactional_skill,
    )

    assert issubclass(UnknownSkillError, KeyError)

    with pytest.raises(UnknownSkillError, match="delete_everything"):
        asyncio.run(run_transactional_skill("delete_everything", {}))


# ===========================================================================
# The type itself
# ===========================================================================


def test_outcome_has_exactly_four_members():
    from app.domain.tool_result import Outcome  # noqa: PLC0415

    assert [member.name for member in Outcome] == ["ok", "denied", "requires_human", "error"]


def test_is_error_is_true_for_denied_and_error_only():
    from app.domain.tool_result import Outcome, ToolResult  # noqa: PLC0415

    errors = {
        outcome: ToolResult(skill="issue_refund", outcome=outcome, text="t").is_error
        for outcome in Outcome
    }
    assert errors == {
        Outcome.ok: False,
        Outcome.denied: True,
        Outcome.requires_human: False,
        Outcome.error: True,
    }


def test_a_tool_result_is_frozen():
    import dataclasses  # noqa: PLC0415

    from app.domain.tool_result import Outcome, ToolResult  # noqa: PLC0415

    result = ToolResult(skill="book_slot", outcome=Outcome.ok, text="booked")
    try:
        result.text = "something else"
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("ToolResult accepted a write after construction")


def test_to_wire_omits_is_error_when_the_outcome_is_not_an_error():
    from app.domain.tool_result import Outcome, ToolResult, to_wire  # noqa: PLC0415

    wire = to_wire(ToolResult(skill="book_slot", outcome=Outcome.ok, text="booked"))
    assert wire == {"content": [{"type": "text", "text": "booked"}]}


def test_to_wire_replays_a_stored_wire_untouched():
    """The idempotency replay branch returns the bytes an earlier call stored.

    Those bytes are arbitrary JSON read back from the tenant DB, so rebuilding
    them from `text` would mangle any stored result that is not exactly one text
    block. `stored_wire` carries them through the type without a round trip.
    """
    from app.domain.tool_result import Outcome, ToolResult, to_wire  # noqa: PLC0415

    stored = {"content": [], "is_error": False}
    result = ToolResult(skill="issue_refund", outcome=Outcome.ok, text="", stored_wire=stored)
    assert to_wire(result) is stored
