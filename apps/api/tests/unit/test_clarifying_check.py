"""The deterministic check for an ambiguous scenario (#226, ADR 0012).

A rule, not a Judge, so every branch here is one the eval can be observed to
take. The two halves are separate locks: a response ends in a question, and it
is short enough that the question is the reply rather than a courtesy on the
end of an answer.
"""

from __future__ import annotations

import pytest

from app.services.clarifying_check import (
    CLARIFYING_CHECK_KEY,
    CLARIFYING_MAX_WORDS,
    clarifying_verdicts,
    is_clarifying_question,
    split_checked_rows,
)


class TestIsClarifyingQuestion:
    @pytest.mark.parametrize(
        "response",
        [
            "Which project are you setting up?",
            "Happy to help. Is this for Mellow's Earth Elements or the widget?",
            '"Which project?"',
            "  Which project?  \n",
            "Could you tell me which plan you are on, the monthly or the annual one?",
        ],
    )
    def test_a_short_reply_that_ends_by_asking_is_a_clarifying_question(self, response):
        assert is_clarifying_question(response) is True

    @pytest.mark.parametrize(
        "response",
        [
            "",
            "   ",
            "Run pnpm dev from the repo root.",
            "Run pnpm dev from the repo root. Let me know if that works",
            "Which project? Run pnpm dev either way.",
        ],
    )
    def test_a_reply_that_answers_is_not(self, response):
        assert is_clarifying_question(response) is False

    def test_an_answer_with_a_courtesy_question_on_the_end_is_an_answer(self):
        """The word cap is the second lock. Without it this passes."""
        answer = " ".join(["word"] * CLARIFYING_MAX_WORDS) + " Anything else I can help with?"
        assert is_clarifying_question(answer) is False

    def test_the_cap_is_inclusive(self):
        at_cap = " ".join(["which"] * (CLARIFYING_MAX_WORDS - 1)) + " one?"
        over = " ".join(["which"] * CLARIFYING_MAX_WORDS) + " one?"
        assert is_clarifying_question(at_cap) is True
        assert is_clarifying_question(over) is False


class TestTheVerdictsComeOffTheKey:
    def test_only_rows_carrying_the_key_have_a_verdict(self):
        rows = [
            {"id": "s0", CLARIFYING_CHECK_KEY: True},
            {"id": "s1", CLARIFYING_CHECK_KEY: False},
            {"id": "s2"},
        ]
        assert clarifying_verdicts(rows) == {"s0": True, "s1": False}

    def test_a_row_without_the_key_is_never_read_as_a_fail(self):
        assert clarifying_verdicts([{"id": "s2", "ambiguous": True}]) == {}

    def test_split_keeps_input_order_on_both_sides(self):
        rows = [
            {"id": "a"},
            {"id": "b", CLARIFYING_CHECK_KEY: True},
            {"id": "c"},
            {"id": "d", CLARIFYING_CHECK_KEY: False},
        ]
        judged, checked = split_checked_rows(rows)
        assert [r["id"] for r in judged] == ["a", "c"]
        assert [r["id"] for r in checked] == ["b", "d"]
