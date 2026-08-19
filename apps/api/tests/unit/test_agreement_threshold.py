"""The calibration threshold comes from the data, not from a constant.

BACKLOG 8.2c deleted `KAPPA_THRESHOLD = 0.6`. BACKLOG 8.2d deleted the constant
that replaced it without anyone noticing, after four independent adversarial
reviews on 2026-08-19 measured it:

    against a self-consistent labeller, `judge_high >= ceiling_low` reduces to
    `judge_high >= 1.000`, which needs 2.5% of resamples to contain none of the
    judge's error rows, i.e. `(1 - e/n)^n ~= e^-e >= 0.025`, i.e. `e <= 3.68`.

`e` is a COUNT, so the gate was "at most 3 disagreements" at every n. Two tests
here exist because of that and must not be deleted:

  - `test_a_fixed_error_rate_does_not_change_verdict_with_n` is the regression.
    Under the old rule the same judge passed at n=12 and failed at n=40, so
    labelling more rows made a judge harder to pass.
  - `test_a_labeller_who_cannot_reproduce_themselves_passes_nobody` is half (b1).
    Two reviewers independently produced exit-0 CALIBRATED from a ceiling whose
    interval spanned zero, because nothing asked whether the ceiling was any good.
"""

from __future__ import annotations

import pytest

from tests.evals.calibration.agreement import (
    BOOTSTRAP_SEED,
    MAX_UNDEFINED_FRACTION,
    agreement_precision,
    bootstrap_kappa,
    calibration_verdict,
    cohens_kappa,
    confusion,
    human_ceiling,
    tails,
    paired_difference,
)


def pairs(agree_pass: int = 0, agree_fail: int = 0, harsh: int = 0, lenient: int = 0):
    """(human_passed, judge_passed) pairs in the four confusion cells."""
    return (
        [(True, True)] * agree_pass
        + [(False, False)] * agree_fail
        + [(True, False)] * harsh
        + [(False, True)] * lenient
    )


def rows_and_judge(n: int, errors: int, human_errors: int = 0):
    """A balanced n-row sheet, a labeller, and a judge wrong `errors` times.

    Returns `(judge_pairs, ceiling_pairs)` aligned row by row, which is the shape
    `paired_difference` requires. The human's first-pass labels alternate so both
    labels are always well represented.

    `human_errors` is how many rows the labeller contradicted on their blind
    second pass; the default of 0 is a labeller who reproduced themselves exactly,
    which is the hardest ceiling and the one the old rule collapsed against. The
    contradictions are taken from the far end so they do not coincide with the
    judge's, which would make the two statistics agree for the wrong reason.
    """
    human = [i % 2 == 0 for i in range(n)]
    judge = list(human)
    for i in range(errors):
        judge[i] = not judge[i]
    second = list(human)
    for i in range(n - human_errors, n):
        second[i] = not second[i]
    return list(zip(human, judge)), list(zip(human, second))


def verdict_for(n: int, errors: int):
    judge_pairs, ceiling_pairs = rows_and_judge(n, errors)
    return calibration_verdict(
        bootstrap_kappa(judge_pairs),
        human_ceiling(ceiling_pairs),
        paired_difference(judge_pairs, ceiling_pairs),
    )


