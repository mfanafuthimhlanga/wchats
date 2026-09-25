"""The grounding rule (ADR 0015): what it flags, what it lets through, and the benchmark it is pinned to.

Nothing here spends a model call. The benchmark numbers are pinned per rule
version, so a change to a floor or a stop word moves a test and not only a note.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import pathlib

import pytest

from app.domain.grounding import (
    CARRIED_FLOOR,
    GROUNDING_IDENTITY,
    GROUNDING_RULE_VERSION,
    ground,
    passages_of,
    response_sentences,
    stem,
    tokens_of,
)

BENCH = pathlib.Path(__file__).resolve().parents[1] / "evals" / "calibration" / "benchmark"
FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "widget_claim.json"

RETURNS = (
    "Returns and refunds. You can return an unused item within 30 days of delivery for a full refund. "
    "Refunds are paid to the original payment method and arrive within 5 to 7 working days of us receiving the item. "
    "Items marked final sale cannot be returned. To start a return, open your order in the account page and choose Return item. "
    "We email a prepaid courier label the same day. Sale items can be returned for store credit only."
)


class TestTheWords:
    def test_stem_folds_inflections_and_never_cuts_below_four_letters(self):
        assert [stem(w) for w in ("deployed", "deploys", "deploying", "deploy")] == ["deploy"] * 4
        assert stem("policies") == "policy"
        assert stem("arrives") == "arrive"
        assert stem("fees") == "fees"

    def test_tokens_drop_stop_words_and_short_words_and_keep_numbers_and_identifiers(self):
        assert tokens_of("The refund takes 14 days via app.config and the API") == {"14", "app.config", "days", "refund", "take"}


class TestTheCuts:
    def test_sentences_keep_bullets_and_drop_code_and_the_citations_block(self):
        text = "Two steps apply here today.\n\n- Open the settings page first.\n```\nrun this\n```\nCITATIONS:\n- Document: x"
        assert response_sentences(text) == ["Two steps apply here today.", "Open the settings page first."]

    def test_a_long_chunk_is_cut_at_sentence_ends_and_an_empty_one_yields_nothing(self):
        long = "The warranty covers accidental damage for twelve months from purchase. " * 10
        cut = passages_of([long, "", "  "])
        assert len(cut) == 2  # 710 characters cut once past the 300 floor
        assert all(p.endswith(".") for p in cut)


class TestTheRule:
    def test_a_sentence_the_passage_carries_is_grounded_and_one_it_does_not_is_flagged(self):
        g = ground("Refunds arrive within fourteen days. Purple elephants dance nightly.", [RETURNS])
        assert [s.supported for s in g.sentences] == [True, False]
        assert g.score == 0.5
        assert g.claims[1]["reason"] == "no passage shares a word with it"

    def test_a_number_the_retrieved_text_never_states_flags_the_sentence_even_when_its_words_are_carried(self):
        g = ground("Refunds arrive within 9 working days of us receiving the item.", [RETURNS])
        assert g.sentences[0].carried >= CARRIED_FLOOR
        assert g.sentences[0].missing_numbers == ("9",)
        assert g.sentences[0].supported is False
        assert "number 9" in g.claims[0]["reason"]

    def test_no_retrieved_text_flags_every_sentence_rather_than_passing_it(self):
        g = ground("Refunds arrive within fourteen days.", [])
        assert g.score == 0.0

    def test_an_answer_with_no_scoreable_sentence_has_no_score(self):
        assert ground("```\ncode only\n```", [RETURNS]).score is None
        assert ground("", [RETURNS]).claims == []

    def test_the_claims_reproduce_the_score_which_is_what_judge_record_refuses_otherwise(self):
        g = ground("Refunds arrive within fourteen days. Purple elephants dance nightly. Sale items return for store credit only.", [RETURNS])
        share = sum(1 for c in g.claims if c["supported"]) / len(g.claims)
        assert share == g.score

    def test_the_widget_claim_lands_on_the_deploy_passage_the_same_as_the_bench(self):
        # the case that fooled the reading aid on 2026-09-23; the rule and the aid are one rule
        fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
        g = ground(fx["claims"][0]["statement"], fx["retrieved_contexts"])
        passages = passages_of(fx["retrieved_contexts"])
        assert fx["expect_passage_contains"] in passages[g.sentences[0].passage]

    def test_a_tie_goes_to_the_denser_passage_not_the_first(self):
        # both passages carry exactly "refund" and "return" of the sentence's five words; the
        # first is an overview padded with other words, the second is about those two
        sentence = "Refunds arrive within fourteen days of the return."
        overview = "Refunds and returns are handled through the account page along with orders invoices addresses statements and profile settings for every customer of the store."
        dense = "Refunds and returns are quick."
        assert len(tokens_of(sentence) & tokens_of(overview)) == len(tokens_of(sentence) & tokens_of(dense)) == 2
        g = ground(sentence, [overview, dense])
        assert g.sentences[0].passage == 1

    @pytest.mark.parametrize("sentence", [
        "The corpus does not specify the preview port.",
        "However, the documentation does not establish that every feature works offline.",
        "I don't have more specific post-launch information in my knowledge base.",
        # a list comma, not a clause comma (#319): no subject with an auxiliary or a named verb
        # follows the comma, or the item carries its own relative clause
        "The corpus does not state who approves an answer, how provenance is stored, or how cache entries are invalidated.",
        "The documentation does not include a measured performance, bundle-size, or maintenance comparison.",
        "The corpus does not document the build, the tests, or the deployment steps.",
        "The documentation does not name React, Vue, or Svelte as options.",
        "The corpus does not document the build, the tests, or the deployment steps for staging.",
        "The corpus does not document the build, or the deployment process for staging.",
        "The corpus does not name the fixtures, or the tests themselves in detail.",
        "The documentation does not name React, Vue, or Svelte plugins for this.",
        "The corpus does not give the timeout, or the 3 retries per minute.",
        "The documentation does not describe the owner, or the teams that are on call.",
        "The documentation does not describe the port, or the host which is used in staging.",
        "The documentation does not describe the queue, or the files it writes to.",
        "The documentation does not describe the owner, or the keys the service requires for signing.",
        "The documentation does not describe the port, or the settings Fastify expects in production.",
        "The documentation does not describe the hosts, or the ports each service listens on.",
        "The documentation does not describe the refunds, or the webhooks Stripe sends on failure.",
        "The documentation does not describe the roles, or the files users can read.",
        "The documentation does not describe the defaults, or the settings admins can change.",
        "The documentation does not describe the tests, or the data nobody has checked.",
        "The documentation does not describe the cache, or the way caching is configured.",
        "The documentation does not describe the host, or the port traffic runs on.",
        "The documentation does not describe the flow, or the sign-up steps users must complete.",
        "The documentation does not describe the roles, or the files users can read in the workspace.",
        "The documentation does not describe the defaults, or the settings admins can change at runtime.",
        "The documentation does not describe the tests, or the data nobody has checked since the migration.",
        "The documentation does not describe the cache, or the way caching is configured for staging.",
        "The documentation does not describe the host, or the port traffic runs on in production.",
        "The documentation does not describe the flow, or the sign-up steps users must complete before checkout.",
        "The documentation does not describe the roles, or Postgres users can connect with.",
        "The documentation does not describe the build, or the test runs for staging.",
        "The documentation does not describe the roles, or the files users can see when I share them.",
    ])
    def test_a_whole_sentence_decline_about_the_documents_is_grounded(self, sentence):
        s = ground(sentence, [RETURNS]).sentences[0]
        assert s.supported is True and s.decline is True
        assert "asserts nothing" in s.reason

    @pytest.mark.parametrize("sentence", [
        "The server listens on port 8080; the corpus does not specify the preview port.",
        "The corpus does not specify a web test command; it documents Playwright for the web tests.",
        "The deploy workflow does not roll the control database back when a tenant migration fails.",
        "The repository does not use Memgraph for the server tests.",
        "That means the repository cannot provide a reliable monthly order count.",
        # a clause after the comma (#319): a subject and a finite verb, so the number is checked
        "The documentation does not specify the port, and the Fastify server listens on 8080.",
        "The documentation does not establish that all agent functionality works without network access, and the normal configuration still expects Anthropic credentials.",
        "The corpus does not specify the port, and the server is 8080.",
        "The corpus does not specify the port, and Fastify listens on 8080.",
    ])
    def test_a_claim_about_the_system_or_a_second_clause_is_scored_on_its_words(self, sentence):
        s = ground(sentence, [RETURNS]).sentences[0]
        assert s.decline is False
        assert s.supported is False

    def test_a_curly_closing_quote_still_ends_a_sentence(self):
        got = response_sentences("The portfolio says \u201cGo was chosen.\u201d It does show a constraint here today.")
        assert len(got) == 2

    def test_a_thousands_separator_does_not_make_a_number_missing(self):
        g = ground("The bundle limit is 20,480 bytes for the widget.", ["The widget bundle limit is 20480 bytes gzipped, measured on every build of the widget."])
        assert g.sentences[0].missing_numbers == ()
        g = ground("The cap is 1,000,000 rows for the widget.", ["The widget cap is 1000000 rows on every plan of the widget."])
        assert g.sentences[0].missing_numbers == ()

    def test_a_short_list_of_numbers_is_two_numbers_not_one(self):
        g = ground("Follow steps 3,4 for the widget refund.", ["The widget refund follows steps 3 and 4 in order for every customer."])
        assert g.sentences[0].missing_numbers == ()

    def test_a_stop_word_on_both_sides_counts_for_nothing(self):
        assert tokens_of("within without about") == frozenset()

    def test_the_identity_names_the_rule_and_its_version(self):
        assert GROUNDING_IDENTITY.model == "rule:grounding"
        assert GROUNDING_IDENTITY.prompt_version == GROUNDING_RULE_VERSION


def _measure():
    spec = importlib.util.spec_from_file_location("ground_rows", BENCH / "ground_rows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.measure(CARRIED_FLOOR, 0.80)


class TestTheBenchmark:
    """PUBLISHED for grounding-v3, measured 2026-09-25 (#319). A moved number is a moved rule.

    grounding-v1, 2026-09-23, read 8 of 30 passing and 103 of 260 sentences flagged. v2 reads a
    sentence under the floor once more against its best passage joined with the passage that
    adds the most words the best lacks, two at least, and never for a sentence that asserts a
    reason or a consequence. The planted recall held at 9 of 10, eight of them by words as
    before, and 20 sentences stopped being flagged.
    """

    def test_nine_of_ten_planted_sentences_are_flagged_each_scored_on_its_own(self):
        # the tenth, the 4b0b3432 plant, shares half its words with a passage and passes
        # at every floor below 0.6; 0.6 would fail 29 of the 30 real answers
        m = _measure()
        assert (m["planted_flagged"], m["planted"]) == (9, 10)

    def test_the_real_answers_read_as_measured_on_the_day(self):
        m = _measure()
        assert (m["real_answers"], m["pass_at_threshold"], m["sentences"], m["sentences_flagged"]) == (30, 10, 260, 84)

    def test_every_truth_claim_sits_in_its_answer(self):
        rows = {r["scenario_id"]: r for r in csv.DictReader((BENCH / "rows.csv").open(encoding="utf-8", newline=""))}
        for t in csv.DictReader((BENCH / "truth.csv").open(encoding="utf-8", newline="")):
            assert t["claim"] in rows[t["scenario_id"]]["response"]


@pytest.mark.parametrize("floor", [0.0, 1.0])
def test_the_floor_is_the_only_knob_and_it_moves_the_verdict(floor):
    g = ground("Refunds arrive within fourteen days of the return.", [RETURNS], carried_floor=floor)
    assert g.sentences[0].supported is (floor == 0.0 or g.sentences[0].carried >= 1.0)


class TestTwoPassages:
    """A sentence that joins two facts from two chunks is true to the documents (#306)."""

    A = "The storefront runs React Router for the catalog, the product pages and the cart."
    B = "Orders are never sent to a server; the app reads and writes no database at all."
    C = "The Fastify server broadcasts every event over a WebSocket to the dashboard."

    def test_a_sentence_joining_two_passages_is_carried_by_both_together(self):
        # A and B each carry 4 of the 13 content words, both under the floor; together 8
        sentence = (
            "React Router handles the catalog and the cart, the audit log keeps every step, "
            "nothing is sent to a server, and the app writes no database."
        )
        g = ground(sentence, [self.A, self.B, self.C])
        (s,) = g.sentences
        assert s.supported
        assert s.spanned_with >= 0 and s.spanned_with != s.passage
        assert "together carry" in s.reason

    def test_a_sentence_one_passage_carries_is_not_read_against_two(self):
        g = ground("React Router handles the catalog, the product pages and the cart.", [self.A, self.B])
        (s,) = g.sentences
        assert s.supported and s.spanned_with == -1
        assert "together" not in s.reason

    def test_words_spread_thin_over_three_passages_stay_flagged(self):
        # A carries one word, B one and C two; no second passage adds two new words
        sentence = "The router, the server and the dashboard were rewritten in Rust last quarter."
        g = ground(sentence, [self.A, self.B, self.C])
        (s,) = g.sentences
        assert not s.supported

    def test_the_floor_is_the_same_floor(self):
        sentence = (
            "React Router handles the catalog and the cart, the audit log keeps every step, "
            "nothing is sent to a server, and the app writes no database."
        )
        assert ground(sentence, [self.A, self.B], carried_floor=0.95).sentences[0].supported is False

    def test_a_missing_number_still_flags_a_spanned_sentence(self):
        sentence = "React Router handles the catalog and the cart, and 42 orders are sent to a server."
        (s,) = ground(sentence, [self.A, self.B]).sentences
        assert not s.supported and s.missing_numbers == ("42",)

    def test_the_reason_names_both_passages(self):
        sentence = (
            "React Router handles the catalog and the cart, the audit log keeps every step, "
            "nothing is sent to a server, and the app writes no database."
        )
        (s,) = ground(sentence, [self.A, self.B]).sentences
        assert s.reason.startswith(f"passages {s.passage + 1} and {s.spanned_with + 1} together carry")

    def test_a_reason_or_a_consequence_gets_no_second_reading(self):
        # two passages carry the facts; neither carries the "because" between them
        sentence = (
            "React Router handles the catalog and the cart because the audit log keeps every "
            "step, nothing is sent to a server, and the app writes no database."
        )
        (s,) = ground(sentence, [self.A, self.B]).sentences
        assert not s.supported and s.spanned_with == -1

    def test_a_second_passage_adding_one_word_does_not_count(self):
        # C shares only "server" with the sentence beyond what A carries
        sentence = "React Router handles the catalog, the cart and the server that lists them."
        (s,) = ground(sentence, [self.A, self.C]).sentences
        assert s.spanned_with == -1

    def test_a_union_under_the_floor_leaves_the_single_reading(self):
        sentence = (
            "React Router handles the catalog, the audit log keeps every step and the ledger, "
            "the dashboard shows the map, nothing is sent to a server."
        )
        (s,) = ground(sentence, [self.A, self.B]).sentences
        single = ground(sentence, [self.A]).sentences[0]
        assert s.spanned_with == -1 and s.carried == single.carried and not s.supported


class TestTheView:
    """grounding-v4: the paragraph VIEW_MARKER opens is the agent's reasoning.

    It is not held to the word-overlap floor, and it still fails on a number no
    passage carries. The platform prompt imports VIEW_MARKER, so the words the agent
    is told to write are the words read here.
    """

    FACT = "Returns are accepted within 30 days of delivery with the original receipt."
    VIEW = (
        "My view: keep the receipt anyway, since it settles any argument quickly "
        "and costs nothing to hold on to."
    )

    def test_a_view_the_passages_share_no_words_with_is_grounded(self):
        g = ground(self.FACT + "\n\n" + self.VIEW, [RETURNS])
        fact, view = g.sentences[0], g.sentences[-1]
        assert fact.view is False and view.view is True
        assert view.supported is True
        assert view.reason == "the agent's view, read for its numbers only"
        assert g.score == 1.0

    def test_a_number_in_the_view_is_still_checked(self):
        view = "My view: wait 45 days before chasing it, because couriers run late."
        s = ground(view, [RETURNS]).sentences[0]
        assert (s.view, s.supported, s.missing_numbers) == (True, False, ("45",))
        assert s.reason == "the agent's view; number 45 appears in no passage"

    def test_the_view_ends_at_the_blank_line(self):
        answer = self.VIEW + "\n\nShipping is free on every order over 900 rand."
        after = ground(answer, [RETURNS]).sentences[-1]
        assert after.view is False and after.supported is False

    def test_every_sentence_of_the_view_paragraph_is_the_view(self):
        answer = "My view: narrow it first.\nA smaller scope is easier to test well. It also ships sooner."
        assert [s.view for s in ground(answer, [RETURNS]).sentences] == [True, True]

    def test_the_marker_in_bold_opens_the_view(self):
        assert ground("**My view:** narrow it first, since a smaller scope tests well.", [RETURNS]).sentences[0].view

    @pytest.mark.parametrize("answer", [
        "In my view, returns take too long to process for most customers today.",
        "Returns take too long. My view: nothing here opens a paragraph mid-line.",
        "- My view: a list item is a fact line, not the view paragraph.",
    ])
    def test_only_a_paragraph_opening_with_the_marker_is_the_view(self, answer):
        assert not any(s.view for s in ground(answer, [RETURNS]).sentences)

    def test_the_prompt_asks_for_the_marker_this_rule_reads(self):
        from app.domain.grounding import VIEW_MARKER
        from app.services.agent_prompt import _TEMPLATE

        assert f'one paragraph \nthat opens "{VIEW_MARKER}"' in _TEMPLATE
