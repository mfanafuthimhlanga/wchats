"""The calibration threshold comes from the data, not from a constant.

BACKLOG 8.2c. The gate used to be `kappa >= 0.6`, a number taken from a band in
a talk and admitted in its own comment to be a choice. Owner instruction
2026-08-18: "the kappa measurement must not be a choice it must be derived from
data."

Two load-bearing tests here.

`test_a_coin_flip_judge_does_not_beat_chance` is half (a): with an interval, a
judge whose point estimate is 0.0 is refused because the interval straddles
zero, and a small sample cannot fake its way past that the way a point estimate
can.

`test_an_unmeasured_ceiling_is_never_a_pass` is half (b), and it is the one that
makes the threshold data-derived rather than merely uncertain. A judge cannot be
expected to agree with a human more than that human agrees with themself, so the
human's own test-retest kappa is the scale. With no ceiling there is no scale,
and the harness must refuse rather than fall back to a number.
"""

from __future__ import annotations

import pytest

from tests.evals.calibration.agreement import (
    MAX_UNDEFINED_FRACTION,
    bootstrap_kappa,
    calibration_verdict,
    human_ceiling,
)


def pairs(agree_pass: int = 0, agree_fail: int = 0, harsh: int = 0, lenient: int = 0):
    """(human_passed, judge_passed) pairs in the four confusion cells."""
    return (
        [(True, True)] * agree_pass
        + [(False, False)] * agree_fail
        + [(True, False)] * harsh
        + [(False, True)] * lenient
    )


class TestTheBootstrap:
    def test_it_is_deterministic_for_the_same_data(self):
        """An unseeded bootstrap makes the gate flicker between runs on one corpus."""
        sample = pairs(agree_pass=9, agree_fail=9, harsh=1, lenient=1)
        first, second = bootstrap_kappa(sample), bootstrap_kappa(sample)
        assert first == second

    def test_a_strong_judge_clears_chance(self):
        result = bootstrap_kappa(pairs(agree_pass=9, agree_fail=9, harsh=1, lenient=1))
        assert result["usable"]
        assert result["point"] == pytest.approx(0.8)
        assert result["low"] > 0

    def test_a_coin_flip_judge_does_not_beat_chance(self):
        """Half (a). The point estimate is 0.0; the interval is what refuses it.

        A judge that agrees on exactly half a balanced set carries no
        information. Its interval straddles zero, and no amount of luck on a
        small sample moves the lower bound above it.
        """
        result = bootstrap_kappa(pairs(agree_pass=5, agree_fail=5, harsh=5, lenient=5))
        assert result["usable"]
        assert result["point"] == pytest.approx(0.0)
        assert result["low"] <= 0 <= result["high"]
        assert calibration_verdict(result, None)["beats_chance"] is False

    def test_a_one_sided_corpus_is_refused_rather_than_scored(self):
        """Every label the same: kappa is undefined and so is every resample.

        Reporting a percentile over the few resamples that happened to contain a
        minority label would describe a corpus nobody has.
        """
        result = bootstrap_kappa([(True, True)] * 10)
        assert result["usable"] is False
        assert result["undefined_fraction"] > MAX_UNDEFINED_FRACTION
        assert result["low"] is None and result["high"] is None

    def test_a_nearly_one_sided_corpus_is_also_refused(self):
        """The realistic version, and the test that actually pins the fraction.

        Nine pass, one fail: this corpus's actual expected shape. It skipped
        itself when the resamples stayed definable, which made it unable to
        fail - and MAX_UNDEFINED_FRACTION was then pinned by nothing, because
        the entirely-one-sided test above is caught one line earlier by
        `defined` being empty. Observed by mutation 2026-08-18: deleting the
        fraction check left that test green.

        Two thirds of the resamples here ARE defined, so a percentile could be
        taken. It must not be: the third that dropped the single `fail` describe
        a corpus with no disagreement in it at all, and the interval would be
        reporting on those as though they were observations.
        """
        result = bootstrap_kappa(pairs(agree_pass=9, agree_fail=1))

        assert result["undefined_fraction"] > MAX_UNDEFINED_FRACTION, (
            "the premise: enough resamples lose the minority label"
        )
        assert result["undefined_fraction"] < 1.0, (
            "and enough survive that a number COULD have been returned"
        )
        assert result["usable"] is False
        assert result["low"] is None and result["high"] is None

    def test_too_few_pairs_is_not_a_measurement(self):
        assert bootstrap_kappa([(True, True)])["usable"] is False
        assert bootstrap_kappa([])["usable"] is False

    def test_the_interval_widens_as_the_sample_shrinks(self):
        """Small n must not be able to buy a confident number.

        Both samples carry the SAME disagreement ratio, so the point estimate is
        the same and only n differs. A perfectly-agreeing judge would give a
        zero-width interval at every n and prove nothing here.
        """
        small = bootstrap_kappa(pairs(agree_pass=6, agree_fail=2, harsh=1, lenient=1))
        large = bootstrap_kappa(pairs(agree_pass=60, agree_fail=20, harsh=10, lenient=10))

        assert small["usable"] and large["usable"]
        assert small["point"] == pytest.approx(large["point"], abs=0.01)
        assert (small["high"] - small["low"]) > (large["high"] - large["low"]), (
            "ten rows must not produce as tight an interval as a hundred"
        )


