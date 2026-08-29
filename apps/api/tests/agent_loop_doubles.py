"""What `run_agent_loop` hands back, in ONE place.

THE DEFECT THIS CLOSES. Six test files each carried their own hand-copied
literal of the loop's return dict. #50 added four `pii_` keys to
`agent_loop._turn_result`, and `app.worker.tasks.runtime.agent` reads
`result["pii_detector"]` by subscript on purpose: a turn whose seam did not scan
is a turn nobody may serve, so the read stays fail-loud and the doubles are what
move. Fifteen tests went red for one reason, none of them in the body of the
test that failed, and a sixteenth stayed GREEN on the same drifted dict because
the eval path happens not to read that key yet. A green test on a drifted double
is the defect; the red ones only made it visible.

A helper module rather than a `conftest.py` fixture, because four of the call
sites bind this at MODULE level (`_CANNED_TURN_RESULT = ...`) and a fixture
cannot be read there. Same shape and same import path as `tests.model_doubles`,
which already carries the doubles for the model-client factory.

THE DEFAULTS DESCRIBE A CLEAN TURN, which is what almost every fake is. The
firewall served the model's own words, so `pii_detector` is None; no published
chunk exempted anything, so the count is 0 and the exemption False; and
`pii_original_length` is the length of the text as it stood BEFORE the scan,
which for a clean turn is the text itself. A test about a deflected turn passes
`pii_detector=`, one about an exhausted budget passes `stop_reason=`, and it
says nothing else.

The parity between this key set and the real one is not asserted here.
tests/unit/test_turn_result_double.py drives the real loop over a scripted
client and compares key sets, so the next key added to `_turn_result` turns one
named test red instead of leaving six doubles quietly wrong.

`stop_reason` is the one VALUE this module polices, because it is the one a
caller may override with a word production cannot produce. STOP_REASONS below
holds the vocabulary and `canned_turn_result` refuses anything outside it.
"""

from __future__ import annotations

#: Every word `run_agent_loop` can put in `stop_reason`, and nothing else.
#:
#: Two sources. The loop names three endings of its own: it ran out of model
#: calls, it ran out of budget, or the reply carried no choices to read. For
#: every other ending it passes the provider's `finish_reason` straight through,
#: and since #49 that provider is OpenAI, whose chat completions send `stop`,
#: `length`, `tool_calls`, `content_filter` and the deprecated `function_call`.
#:
#: `end_turn` is Anthropic's word for `stop`, and it sat in three call sites of
#: this helper until #107 while `app/` used it nowhere. A fourth test asserted
#: the turn_metrics row held `end_turn`, which passed because the double a
#: hundred lines above handed it that string. It would have stayed green if the
#: column stopped being written at all.
#:
#: tests/unit/test_agent_loop.py drives the real loop to every ending it can
#: reach and checks each recorded value against this set, so the set is pinned
#: to the loop rather than to itself.
STOP_REASONS = frozenset(
    {
        "max_model_calls",
        "budget_exceeded",
        "no_choices",
        "stop",
        "length",
        "tool_calls",
        "content_filter",
        "function_call",
    }
)


def canned_turn_result(response_text: str, **overrides) -> dict:
    """The dict `run_agent_loop` returns for a clean turn, plus `overrides`."""
    reason = overrides.get("stop_reason", "stop")
    if reason not in STOP_REASONS:
        raise ValueError(
            "stop_reason=%r is not a word run_agent_loop records. It is one of "
            "%s. A double that hands a test a value the loop cannot produce "
            "makes any assertion reading that value pass for the wrong reason."
            % (reason, sorted(STOP_REASONS))
        )
    return {
        "response_text": response_text,
        "tool_calls_log": [],
        "escalated": False,
        "escalation_reason": None,
        "escalation_context": None,
        # Always returned by _turn_result, read with .get() by the task.
        "num_turns": 2,
        "stop_reason": "stop",
        # SEC-01/L4, added by #50 when the firewall moved inside the seam.
        "pii_detector": None,
        "pii_published_chunks": 0,
        "pii_original_length": len(response_text),
        "pii_published_exemption": False,
        **overrides,
    }
