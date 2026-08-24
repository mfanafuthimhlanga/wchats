"""Cohen's kappa, Matthews, and the 2x2 they are read from.

BACKLOG 8.2b. These replace Spearman as the judge-calibration gate, so they are
tested as arithmetic in their own right rather than only through the harness.

The case that matters most is `test_a_judge_that_passes_everything_scores_zero`.
Raw agreement and rank correlation both reward a judge that returns PASS to
every input; kappa subtracts the rate at which two raters agree by chance and
refuses it. That is the whole reason the gate moved.

The second is `test_kappa_collapses_on_an_imbalanced_corpus`. Kappa has a known
failure mode on lopsided data, which is exactly the data this project has
(mostly-good responses), and pretending otherwise would replace one
misunderstood number with another. Matthews is reported for that case, and the
test shows the gap rather than describing it.
"""

from __future__ import annotations

import math

import pytest

from tests.evals.calibration.agreement import cohens_kappa, confusion
from tests.evals.calibration.compute_correlation import matthews


def _cells(both_pass=0, judge_too_harsh=0, judge_too_lenient=0, both_fail=0):
    return {
        "both_pass": both_pass,
        "judge_too_harsh": judge_too_harsh,
        "judge_too_lenient": judge_too_lenient,
        "both_fail": both_fail,
    }


class TestTheConfusionMatrix:
    """Four cells, four different actions. That is why it is not one number."""

    def test_each_pair_lands_in_its_own_cell(self):
        cells = confusion([
            (True, True),    # both pass
            (True, False),   # judge too harsh
            (False, True),   # judge too lenient
            (False, False),  # both fail
        ])
        assert cells == _cells(1, 1, 1, 1)

    def test_the_dangerous_cell_is_named_for_what_it_means(self):
        """Judge PASS over human FAIL is bad answers reaching customers."""
        cells = confusion([(False, True), (False, True)])
        assert cells["judge_too_lenient"] == 2
        assert cells["judge_too_harsh"] == 0

    def test_an_empty_set_is_all_zeros_not_an_error(self):
        assert confusion([]) == _cells()


