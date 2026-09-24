"""The platform benchmark and the per-claim scorer (#290 step 2).

Two subjects. The scorer, `tests/evals/calibration/benchmark/score_claims.py`,
vendored from the calibrate-judge skill: its match rule, its recall, the
three-way split of judge-unsupported claims into true alarm, false alarm and
undecided, and the two lines that say `unknown` rather than a ratio. And the
benchmark files themselves: every truth claim names an answer the benchmark
holds, sits verbatim in that answer, and states at least one fabricated word
that neither the answer's retrieved text nor its original answer carries, so
the truth is construction truth and not a label anyone had to be right about.

The published numbers are pinned against the shipped claims file, per judge
identity, so a change to the rule or the data moves a test and not only a note.
Nothing here spends a model call.
"""

from __future__ import annotations

import csv
import importlib.util
import pathlib

import pytest

BENCH = pathlib.Path(__file__).resolve().parents[1] / "evals" / "calibration" / "benchmark"


def _load():
    spec = importlib.util.spec_from_file_location("score_claims", BENCH / "score_claims.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sc = _load()


def _rows(path: str) -> list[dict]:
    with open(BENCH / path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# The match rule, on the real sentences from the benchmark.
# ---------------------------------------------------------------------------
PLANTED = "A failed tenant migration rolls the control database back to its previous revision before the deploy is marked failed."
NOVEL = {"failed", "marked", "previous", "revision", "rolls"}


class TestTheMatchRule:
    def test_an_atomic_piece_carrying_a_fabricated_word_matches(self):
        assert sc.matches("A failed tenant migration causes the deployment to be marked as failed.", PLANTED, NOVEL)

    def test_a_neighbouring_claim_with_no_fabricated_word_does_not(self):
        """Four of its six words are in the planted sentence; none is fabricated."""
        assert not sc.matches("The migration command migrates the control database.", PLANTED, NOVEL)

    def test_the_first_threshold_binds_from_below(self):
        """A claim carrying a fabricated word but mostly other words is not the plant.

        Three of seven words in the truth claim, under JUDGE_IN_TRUTH = 0.5.
        Delete that test and this claim matches on 'revision' alone.
        """
        assert not sc.matches("The revision history of the widget bundle is kept by git.", PLANTED, NOVEL)
        assert sc.JUDGE_IN_TRUTH == 0.5

    def test_a_claim_the_original_answer_already_made_does_not_match_its_plant(self):
        """The 1adec12b case: 'serves the bundle locally' was flagged before the plant existed."""
        plant = "`pnpm preview` serves the bundle on port 4173 by default."
        assert not sc.matches('The command "pnpm preview" serves the production bundle locally.', plant, {"4173", "default"})
        assert sc.matches('The command "pnpm preview" serves the production bundle on port 4173 by default.', plant, {"4173", "default"})

    def test_a_two_digit_number_counts_as_a_word(self):
        """The fabricated detail is often a number; '80' must not be filtered away."""
        plant = "Coverage is enforced at 80 percent by the Jest configuration."
        assert sc.matches("The Jest configuration enforces 80 percent test coverage.", plant, {"80"})
        assert not sc.matches("The Jest configuration enforces 95 percent test coverage.", plant, {"80"})

    def test_a_truth_claim_with_no_novel_words_is_a_reviewed_statement_and_matches_only_itself(self):
        """A neighbouring judge claim the Tenant never saw must not inherit their answer."""
        reviewed = "Orders over R500 ship free."
        assert sc.matches("orders over  R500 ship free.", reviewed, set())
        assert not sc.matches("Orders over R500 ship free to Tembisa.", reviewed, set())
        assert not sc.matches("A failed tenant migration causes the deployment to be marked as failed.", PLANTED, set())

    def test_empty_text_never_matches(self):
        assert not sc.matches("", PLANTED, NOVEL)
        assert not sc.matches("a", "", NOVEL)


# ---------------------------------------------------------------------------
# The counts, over hand-built lists.
# ---------------------------------------------------------------------------
T_UNSUPPORTED = {"scenario_id": "s1", "claim": "Coverage is enforced at 80 percent by the Jest configuration.", "supported": "false", "novel": "80 enforced"}
# A reviewed truth is the judge's own statement, as the review showed it, with no novel word.
T_SUPPORTED = {"scenario_id": "s2", "claim": "Delivery costs R30 in the Tembisa area.", "supported": "true", "novel": ""}
ROWS = [{"scenario_id": "s1"}, {"scenario_id": "s2"}, {"scenario_id": "s3"}]
FLAG_S1 = {"scenario_id": "s1", "statement": "The Jest configuration enforces 80 percent test coverage.", "supported": "false"}
FLAG_S2 = {"scenario_id": "s2", "statement": "Delivery costs R30 in the Tembisa area.", "supported": "false"}
FLAG_S3 = {"scenario_id": "s3", "statement": "The site is hosted on Vercel.", "supported": "false"}


def _score(truth, judge, rows=ROWS):
    return sc.score(truth, judge, rows)


class TestRecall:
    def test_a_planted_claim_the_judge_flags_is_a_hit(self):
        r = _score([T_UNSUPPORTED], [FLAG_S1])
        assert (r["recall_hits"], r["unsupported_truth"]) == (1, 1)

    def test_a_planted_claim_the_judge_marks_supported_is_a_miss(self):
        r = _score([T_UNSUPPORTED], [{**FLAG_S1, "supported": "true"}])
        assert (r["recall_hits"], r["unsupported_truth"]) == (0, 1)
        assert r["not_extracted"] == []

    def test_a_planted_claim_the_judge_never_extracted_is_a_miss_of_its_own_kind(self):
        r = _score([T_UNSUPPORTED], [{"scenario_id": "s1", "statement": "Tests run under Jest.", "supported": "false"}])
        assert r["recall_hits"] == 0
        assert [h["truth"] for h in r["not_extracted"]] == [T_UNSUPPORTED]

    def test_a_flag_on_another_answer_does_not_count(self):
        r = _score([T_UNSUPPORTED], [{**FLAG_S1, "scenario_id": "s3"}])
        assert r["recall_hits"] == 0

    def test_zero_unsupported_truth_reports_unknown_not_a_ratio(self):
        lines = sc.report(_score([T_SUPPORTED], [FLAG_S2]), "j")
        [line] = [line for line in lines if line.strip().startswith("recall")]
        assert "unknown" in line
        assert " of " not in line

    def test_the_interval_is_printed_beside_the_count(self):
        lines = sc.report(_score([T_UNSUPPORTED], [FLAG_S1]), "j")
        [line] = [line for line in lines if "recall    1 of 1" in line]
        assert "95% interval [" in line


class TestWilson:
    def test_ten_of_ten_is_not_reported_as_certainty(self):
        low, high = sc.wilson(10, 10)
        assert 0.72 <= low <= 0.73
        assert high == 1.0

    def test_zero_of_ten_is_bounded_above(self):
        low, high = sc.wilson(0, 10)
        assert low == 0.0
        assert 0.27 <= high <= 0.28


class TestPrecisionSplitsThreeWays:
    def test_true_alarm_false_alarm_and_undecided_are_counted_apart(self):
        judge = [FLAG_S1, FLAG_S2, FLAG_S3, {"scenario_id": "s3", "statement": "The site uses React.", "supported": "true"}]
        r = _score([T_UNSUPPORTED, T_SUPPORTED], judge)
        assert (r["true_alarms"], r["false_alarms"], r["undecided"]) == (1, 1, 1)
        assert r["judge_unsupported"] == 3

    def test_an_undecided_claim_is_never_a_false_alarm(self):
        r = _score([T_UNSUPPORTED], [FLAG_S3])
        assert (r["true_alarms"], r["false_alarms"], r["undecided"]) == (0, 0, 1)

    def test_with_no_supported_truth_the_report_says_unknown_and_gives_the_confirmed_count(self):
        """A ratio whose denominator can only hold true alarms is not a precision."""
        lines = sc.report(_score([T_UNSUPPORTED], [FLAG_S1, FLAG_S3]), "j")
        [line] = [line for line in lines if line.strip().startswith("precision")]
        assert line.strip().startswith("precision unknown")
        assert "1 flags confirmed" in line
        assert "1 undecided" in line

    def test_with_a_supported_truth_the_ratio_is_printed(self):
        lines = sc.report(_score([T_UNSUPPORTED, T_SUPPORTED], [FLAG_S1, FLAG_S2]), "j")
        [line] = [line for line in lines if line.strip().startswith("precision")]
        assert "precision 1 of 2 decided" in line
        assert "1 false alarms" in line

    def test_a_supported_truth_with_nothing_decided_reports_unknown_not_zero_of_zero(self):
        """The recall line had this guard from the start; the precision line did not."""
        lines = sc.report(_score([T_UNSUPPORTED, T_SUPPORTED], [FLAG_S3]), "j")
        [line] = [line for line in lines if line.strip().startswith("precision")]
        assert line.strip().startswith("precision unknown")
        assert "0 of 0" not in line

    def test_a_judge_claim_matching_truths_that_disagree_is_ambiguous_not_decided(self):
        """Last-write-wins across truth rows would make the verdict depend on CSV order."""
        twin = {"scenario_id": "s1", "claim": FLAG_S1["statement"], "supported": "true", "novel": ""}
        r = _score([T_UNSUPPORTED, twin], [FLAG_S1])
        assert (r["true_alarms"], r["false_alarms"], r["ambiguous"], r["undecided"]) == (0, 0, 1, 0)

    def test_a_flagged_supported_truth_reads_as_a_false_alarm_in_the_report(self):
        lines = sc.report(_score([T_SUPPORTED], [FLAG_S2]), "j")
        assert any("FALSE ALARM" in line for line in lines)
        assert not any(" FOUND " in line for line in lines)


class TestTheAnswerLevel:
    def test_flagged_answers_are_split_by_whether_they_hold_a_planted_claim(self):
        r = _score([T_UNSUPPORTED], [FLAG_S1, FLAG_S3, {"scenario_id": "s2", "statement": "Delivery is R30.", "supported": "true"}])
        assert (r["flagged_with_planted"], r["answers_with_planted"]) == (1, 1)
        assert (r["flagged_without_planted"], r["answers_without_planted"]) == (1, 2)
        assert r["per_answer_unsupported"] == [0, 1, 1]

    def test_an_answer_with_no_judge_claim_is_unscored_not_clean(self):
        """A NaN faithfulness result carries no claims; it must not read as zero unsupported."""
        r = _score([T_UNSUPPORTED], [FLAG_S1])
        assert r["unscored"] == ["s2", "s3"]
        assert (r["answers_without_planted"], r["flagged_without_planted"]) == (0, 0)
        assert r["per_answer_unsupported"] == [1]
        assert any("2 unscored" in line for line in sc.report(r, "j"))


class TestRefusals:
    def test_supported_must_be_true_or_false(self):
        with pytest.raises(SystemExit, match="true or false"):
            _score([T_UNSUPPORTED], [{**FLAG_S1, "supported": "yes"}])

    def test_a_claim_about_an_answer_the_benchmark_lacks_is_refused(self):
        with pytest.raises(SystemExit, match="rows.csv does not hold"):
            _score([T_UNSUPPORTED], [{**FLAG_S1, "scenario_id": "zzz"}])

    def test_a_truth_file_missing_the_novel_column_is_refused(self, tmp_path):
        (tmp_path / "truth.csv").write_text("scenario_id,claim,supported\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="novel"):
            sc.read_csv(tmp_path / "truth.csv", {"scenario_id", "claim", "supported", "novel"})


# ---------------------------------------------------------------------------
# The benchmark files.
# ---------------------------------------------------------------------------
class TestTheBenchmarkFiles:
    def test_rows_holds_the_thirty_real_and_ten_seeded_answers(self):
        rows = _rows("rows.csv")
        assert len(rows) == 40
        assert sum(1 for r in rows if r["source"] == "735fb9fa") == 30
        assert sum(1 for r in rows if r["source"] == "735fb9fa-seeded") == 10

    def test_each_seeded_answer_names_the_real_answer_it_was_built_from(self):
        rows = {r["scenario_id"]: r for r in _rows("rows.csv")}
        for sid in [s for s in rows if s.endswith("-seeded")]:
            assert sid.removesuffix("-seeded") in rows, sid

    def test_every_truth_claim_names_an_answer_the_benchmark_holds(self):
        known = {r["scenario_id"] for r in _rows("rows.csv")}
        for t in _rows("truth.csv"):
            assert t["scenario_id"] in known, t["scenario_id"]

    def test_every_construction_truth_is_verbatim_in_its_answer_with_a_fabricated_word_nothing_else_carries(self):
        """The `novel` words are the truth's whole claim to being truth: each one is
        absent from the retrieved text AND from the original answer, by the scorer's
        own tokenizer, so a judge claim carrying one is about the plant."""
        rows = {r["scenario_id"]: r for r in _rows("rows.csv")}
        truths = [t for t in _rows("truth.csv") if t["source"] == "construction"]
        assert len(truths) == 10
        for t in truths:
            assert t["supported"] == "false"
            row = rows[t["scenario_id"]]
            original = rows[t["scenario_id"].removesuffix("-seeded")]
            assert t["claim"] in row["response"], t["scenario_id"]
            assert t["claim"] not in original["response"], t["scenario_id"]
            novel = sc.novel_words(t)
            assert novel, t["scenario_id"]
            assert novel <= sc.words(t["claim"])
            assert not novel & sc.words(row["retrieved_contexts"]), t["scenario_id"]
            assert not novel & sc.words(original["response"]), t["scenario_id"]

    def test_no_file_in_the_benchmark_puts_a_judge_verdict_on_a_labelling_sheet(self):
        """rows.csv and truth.csv are what a reviewer's sheet is built from."""
        for name in ("rows.csv", "truth.csv"):
            with open(BENCH / name, newline="", encoding="utf-8") as f:
                header = next(csv.reader(f))
            assert not {"verdict", "score", "reason", "judge_identity"} & set(header), name


# ---------------------------------------------------------------------------
# The published numbers, per shipped judge identity.
# ---------------------------------------------------------------------------
PUBLISHED = {
    "gpt-5.6-luna-none-ragas-0.4.3": {
        "recall_hits": 10,
        "unsupported_truth": 10,
        "true_alarms": 16,
        "false_alarms": 0,
        "undecided": 161,
        "flagged_with_planted": 10,
        "flagged_without_planted": 28,
        "answers_without_planted": 30,
        "unscored": [],
        "seeded_fraction_fails": 6,
    },
}


@pytest.mark.parametrize("identity", sorted(PUBLISHED))
def test_the_numbers_in_the_note_are_the_numbers_the_scorer_prints(identity):
    """Every headline number in `.dev/reference/260916-faithfulness-judge-first-measurement.md`
    comes from this file, so a change to the rule or the data moves this test first."""
    expected = PUBLISHED[identity]
    r = sc.score(_rows("truth.csv"), _rows(f"claims_{identity}.csv"), _rows("rows.csv"))
    for key in ("recall_hits", "unsupported_truth", "true_alarms", "false_alarms", "undecided",
                "flagged_with_planted", "flagged_without_planted", "answers_without_planted", "unscored"):
        assert r[key] == expected[key], key
    seeded = [s for s in _rows(f"scores_{identity}.csv") if s["scenario_id"].endswith("-seeded")]
    assert len(seeded) == 10
    assert sum(1 for s in seeded if s["verdict"] == "fail") == expected["seeded_fraction_fails"]


def test_every_shipped_claims_file_has_a_published_row():
    """Adding a judge identity means adding its numbers here, not only its CSV."""
    shipped = {p.stem.removeprefix("claims_") for p in BENCH.glob("claims_*.csv")}
    assert shipped == set(PUBLISHED)


# ---------------------------------------------------------------------------
# The reading aids carry the gate's rules (#298).
# ---------------------------------------------------------------------------
PAGE = BENCH.parent / "page"
SKILL_COPY = pathlib.Path.home() / ".claude" / "skills" / "calibrate-judge" / "page" / "claims_template.html"
GATE_TABLE = pathlib.Path(__file__).resolve().parents[4] / "apps" / "admin" / "tests-unit" / "fixtures-gate-rules.json"


@pytest.mark.skipif(not SKILL_COPY.exists(), reason="the calibrate-judge skill is installed on the owner's machine only")
def test_the_vendored_claims_bench_is_the_skill_copy_byte_for_byte():
    assert (PAGE / "claims_template.html").read_bytes() == SKILL_COPY.read_bytes()


def _tint(s) -> str:
    """The edge an aid draws for the gate's verdict: bone when grounded, red on no passage or a
    missing number, grey when some words are shared but too few."""
    if s.supported:
        return "bone"
    if s.passage < 0 or s.missing_numbers:
        return "fail"
    return "grey"


@pytest.mark.parametrize("scenario", ["with passages", "no_passages"])
def test_the_table_both_reading_aids_are_held_to_is_what_the_gate_says(scenario):
    """claims-reading.spec.ts and claims-bench.spec.ts assert this table against the console aid
    and the bench; here the gate itself produces it, so all three say one thing per sentence."""
    import json

    from app.domain.grounding import ground

    fx = json.loads(GATE_TABLE.read_text(encoding="utf-8"))
    table = fx if scenario == "with passages" else fx[scenario]
    got = ground(table["response"], table["retrieved_contexts"]).sentences
    assert len(got) == len(table["expect"])
    for s, want in zip(got, table["expect"], strict=True):
        lit = [] if s.passage < 0 else [s.passage] + ([s.spanned_with] if s.spanned_with >= 0 else [])
        assert (s.statement, _tint(s), s.reason, lit) == (
            want["sentence"], want["tint"], want["reason"], want["lit"]
        ), want["rule"]