class TestTheBootstrap:
    def test_the_seed_is_actually_used(self):
        """Same seed identical, different seeds different.

        The previous version asserted only the first half, which an unseeded
        bootstrap satisfies whenever two runs happen to land on the same
        percentile. Measured 2026-08-19: it caught `random.Random()` one run in
        five. The second assertion is what makes the seed load-bearing.
        """
        sample = pairs(agree_pass=9, agree_fail=9, harsh=1, lenient=1)

        assert bootstrap_kappa(sample) == bootstrap_kappa(sample)
        assert (
            bootstrap_kappa(sample, seed=BOOTSTRAP_SEED)
            != bootstrap_kappa(sample, seed=BOOTSTRAP_SEED + 1)
        ), "if the seed changes nothing, the bootstrap is not using it"

    def test_the_confidence_level_widens_the_interval(self):
        """`CONFIDENCE` was pinned by nothing: `(1-c)/2` -> `(1-c)` left 87 tests green."""
        sample = pairs(agree_pass=14, agree_fail=14, harsh=3, lenient=3)
        narrow = bootstrap_kappa(sample, confidence=0.80)
        wide = bootstrap_kappa(sample, confidence=0.99)

        assert narrow["usable"] and wide["usable"]
        assert wide["low"] < narrow["low"], "a higher confidence must reach lower"
        assert (wide["high"] - wide["low"]) > (narrow["high"] - narrow["low"])

    def test_a_strong_judge_clears_chance(self):
        result = bootstrap_kappa(pairs(agree_pass=9, agree_fail=9, harsh=1, lenient=1))
        assert result["usable"]
        assert result["point"] == pytest.approx(0.8)
        assert result["low"] > 0

    def test_a_coin_flip_judge_does_not_beat_chance(self):
        """Half (a). The point estimate is 0.0; the interval is what refuses it."""
        result = bootstrap_kappa(pairs(agree_pass=5, agree_fail=5, harsh=5, lenient=5))
        assert result["usable"]
        assert result["point"] == pytest.approx(0.0)
        assert result["low"] <= 0 <= result["high"]
        assert calibration_verdict(result, None)["beats_chance"] is False

    def test_no_floating_point_dust_crosses_the_boundary(self):
        """Half (a) is `low > 0`, so an exact zero must be exactly zero.

        In float, `observed` and `expected` reached the same value by different
        routes and a mathematically exact 0 came back as 2.66e-16. The same
        corpus then returned CALIBRATED at the shipped seed and NOT CALIBRATED at
        seed 3000, both printing `[+0.000, +1.000]`.
        """
        balanced = pairs(agree_pass=5, agree_fail=5, harsh=5, lenient=5)
        for seed in (BOOTSTRAP_SEED, 3000, 3001, 3002):
            result = bootstrap_kappa(balanced, seed=seed)
            if result["usable"]:
                assert not result["low"] > 0, f"dust crossed the boundary at seed {seed}"

        assert cohens_kappa(confusion(balanced)) == 0.0
        assert not cohens_kappa(confusion(balanced)) > 0

    def test_a_one_sided_corpus_is_refused_rather_than_scored(self):
        result = bootstrap_kappa([(True, True)] * 10)
        assert result["usable"] is False
        assert result["low"] is None and result["high"] is None

    def test_a_nearly_one_sided_corpus_is_also_refused(self):
        """The test that actually pins `MAX_UNDEFINED_FRACTION`.

        Nine pass, one fail. Two thirds of the resamples ARE informative, so a
        percentile could be taken; it must not be, because the third that dropped
        the single `fail` describe a corpus with no disagreement in it at all.
        The entirely-one-sided test above is caught one line earlier by having no
        informative resamples at all, so it pins nothing here.
        """
        result = bootstrap_kappa(pairs(agree_pass=9, agree_fail=1))

        assert result["undefined_fraction"] > MAX_UNDEFINED_FRACTION
        assert result["undefined_fraction"] < 1.0, "some resamples DID survive"
        assert result["usable"] is False
        assert result["low"] is None and result["high"] is None

    def test_an_interval_spanning_the_whole_range_is_not_a_measurement(self):
        """Six rows cannot separate a coin from a flawless judge.

        Without this, a small sheet returned `[-0.000, +1.000]`, half (a) read it
        as a MEASURED failure, and the run exited 1 - which the exit codes define
        as "fix the judge" - over rows that could not say anything about any judge.

        Only 6% of these resamples are uninformative, well under
        `MAX_UNDEFINED_FRACTION`, so this refusal is the span guard doing the work
        and not the lopsided-corpus guard standing in for it.
        """
        result = bootstrap_kappa(pairs(agree_pass=2, agree_fail=2, harsh=1, lenient=1))

        assert result["undefined_fraction"] < MAX_UNDEFINED_FRACTION, (
            "the premise: this corpus is NOT refused for being one-sided"
        )
        assert result["spans_the_whole_range"] is True
        assert result["usable"] is False
        verdict = calibration_verdict(result, None)
        assert verdict["beats_chance"] is None, "None, never False"
        assert any("SIZE of the labelled set" in r for r in verdict["reasons"])

    def test_too_few_pairs_is_not_a_measurement(self):
        assert bootstrap_kappa([(True, True)])["usable"] is False
        assert bootstrap_kappa([])["usable"] is False

    def test_the_interval_widens_as_the_sample_shrinks(self):
        small = bootstrap_kappa(pairs(agree_pass=6, agree_fail=6, harsh=2, lenient=2))
        large = bootstrap_kappa(pairs(agree_pass=60, agree_fail=60, harsh=20, lenient=20))

        assert small["usable"] and large["usable"]
        assert small["point"] == pytest.approx(large["point"], abs=0.01)
        assert (small["high"] - small["low"]) > (large["high"] - large["low"])

    def test_the_reported_coverage_falls_as_resamples_lose_information(self):
        """"95%" is a percentile over the INFORMATIVE resamples only."""
        clean = bootstrap_kappa(pairs(agree_pass=15, agree_fail=15, harsh=2, lenient=2))
        assert clean["usable"]
        assert clean["coverage_of_total_mass"] > 0.9

        lopsided = bootstrap_kappa(pairs(agree_pass=17, agree_fail=2, harsh=1))
        if lopsided["usable"]:
            assert lopsided["coverage_of_total_mass"] < clean["coverage_of_total_mass"]

    def test_precision_is_reported_rather_than_claimed(self):
        """The docstring used to claim "stable to the third decimal". It was not."""
        measured = agreement_precision(pairs(agree_pass=6, agree_fail=6, harsh=2, lenient=2))
        assert measured["usable"]
        assert measured["spread"] is not None and measured["spread"] >= 0


