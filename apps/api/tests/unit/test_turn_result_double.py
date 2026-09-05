"""`tests.agent_loop_doubles` is pinned to the real loop, or it drifts again.

THE DEFECT THIS PINS. Six test files each held their own literal of what
`run_agent_loop` returns. #50 added four `pii_` keys to `_turn_result` and every
one of those literals went stale in the same instant. Four files turned red, for
a reason that appeared in none of the failing test bodies; the fifth
(`test_eval_agent_invocation.py`) stayed GREEN on the identical stale dict,
because the eval path does not happen to read `pii_detector` yet. The green one
was the worse defect: an eval measured against a turn shape production stopped
producing.

Collapsing the six literals into one helper removes five of those copies and
none of the drift. The helper is still a hand-written guess at another
function's return, and the seventh key added to `_turn_result` would leave it
wrong in exactly the same way, only now in one place instead of six.

So this drives the REAL `run_agent_loop` over a scripted client and compares key
sets. A key added to `_turn_result` turns this one test red, and the message
names the helper to change.

WHY KEY SETS AND NOT VALUES. `num_turns`, `stop_reason` and `tool_calls_log`
differ legitimately per test — that is what the helper's `**overrides` are for,
and a value pin here would forbid the escalation and budget-exhausted fakes the
other files need. `pii_original_length` is not comparable either: on the
deflected turn below the real loop reports the length of the text BEFORE the
scan, which the served text no longer is. What every caller relies on is the
presence of a key, because that is what `run_agent_turn` subscripts.

THE CONTROL IS THE SECOND DRIVE. "Both key sets match" is satisfied by a harness
that never reached the firewall at all, so the leaking answer runs through the
same loop and must come back deflected. That also says the branch which SETS
`pii_detector` returns the same keys as the branch that leaves it None.

THE SECOND TEST IS ABOUT ONE VALUE, NOT THE KEYS. `stop_reason` was the key set's
blind spot: every double carried it, so no key ever went missing, and three of
them carried Anthropic's `end_turn`, which the loop has not emitted since #49
(issue #107). The helper now refuses a word outside `STOP_REASONS`, and the test
below is the observation that the refusal fires.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.model_client import route_for
from app.domain.pii_firewall import PII_DEFLECTION
from app.services.agent_loop import (
    MAX_MODEL_CALLS_PER_TURN,
    AgentTurn,
    run_agent_loop,
)
from tests.agent_loop_doubles import STOP_REASONS, canned_turn_result

JOB_ID = "33333333-3333-3333-3333-333333333333"
QUESTION = "Did my refund go through?"


async def _no_emit(*args, **kwargs) -> None:
    """The loop's event sink, silenced. A coroutine since #86 made it awaited."""
    return None

#: Answers the model gives. One the firewall passes through, one it deflects.
CLEAN_ANSWER = "Yes, it was returned to your original payment method."
CUSTOMER_ADDRESS = "jane.smith@gmail.example"
LEAKING_ANSWER = f"Yes, the confirmation went to {CUSTOMER_ADDRESS} this morning."


class _Client:
    """One scripted completion. No tool call, so the turn is a single call."""

    def __init__(self, answer: str) -> None:
        message = SimpleNamespace(content=answer, tool_calls=None)
        self._reply = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")]
        )
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **_kwargs):
        return self._reply

    async def close(self) -> None:
        return None


def _real_turn_result(answer: str) -> dict:
    """What `run_agent_loop` actually returns, driven as `run_agent_turn` drives it.

    Only the provider client is replaced, and it is the one boundary that costs
    money. The loop, the seam's exit and the firewall inside it all run.
    """
    turn = AgentTurn(
        client=_Client(answer),
        route=route_for("agent_turn"),
        system_prompt="you are a refunds specialist",
        tools=(),
        max_model_calls=MAX_MODEL_CALLS_PER_TURN,
        max_budget_usd=1.0,
        calls=[],
        ledger=lambda call: None,
    )
    with patch("app.services.agent_loop.emit_async", new=_no_emit):
        return asyncio.run(
            run_agent_loop(
                QUESTION,
                history=[],
                turn=turn,
                job_id=JOB_ID,
                db=MagicMock(),
                redis=MagicMock(),
            )
        )


def test_the_shared_double_carries_every_key_the_real_turn_returns() -> None:
    """`canned_turn_result` and `_turn_result` return the same key set."""
    clean = _real_turn_result(CLEAN_ANSWER)
    deflected = _real_turn_result(LEAKING_ANSWER)

    assert clean["pii_detector"] is None, (
        "the clean answer came back scanned as PII, so this harness is not "
        "driving the turn the doubles stand in for"
    )
    assert deflected["response_text"] == PII_DEFLECTION, (
        f"the leaking answer was served as {deflected['response_text']!r}. The "
        "firewall did not run, so a matching key set below would say nothing "
        "about the branch that sets `pii_detector`."
    )

    double = set(canned_turn_result(CLEAN_ANSWER))
    for name, real in (("clean", clean), ("deflected", deflected)):
        missing = sorted(set(real) - double)
        extra = sorted(double - set(real))
        assert not missing and not extra, (
            f"`run_agent_loop` and tests.agent_loop_doubles.canned_turn_result "
            f"disagree on the {name} turn.\n"
            f"  the real return has, and the double does not: {missing}\n"
            f"  the double has, and the real return does not: {extra}\n"
            "Every test that fakes this dict fakes it through that helper, so "
            "add the key there with a default describing a clean turn. Do NOT "
            "soften the `result[...]` subscripts in "
            "app/worker/tasks/runtime/agent.py to make them tolerate a missing "
            "key: a turn whose seam did not scan is a turn nobody may serve."
        )


def test_the_double_hands_out_the_word_the_real_turn_recorded() -> None:
    """The helper's default `stop_reason` is what the loop put in the dict."""
    real = _real_turn_result(CLEAN_ANSWER)

    assert real["stop_reason"] in STOP_REASONS, (
        f"the loop recorded {real['stop_reason']!r}, which STOP_REASONS does not "
        "hold. tests/unit/test_agent_loop.py drives every ending against that set "
        "and is the test to read first."
    )
    assert canned_turn_result(CLEAN_ANSWER)["stop_reason"] == real["stop_reason"]


def test_the_double_refuses_a_word_the_loop_cannot_record() -> None:
    """`end_turn` is issue #107 itself, and the helper is where it is caught.

    Three call sites passed it as an override and a fourth asserted the
    turn_metrics row held it. Every one of those was green, because the double
    is what the assertion read. The override is checked rather than the default,
    since the default was never the wrong one.
    """
    with pytest.raises(ValueError) as refused:
        canned_turn_result(CLEAN_ANSWER, stop_reason="end_turn")

    assert "end_turn" in str(refused.value)

    # The positive half: a word the loop does emit still goes through, so the
    # guard is not simply refusing every override.
    exhausted = canned_turn_result(CLEAN_ANSWER, stop_reason="max_model_calls")
    assert exhausted["stop_reason"] == "max_model_calls"
