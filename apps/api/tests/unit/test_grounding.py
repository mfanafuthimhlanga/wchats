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
    """PUBLISHED for grounding-v1, measured 2026-09-23. A moved number is a moved rule."""

    def test_nine_of_ten_planted_sentences_are_flagged_each_scored_on_its_own(self):
        # the tenth, the 4b0b3432 plant, shares half its words with a passage and passes
        # at every floor below 0.6; 0.6 would fail 29 of the 30 real answers
        m = _measure()
        assert (m["planted_flagged"], m["planted"]) == (9, 10)

    def test_the_real_answers_read_as_measured_on_the_day(self):
        m = _measure()
        assert (m["real_answers"], m["pass_at_threshold"], m["sentences"], m["sentences_flagged"]) == (30, 8, 260, 103)

    def test_every_truth_claim_sits_in_its_answer(self):
        rows = {r["scenario_id"]: r for r in csv.DictReader((BENCH / "rows.csv").open(encoding="utf-8", newline=""))}
        for t in csv.DictReader((BENCH / "truth.csv").open(encoding="utf-8", newline="")):
            assert t["claim"] in rows[t["scenario_id"]]["response"]


@pytest.mark.parametrize("floor", [0.0, 1.0])
def test_the_floor_is_the_only_knob_and_it_moves_the_verdict(floor):
    g = ground("Refunds arrive within fourteen days of the return.", [RETURNS], carried_floor=floor)
    assert g.sentences[0].supported is (floor == 0.0 or g.sentences[0].carried >= 1.0)
