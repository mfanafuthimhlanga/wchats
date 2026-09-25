"""The deterministic check for an ambiguous scenario (#226, ADR 0012).

The agent's verdict is read off what it DID, a `clarify` call, or off a reply
that opens with a short question (the owner's decision, 2026-09-25). The
owner's reference has no tool log and is held to its own text rule.
"""

from __future__ import annotations

import pytest

from app.services.clarifying_check import (
    CLARIFYING_CHECK_KEY,
    CLARIFYING_MAX_WORDS,
    asked_to_clarify,
    clarifying_verdicts,
    is_clarifying_question,
    reply_asks_to_clarify,
    split_checked_rows,
    turn_asked_to_clarify,
)

#: Replies from eval run 2944b802, verbatim apart from the curly apostrophe.
ASKED_ABOVE_A_LIST = (
    "Which project do you mean?\n\n- W Chats\n- Mellow's Earth Elements\n- Beekeeper\n\n"
    "CITATIONS:\n- Document: W Chats | Section: What it is\n"
    "- Document: Mellow's Earth Elements | Section: What it is\n"
    "- Document: Beekeeper | Section: Stack"
)
ASKED_THEN_NAMED_THE_OPTIONS = (
    "Which product or project do you mean? The knowledge base includes pricing "
    "information for Beekeeper, Mellow's Earth Elements, and W Chats."
)
GUESSED_TWO_PROJECTS = (
    "W Chats uses Neon Postgres as its data store, with `pgvector` HNSW indexes. It has a "
    "control database and a separate Neon project for each tenant. Redis is used as the "
    "broker and pub/sub layer. (W Chats, Stack; W Chats, Architecture)\n\n"
    "Sentinel OHS uses Memgraph for persistence, reached through the Bolt port `7687`. "
    "(Sentinel OHS, Stack)\n\nCITATIONS:\n- Document: W Chats | Section: Stack"
)
GUESSED_ONE_PROJECT = (
    "The checkout code is in **Mellow's Earth Elements**:\n\n- `src/pages/Checkout.tsx`\n"
    "- `src/components/checkout/PaymentInfo.tsx`\n\nCITATIONS:\n"
    "- Document: Mellow's Earth Elements | Section: What it is"
)


class TestTheReplyRule:
    @pytest.mark.parametrize(
        "reply",
        [
            ASKED_ABOVE_A_LIST,
            ASKED_THEN_NAMED_THE_OPTIONS,
            "Which project are you asking about: Mellow's Earth Elements, Sentinel OHS, or W Chats?",
            "**Which project do you mean?** W Chats or Beekeeper.",
            "Which project, v1.2 or v2? The docs cover both.",
            "أي مشروع؟ W Chats أو Beekeeper.",
        ],
    )
    def test_a_short_reply_that_opens_by_asking_passes(self, reply):
        assert reply_asks_to_clarify(reply) is True

    @pytest.mark.parametrize(
        "reply",
        [
            "",
            "   ",
            GUESSED_TWO_PROJECTS,
            GUESSED_ONE_PROJECT,
            "Nine to five. Anything else?",
            "Run pnpm dev. Which project was that for?",
            "CITATIONS:\n- Document: W Chats | Section: Stack",
        ],
    )
    def test_a_reply_that_answers_first_or_runs_long_fails(self, reply):
        assert reply_asks_to_clarify(reply) is False

    def test_the_citations_block_does_not_count_toward_the_cap(self):
        question = " ".join(["which"] * (CLARIFYING_MAX_WORDS - 1)) + " one?"
        cited = question + "\n\nCITATIONS:\n" + "- Document: W Chats | Section: Stack\n" * 5
        assert reply_asks_to_clarify(cited) is True
        assert reply_asks_to_clarify("which " + question) is False

    def test_either_rule_is_enough_for_the_verdict(self):
        clarify = [{"tool_name": "clarify", "result": "?"}]
        retrieve = [{"tool_name": "retrieve", "result": "..."}]
        assert asked_to_clarify(clarify, GUESSED_ONE_PROJECT) is True
        assert asked_to_clarify(retrieve, ASKED_ABOVE_A_LIST) is True
        assert asked_to_clarify(retrieve, GUESSED_ONE_PROJECT) is False


class TestTheAgentsVerdictComesOffTheToolLog:
    def test_a_clarify_call_with_no_retrieve_is_asking(self):
        assert turn_asked_to_clarify([{"tool_name": "clarify", "result": "Which project?"}]) is True

    def test_retrieving_first_and_then_asking_is_asking(self):
        """The agent looked, saw four projects in the chunks, and asked."""
        log = [{"tool_name": "retrieve", "result": "..."}, {"tool_name": "clarify", "result": "?"}]
        assert turn_asked_to_clarify(log) is True

    def test_asking_and_then_retrieving_and_answering_anyway_is_answering(self):
        """The shape that made five of run 735fb9fa's ambiguous rows fail."""
        log = [{"tool_name": "clarify", "result": "?"}, {"tool_name": "retrieve", "result": "..."}]
        assert turn_asked_to_clarify(log) is False

    def test_a_stored_row_the_current_loop_could_not_write_still_reads_as_answering(self):
        """The False branch survives #280, because the rule reads STORED evidence.

        `agent_loop` ends a turn on a successful clarify and sorts a clarify to
        the end of its own reply's batch, so a turn served today cannot produce
        `clarify` then `retrieve`. Four sources still can. A `tool_calls` row
        written before #280, a trace `mine_production_scenarios` pulls back, a
        replayed conversation, and a red-team transcript. The rule is a reading
        of a log rather than an assertion about the writer, so it must keep
        deciding this shape rather than assume the loop prevents it.
        """
        stored = [
            {"tool_name": "clarify", "input": {"question": "Which project?"}},
            {"tool_name": "retrieve", "input": {"query": "dev server"}, "result": "pnpm dev"},
            {"tool_name": "retrieve", "input": {"query": "port"}, "result": "3000"},
        ]

        assert turn_asked_to_clarify(stored) is False

    def test_a_turn_with_no_tool_calls_is_not_asking(self):
        """Free text is never read for the agent: 'Anything else?' is an answer."""
        assert turn_asked_to_clarify([]) is False

    def test_other_tools_do_not_count_either_way(self):
        assert turn_asked_to_clarify([{"tool_name": "escalate"}]) is False
        assert turn_asked_to_clarify([{"tool_name": "escalate"}, {"tool_name": "clarify"}]) is True
        assert turn_asked_to_clarify([{"tool_name": "clarify"}, {"tool_name": "escalate"}]) is False


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