class TestThePairedDifference:
    def test_it_refuses_unaligned_inputs(self):
        with pytest.raises(ValueError, match="one ceiling pair per judged row"):
            paired_difference(pairs(agree_pass=4, agree_fail=4), pairs(agree_pass=4))

    def test_a_judge_identical_to_the_labeller_differs_by_nothing(self):
        judge_pairs, ceiling_pairs = rows_and_judge(20, errors=0)
        result = paired_difference(judge_pairs, ceiling_pairs)

        assert result["usable"]
        assert result["point"] == pytest.approx(0.0)
        assert result["low"] <= 0, "and so it reaches the ceiling"

    def test_a_worse_judge_differs_by_a_positive_amount(self):
        judge_pairs, ceiling_pairs = rows_and_judge(30, errors=6)
        result = paired_difference(judge_pairs, ceiling_pairs)

        assert result["usable"]
        assert result["point"] > 0
        assert result["low"] > 0, "the whole interval says the human is better"

    def test_it_is_paired_and_not_two_independent_intervals(self):
        """The property that makes (b2) correctly calibrated.

        Differencing two marginal intervals would give a width of roughly the sum
        of theirs. Measured within each resample, the shared first-pass label
        vector cancels, so the difference interval is materially tighter.
        """
        judge_pairs, ceiling_pairs = rows_and_judge(40, errors=10, human_errors=6)
        judge = bootstrap_kappa(judge_pairs)
        ceiling = human_ceiling(ceiling_pairs)
        difference = paired_difference(judge_pairs, ceiling_pairs)

        assert judge["usable"] and ceiling["usable"] and difference["usable"]
        marginal_width = (ceiling["high"] - ceiling["low"]) + (judge["high"] - judge["low"])
        assert (difference["high"] - difference["low"]) < marginal_width, (
            "the labeller must have variance of their own, or there is nothing to cancel"
        )