class TestTheTwoPartGate:
    STRONG = pairs(agree_pass=9, agree_fail=9, harsh=1, lenient=1)

    def test_an_unmeasured_ceiling_is_never_a_pass(self):
        """Half (b), and the reason the threshold is data-derived at all.

        The judge here beats chance comfortably. It is still NOT calibrated,
        because nothing has established what agreement is achievable on this
        corpus by the person who labelled it.
        """
        verdict = calibration_verdict(bootstrap_kappa(self.STRONG), None)

        assert verdict["beats_chance"] is True
        assert verdict["reaches_ceiling"] is None
        assert verdict["calibrated"] is False
        assert any("HUMAN CEILING" in reason for reason in verdict["reasons"])

    def test_a_judge_that_reaches_the_ceiling_is_calibrated(self):
        judge = bootstrap_kappa(self.STRONG)
        ceiling = human_ceiling(self.STRONG)
        assert calibration_verdict(judge, ceiling)["calibrated"] is True

    def test_a_judge_below_the_ceiling_fails_and_the_report_says_which_half(self):
        """Beats chance, does not reach the labeller. Different fix, so different message."""
        judge = bootstrap_kappa(pairs(agree_pass=14, agree_fail=14, harsh=6, lenient=6))
        ceiling = human_ceiling(pairs(agree_pass=20, agree_fail=20))

        verdict = calibration_verdict(judge, ceiling)
        assert verdict["beats_chance"] is True
        assert verdict["reaches_ceiling"] is False
        assert verdict["calibrated"] is False
        assert any("below the human's own lower bound" in r for r in verdict["reasons"])

    def test_an_unusable_ceiling_counts_as_no_ceiling(self):
        """A human who labelled everything the same way has not set a ceiling."""
        judge = bootstrap_kappa(self.STRONG)
        ceiling = human_ceiling([(True, True)] * 10)

        verdict = calibration_verdict(judge, ceiling)
        assert ceiling["usable"] is False
        assert verdict["calibrated"] is False
        assert any("HUMAN CEILING" in r for r in verdict["reasons"])

    def test_an_unusable_judge_interval_stops_before_the_ceiling_question(self):
        verdict = calibration_verdict(bootstrap_kappa([(True, True)] * 10), None)
        assert verdict["beats_chance"] is None
        assert verdict["calibrated"] is False
        assert any("not a measurement" in r for r in verdict["reasons"])

    def test_no_constant_decides_anything(self):
        """The property the row exists for, asserted rather than described."""
        import inspect

        from tests.evals.calibration import agreement

        source = inspect.getsource(agreement.calibration_verdict)
        assert "0.6" not in source, "a chosen threshold came back into the gate"
        assert "judge[" in source and "ceiling[" in source, (
            "the verdict must be decided by the two measured intervals and nothing else"
        )
