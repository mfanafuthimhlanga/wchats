"""The deterministic check for an ambiguous scenario (#226, ADR 0012).

Two rules, and the reason there are two is the first finding of the adversarial
review: the agent has a `clarify` tool, so the agent's verdict is read off what
it DID, and free text is read only for the owner's reference, which has no tool
log.
"""

from __future__ import annotations

import pytest

from app.services.clarifying_check import (
    CLARIFYING_CHECK_KEY,
    CLARIFYING_MAX_WORDS,
    clarifying_verdicts,
    is_clarifying_question,
    split_checked_rows,
    turn_asked_to_clarify,
)


class TestTheAgentsVerdictComesOffTheToolLog:
    def test_a_clarify_call_with_no_retrieve_is_asking(self):
        assert turn_asked_to_clarify([{"tool_name": "clarify", "result": "Which project?"}]) is True

    def test_a_turn_that_retrieved_is_answering_even_if_it_also_asked(self):
        log = [{"tool_name": "retrieve", "result": "..."}, {"tool_name": "clarify", "result": "?"}]
        assert turn_asked_to_clarify(log) is False

    def test_a_turn_with_no_tool_calls_is_not_asking(self):
        """Free text is never read for the agent: 'Anything else?' is an answer."""
        assert turn_asked_to_clarify([]) is False

    def test_other_tools_do_not_count_either_way(self):
        assert turn_asked_to_clarify([{"tool_name": "escalate"}]) is False
        assert turn_asked_to_clarify([{"tool_name": "escalate"}, {"tool_name": "clarify"}]) is True


class TestTheReferenceRule:
    @pytest.mark.parametrize(
        "text",
        [
            "Which project are you setting up?",
            "Happy to help. Is this for Mellow's Earth Elements or the widget?",
            '"Which project?"',
            "**Which project are you setting up?**",
            "_Which project?_",
            "  Which project?  \n",
            "أي مشروع؟",
            "どのプロジェクトですか？",
        ],
    )
    def test_a_short_reference_that_ends_by_asking_passes(self, text):
        assert is_clarifying_question(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "Run pnpm dev from the repo root.",
            "Which project? Run pnpm dev either way.",
        ],
    )
    def test_a_reference_that_answers_is_refused(self, text):
        assert is_clarifying_question(text) is False

    def test_the_cap_is_inclusive_and_exact(self):
        at_cap = " ".join(["which"] * (CLARIFYING_MAX_WORDS - 1)) + " one?"
        over = " ".join(["which"] * CLARIFYING_MAX_WORDS) + " one?"
        assert is_clarifying_question(at_cap) is True
        assert is_clarifying_question(over) is False


class TestTheVerdictsComeOffTheKey:
    def test_only_rows_carrying_the_key_have_a_verdict(self):
        rows = [
            {"id": "s0", CLARIFYING_CHECK_KEY: True},
            {"id": "s1", CLARIFYING_CHECK_KEY: False},
            {"id": "s2", "ambiguous": True},
        ]
        assert clarifying_verdicts(rows) == {"s0": True, "s1": False}

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