class TestTheThreePartGate:
    STRONG = pairs(agree_pass=9, agree_fail=9, harsh=1, lenient=1)

    def test_an_unmeasured_ceiling_is_never_a_pass(self):
        verdict = calibration_verdict(bootstrap_kappa(self.STRONG), None)

        assert verdict["beats_chance"] is True
        assert verdict["reaches_ceiling"] is None
        assert verdict["calibrated"] is False
        assert any("no second labelling pass" in r for r in verdict["reasons"])

    def test_the_instruction_names_the_sheet_that_exists(self):
        """It named `human_verdict_2`, a column this project decided not to build.

        That string was the first thing a stuck owner read on the primary exit-3
        path, and it told them to do something impossible. Three of the four
        reviewers found it independently.
        """
        joined = " ".join(calibration_verdict(bootstrap_kappa(self.STRONG), None)["reasons"])

        assert "human_scores_pass2.csv" in joined
        assert "human_verdict_2" not in joined

    def test_an_unusable_ceiling_says_so_instead_of_asking_for_it_again(self):
        """It printed "has never been measured" over a ceiling that WAS measured.

        It fires on the realistic shape: ten rows, eight scorable, seven pass and
        one fail. Telling the owner to re-label work they already did, when the
        problem is the row mix, wastes an evening and fixes nothing.
        """
        judge = bootstrap_kappa(self.STRONG)
        ceiling = human_ceiling([(True, True)] * 10)

        verdict = calibration_verdict(judge, ceiling)

        assert ceiling["usable"] is False
        assert verdict["calibrated"] is False
        assert any("EXISTS and was read" in r for r in verdict["reasons"])
        assert any("not a request to label them again" in r for r in verdict["reasons"])

    def test_a_labeller_who_cannot_reproduce_themselves_passes_nobody(self):
        """Half (b1), and the defect two reviewers found independently.

        A second pass that disagrees with the first about as often as chance sets
        no ceiling. The old rule read its low bound anyway, so the WORSE the
        labeller the lower the bar, and a coin-flip second pass produced exit 0
        CALIBRATED for a judge nobody had a scale for.
        """
        judge_pairs, _ = rows_and_judge(20, errors=5)
        noisy = pairs(agree_pass=5, agree_fail=5, harsh=5, lenient=5)

        verdict = calibration_verdict(
            bootstrap_kappa(judge_pairs),
            human_ceiling(noisy),
            paired_difference(judge_pairs, noisy),
        )

        assert verdict["ceiling_beats_chance"] is False
        assert verdict["reaches_ceiling"] is None
        assert verdict["calibrated"] is False
        assert any("no ceiling at all" in r for r in verdict["reasons"])

    def test_a_judge_that_matches_the_labeller_is_calibrated(self):
        verdict = verdict_for(20, errors=0)

        assert verdict["beats_chance"] is True
        assert verdict["ceiling_beats_chance"] is True
        assert verdict["reaches_ceiling"] is True
        assert verdict["calibrated"] is True

    def test_a_judge_measurably_worse_than_the_labeller_fails_b2(self):
        verdict = verdict_for(30, errors=6)

        assert verdict["beats_chance"] is True
        assert verdict["ceiling_beats_chance"] is True
        assert verdict["reaches_ceiling"] is False
        assert verdict["calibrated"] is False
        assert any("(b2) FAILED" in r for r in verdict["reasons"])

    def test_a_fixed_error_rate_does_not_change_verdict_with_n(self):
        """THE regression test for the constant nobody chose.

        Under `judge_high >= ceiling_low` the rule was "at most 3 disagreements"
        at every n, so a judge with a FIXED 20% error rate passed at n=12 (2
        errors, under the limit) and failed at n=40 (8 errors, over it). Nothing
        about the judge changed between those runs; only how much work the owner
        had done.

        A judge genuinely worse than the labeller must fail at every n.
        """
        verdicts = {n: verdict_for(n, errors=n // 5) for n in (20, 30, 40)}

        for n, verdict in verdicts.items():
            assert verdict["reaches_ceiling"] is False, (
                f"a judge wrong about a fifth of the rows reached the ceiling at n={n}"
            )
        assert len({v["calibrated"] for v in verdicts.values()}) == 1, (
            "the verdict on one error RATE must not depend on how many rows were labelled"
        )

    def test_the_verdict_is_decided_by_the_measured_intervals_and_nothing_else(self):
        import inspect

        from tests.evals.calibration import agreement

        source = inspect.getsource(agreement.calibration_verdict)
        for literal in ("0.6", "0.7", "0.8"):
            assert literal not in source, f"a chosen threshold came back into the gate: {literal}"
        assert 'judge["low"]' in source
        assert 'ceiling["low"]' in source
        assert 'difference["low"]' in source

    def test_every_unevaluated_part_is_None_and_never_False(self):
        """An absence and a failure route to different exit codes, so they have to
        stay different values all the way down."""
        verdict = calibration_verdict(bootstrap_kappa([(True, True)] * 10), None)

        assert verdict["beats_chance"] is None
        assert verdict["ceiling_beats_chance"] is None
        assert verdict["reaches_ceiling"] is None
        assert verdict["calibrated"] is False


# ---------------------------------------------------------------------------
# The survivors. Every test below exists because a mutation stayed GREEN.
# ---------------------------------------------------------------------------


def interval(low, high, *, usable=True, point=None, spans=False):
    """A hand-made interval, so a boundary can be tested AT the boundary.

    The bootstrap never happens to produce `low == 0.0` on any corpus in this
    file, which is why `judge["low"] > 0` survived being weakened to `>= 0`
    through 44 mutations. Feeding the verdict a synthetic interval tests the
    comparison itself rather than hoping a resample lands on the point.
    """
    return {
        "point": high if point is None else point,
        "low": low,
        "high": high,
        "undefined_fraction": 0.0,
        "coverage_of_total_mass": 0.95,
        "spans_the_whole_range": spans,
        "usable": usable,
    }


class TestTheBoundariesThemselves:
    """`> 0`, `> 0`, `<= 0`. Three comparisons decide everything here."""

    def test_a_judge_exactly_at_chance_does_not_beat_chance(self):
        """`low > 0` weakened to `>= 0` survived 44 mutations, because no corpus
        in the suite produced a lower bound of exactly zero."""
        verdict = calibration_verdict(interval(0.0, 0.6), None)
        assert verdict["beats_chance"] is False

        assert calibration_verdict(interval(1e-9, 0.6), None)["beats_chance"] is True

    def test_a_ceiling_exactly_at_chance_sets_no_scale(self):
        judge = interval(0.2, 0.9)
        verdict = calibration_verdict(judge, interval(0.0, 0.8), interval(-0.1, 0.1))
        assert verdict["ceiling_beats_chance"] is False
        assert verdict["calibrated"] is False

    def test_a_difference_of_exactly_zero_still_reaches_the_ceiling(self):
        """(b2) rejects only when the WHOLE interval is above 0. A lower bound
        sitting exactly on 0 has not shown the human to be better."""
        verdict = calibration_verdict(
            interval(0.2, 0.9), interval(0.3, 0.95), interval(0.0, 0.4)
        )
        assert verdict["reaches_ceiling"] is True
        assert verdict["calibrated"] is True

        worse = calibration_verdict(
            interval(0.2, 0.9), interval(0.3, 0.95), interval(1e-9, 0.4)
        )
        assert worse["reaches_ceiling"] is False


class TestTheTailsAreTwoSided:
    def test_a_95_percent_interval_excludes_2_and_a_half_percent_each_side(self):
        """`(1 - confidence) / 2` became `(1 - confidence)` and 87 tests passed."""
        assert tails(0.95) == pytest.approx((0.025, 0.975))
        assert tails(0.90) == pytest.approx((0.05, 0.95))
        assert tails(0.99) == pytest.approx((0.005, 0.995))

    def test_the_tails_are_symmetric_at_every_confidence(self):
        for confidence in (0.80, 0.90, 0.95, 0.99):
            low, high = tails(confidence)
            assert low == pytest.approx(1 - high)
            assert high - low == pytest.approx(confidence)


class TestExactArithmeticAtZero:
    def test_a_table_whose_kappa_is_exactly_zero_returns_exactly_zero(self):
        """Measured 2026-08-19: 46 reachable tables at n <= 40 where float
        arithmetic returns a positive value for a kappa that is exactly 0. This
        is the smallest of them, and `beats_chance` is `low > 0`.
        """
        cells = {"both_pass": 2, "judge_too_harsh": 1,
                 "judge_too_lenient": 6, "both_fail": 3}
        assert cohens_kappa(cells) == 0.0
        assert not cohens_kappa(cells) > 0, "float arithmetic returns 9.5e-17 here"

        bigger = {"both_pass": 2, "judge_too_harsh": 3,
                  "judge_too_lenient": 6, "both_fail": 9}
        assert cohens_kappa(bigger) == 0.0
        assert not cohens_kappa(bigger) > 0


class TestThePairingIsReal:
    def test_a_judge_identical_to_the_labeller_differs_by_exactly_nothing(self):
        """The sharpest possible statement of "the indices are shared".

        Feed the same rows as both judge and ceiling. Every resample must give
        the same kappa twice, so the difference is 0 in every one of them and the
        interval has zero width. Resampling the two independently cannot produce
        that, so this is what stops the pairing being quietly dropped.
        """
        same = pairs(agree_pass=9, agree_fail=9, harsh=1, lenient=1)
        result = paired_difference(same, same)

        assert result["usable"]
        assert result["low"] == 0.0 and result["high"] == 0.0, (
            "independent resampling would spread this interval"
        )
        assert result["point"] == pytest.approx(0.0)