class TestCohensKappa:
    def test_perfect_agreement_is_one(self):
        assert cohens_kappa(_cells(both_pass=2, both_fail=2)) == pytest.approx(1.0)

    def test_chance_level_agreement_is_zero(self):
        """Half right on a balanced set is exactly what a coin achieves."""
        assert cohens_kappa(_cells(1, 1, 1, 1)) == pytest.approx(0.0)

    def test_perfect_disagreement_is_minus_one(self):
        """The exact value, not `< 0`: a mutation halving it would survive that."""
        assert cohens_kappa(_cells(judge_too_harsh=2, judge_too_lenient=2)) == pytest.approx(-1.0)

    def test_a_judge_that_passes_everything_scores_zero(self):
        """The defect that moved the gate, and CHANGED ANSWER in 8.2d.

        The human failed half the rows; the judge passed all of them. Raw
        agreement is 50%, and Spearman over the underlying 1-5 scores reported
        1.000 because a constant judge ranks in agreement with any rising human.

        This used to assert kappa == 0.0. It is NaN now, and the difference
        matters: with the judge's marginal degenerate, `observed == expected`
        identically, so 0.0 was arriving no matter which rows the judge passed.
        0.0 means "measured, and no better than chance" - a finding about the
        judge. NaN means "this table cannot say" - an absence. Reporting the
        second as the first is how a lopsided corpus gets blamed on a judge, and
        it pinned bootstrap lower bounds at zero (four adversarial reviews,
        2026-08-19).

        The CONCLUSION is unchanged, which is the point: a judge that passes
        everything is still refused, and `test_a_judge_that_passes_everything_is_refused`
        in the harness tests still holds.
        """
        cells = confusion([(True, True), (True, True), (False, True), (False, True)])
        assert cells["judge_too_lenient"] == 2
        assert math.isnan(cohens_kappa(cells)), (
            "a degenerate judge marginal is an absence of evidence, not evidence of chance"
        )

    def test_a_degenerate_HUMAN_marginal_is_also_undefined(self):
        """The other side of the same coin, and the one that bites in practice.

        Every human label is `pass` and the judge splits. Kappa was 0.0 here too,
        for the same arithmetic reason, and the harness reported that as a judge
        failing to beat chance - exit 1, "fix the judge" - over a sheet on which
        no judge could have scored anything else.
        """
        cells = confusion([(True, True), (True, True), (True, False), (True, False)])
        assert math.isnan(cohens_kappa(cells))

    def test_exact_arithmetic_leaves_no_dust_at_zero(self):
        """A mathematically exact 0 must not come back as 2.66e-16.

        `observed` and `expected` reach the same value by different routes. In
        float that produced 2.664535259100375e-16 on this table, and the gate
        tests `low > 0`, so half (a) passed on dust: the same corpus returned
        CALIBRATED at one seed and NOT CALIBRATED at another, both printing
        `[+0.000, +1.000]`.
        """
        cells = {"both_pass": 0, "judge_too_harsh": 5,
                 "judge_too_lenient": 0, "both_fail": 7}
        assert math.isnan(cohens_kappa(cells)), "this table's judge marginal is degenerate"

        # And a genuinely balanced chance-level table lands on exactly 0.0.
        chance = confusion([(True, True), (True, False), (False, True), (False, False)])
        assert cohens_kappa(chance) == 0.0
        assert not cohens_kappa(chance) > 0, "no dust may cross the (a) boundary"

    def test_total_agreement_on_one_label_is_undefined_not_perfect(self):
        """Both raters passed everything, so a coin agrees just as often.

        Returning 1.0 here would be the single most dangerous number this file
        could produce: a corpus of twenty good responses would certify any judge
        that says PASS. NaN is reported as "not calibrated yet".
        """
        assert math.isnan(cohens_kappa(_cells(both_pass=10)))
        assert math.isnan(cohens_kappa(_cells(both_fail=10)))

    def test_an_empty_set_is_undefined(self):
        assert math.isnan(cohens_kappa(_cells()))

    def test_kappa_collapses_on_an_imbalanced_corpus(self):
        """The known failure mode, on the corpus shape this project actually has.

        Nineteen of twenty responses are good and the judge gets nineteen right,
        missing one. Raw agreement is 95%. Kappa is 0.0, because chance agreement
        is already near certain when one label dominates, and reading that as
        "the judge is poor" would be wrong.

        CORRECTED 2026-08-18 by adversarial review: an earlier version of this
        docstring said "Matthews is the statistic to read instead, and it is
        higher here". On THESE cells Matthews is NaN, not higher: the human never
        said FAIL, so a whole marginal is zero and MCC is undefined too. The
        honest statement is that a corpus this one-sided cannot measure a judge
        with either statistic, which is the case `8.2c`'s bootstrap refuses
        outright. The test below uses cells where MCC IS defined, and says so.
        """
        cells = _cells(both_pass=19, judge_too_harsh=1)
        kappa = cohens_kappa(cells)
        assert math.isnan(kappa) or kappa < 0.5, (
            "kappa is expected to look bad here; that is the failure mode, not the judge"
        )


class TestMatthews:
    def test_perfect_agreement_is_one(self):
        assert matthews(_cells(both_pass=2, both_fail=2)) == pytest.approx(1.0)

    def test_perfect_disagreement_is_minus_one(self):
        assert matthews(_cells(judge_too_harsh=2, judge_too_lenient=2)) == pytest.approx(-1.0)

    def test_it_survives_imbalance_that_kappa_does_not(self):
        """DIFFERENT cells from the collapse test, and the difference is the point.

        MCC is undefined on `(19,1,0,0)` because a marginal is zero. It needs at
        least one observation in each of the human's two labels. These cells add
        that one row, and MCC then reads higher than kappa on the same data.
        """
        cells = _cells(both_pass=18, both_fail=1, judge_too_harsh=1)
        mcc = matthews(cells)
        assert not math.isnan(mcc)
        # Not an OR. The claim is that MCC reads higher than kappa on imbalanced
        # data, and an `or mcc > 0.5` escape hatch would let the first half fail
        # silently.
        assert mcc > cohens_kappa(cells)

    def test_a_constant_judge_is_undefined_rather_than_flattering(self):
        """No variance on one side leaves a zero in the denominator."""
        assert math.isnan(matthews(_cells(both_pass=10, judge_too_lenient=5)))

    def test_an_empty_set_is_undefined(self):
        assert math.isnan(matthews(_cells()))
