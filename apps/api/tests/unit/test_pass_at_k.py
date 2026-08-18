"""pass@k and reliable@k say different things, and the difference is the diagnosis.

BACKLOG 8.1. Every eval scenario ran exactly once, so no number this system has
produced separates "cannot" from "sometimes". They prescribe opposite work:
pass@k near zero means prompt tuning is wasted effort, and high pass@k with low
reliable@k means the remaining work is variance.

Three load-bearing tests.

`test_a_ragged_corpus_weights_scenarios_not_runs` - reliable@k must be the mean
of per-scenario rates, not total successes over total runs.

`test_pass_at_k_is_refused_on_a_ragged_corpus` - pass@k cannot be rescued by
weighting, because the quantity itself grows with k. Adversarial review
2026-08-18 found the ragged fix had been applied to reliable@k only, and that
two scenarios both plausibly p=1/8 reported `pass@k 50%` purely because one was
sampled eight times.

`TestDescribeCannotMislead` - `describe` was asserted by four substring checks,
and four separate mutations survived them: swapping the two metrics' values,
printing a hardcoded 100%, printing RAGGED unconditionally, and printing the k
range backwards. `"k=1"` is a substring of `"k=1-1 RAGGED"`, which is how the
false-RAGGED mutant slipped through.
"""

from __future__ import annotations

import pytest

from tests.evals import rates


class TestOneScenario:
    def test_five_of_five_is_capable_and_consistent(self):
        r = rates.scenario_rates([True] * 5)
        assert r["k"] == 5
        assert r["ever_passed"] is True
        assert r["reliable_at_k"] == 1.0

    def test_one_of_five_is_capable_and_not_consistent(self):
        """The case a k=1 corpus cannot see, and the reason 8.1 exists.

        A single run of this scenario returns PASS 20% of the time and FAIL 80%,
        and either reading looks exactly like a deterministic verdict.
        """
        r = rates.scenario_rates([False, True, False, False, False])
        assert r["ever_passed"] is True, "it CAN: the work is variance, not capability"
        assert r["reliable_at_k"] == pytest.approx(0.2)

    def test_zero_of_five_is_cannot_not_sometimes(self):
        r = rates.scenario_rates([False] * 5)
        assert r["ever_passed"] is False, (
            "prompt tuning is wasted effort here; the model, tools or architecture is the fix"
        )
        assert r["reliable_at_k"] == 0.0

    def test_at_k_one_the_two_metrics_are_the_same_number(self):
        """Which is the whole problem with the corpus as captured."""
        r = rates.scenario_rates([True])
        assert r["k"] == 1
        assert float(r["ever_passed"]) == r["reliable_at_k"]

    def test_zero_runs_is_unknown_not_a_pass(self):
        with pytest.raises(ValueError, match="unknown, not a pass"):
            rates.scenario_rates([])


class TestOnlyBooleansAreOutcomes:
    """A verdict string or a None here is an extraction bug in the caller.

    Truthiness counted `"no"`, `"FAIL"` and `"0"` as PASSES, counted an
    unextracted verdict dict as a pass, and counted `None` - what an errored or
    unparsed run looks like - as an observed FAILURE, converting unknown into
    fail while keeping it in the denominator.
    """

    @pytest.mark.parametrize(
        "outcomes",
        [
            pytest.param(["no", "FAIL", "0"], id="verdict-strings-that-mean-fail"),
            pytest.param([1, 0], id="ints"),
            pytest.param([None], id="none-is-unknown-not-fail"),
            pytest.param([{"passed": False}], id="unextracted-verdict-dict"),
        ],
    )
    def test_a_non_boolean_outcome_raises(self, outcomes):
        with pytest.raises(TypeError, match="not a bool"):
            rates.scenario_rates(outcomes)

    def test_a_string_is_not_a_sequence_of_outcomes(self):
        """`"pass"` is a Sequence, so it used to rate as four passing runs."""
        with pytest.raises(TypeError, match="not a string"):
            rates.scenario_rates("pass")


class TestAcrossScenarios:
    def test_the_two_failure_kinds_are_kept_apart(self):
        agg = rates.aggregate({
            "S-001": [True] * 4,
            "S-002": [True, False, False, False],
            "S-003": [False] * 4,
        })
        assert agg["never_passed"] == ["S-003"], "cannot: change the model, tools or architecture"
        assert agg["flaky"] == ["S-002"], "sometimes: the work is variance"
        assert agg["pass_at_k"] == pytest.approx(2 / 3)
        assert agg["reliable_at_k"] == pytest.approx((1.0 + 0.25 + 0.0) / 3)

    def test_never_passed_and_flaky_are_sorted(self):
        """Order is asserted so a reversed list cannot pass as a sorted one."""
        agg = rates.aggregate({
            "S-003": [False], "S-001": [False], "S-002": [False],
            "S-006": [True, False], "S-004": [True, False], "S-005": [True, False],
        })
        assert agg["never_passed"] == ["S-001", "S-002", "S-003"]
        assert agg["flaky"] == ["S-004", "S-005", "S-006"]

    def test_scenarios_counts_scenarios_not_runs(self):
        agg = rates.aggregate({"S-001": [True] * 7, "S-002": [False] * 3})
        assert agg["scenarios"] == 2, "ten runs, two scenarios"

    def test_a_ragged_corpus_weights_scenarios_not_runs(self):
        """reliable@k is the mean of per-scenario rates, never total over total.

        S-001 has eight runs and always fails; S-002 has one run and passes.
        Total-over-total reports 1/9 = 11%. Weighting the two scenarios equally
        reports 50%, which is the statement about the SCENARIO SET that a rate is
        supposed to be.
        """
        agg = rates.aggregate({"S-001": [False] * 8, "S-002": [True]})
        assert agg["reliable_at_k"] == pytest.approx(0.5)

    def test_total_over_total_would_have_given_a_different_answer(self):
        """F16: the previous version of this assertion could not fail.

        It read `!= approx(1/9)` on the line after asserting `== approx(0.5)`,
        so it was unreachable as a failure. Computing the rejected quantity
        here makes the comparison real.
        """
        outcomes = {"S-001": [False] * 8, "S-002": [True]}
        agg = rates.aggregate(outcomes)
        total_over_total = (
            sum(sum(o) for o in outcomes.values()) / sum(len(o) for o in outcomes.values())
        )
        assert total_over_total == pytest.approx(1 / 9)
        assert agg["reliable_at_k"] != pytest.approx(total_over_total)

    def test_pass_at_k_is_refused_on_a_ragged_corpus(self):
        """It cannot be rescued by weighting; the quantity itself grows with k.

        Both scenarios are plausibly p = 1/8. S-B "can" only because it was
        sampled eight times, and averaging an indicator whose expectation is
        1-(1-p)^k across uneven k reports the capture, not the agent.
        """
        agg = rates.aggregate({"S-A": [False], "S-B": [True] + [False] * 7})
        assert agg["ragged"] is True
        assert agg["pass_at_k"] is None
        assert "grows with k" in agg["pass_at_k_unavailable"]
        assert agg["reliable_at_k"] is not None, "reliable@k is unbiased at any k and stays"

    def test_an_even_corpus_still_reports_pass_at_k(self):
        agg = rates.aggregate({"S-A": [True, False], "S-B": [False, False]})
        assert agg["ragged"] is False
        assert agg["pass_at_k"] == pytest.approx(0.5)
        assert agg["pass_at_k_unavailable"] is None

    def test_an_empty_dimension_reports_no_rate_rather_than_a_perfect_one(self):
        agg = rates.aggregate({})
        assert agg["pass_at_k"] is None and agg["reliable_at_k"] is None
        assert "no scenarios rated" in rates.describe("grounding_fidelity", agg)


class TestDescribeCannotMislead:
    """Four mutations survived this function's original assertions."""

    def test_counts_travel_with_every_percentage(self):
        """249 of 250 rounds to 100%, and used to print exactly that.

        The counts are what make one known failure visible in a line someone
        reads at a glance.
        """
        outcomes = {f"S-{i:03d}": [True] for i in range(249)}
        outcomes["S-249"] = [False]
        line = rates.describe("citation", rates.aggregate(outcomes))

        assert "(249/250)" in line, "the percentage rounds to 100%; the count must not"
        assert "1 never" in line

    def test_the_two_metrics_are_not_interchangeable(self):
        """A mutation that swapped their values survived every earlier check."""
        agg = rates.aggregate({"S-1": [True, True], "S-2": [True, False], "S-3": [False, False]})
        line = rates.describe("d", agg)

        assert agg["pass_at_k"] == pytest.approx(2 / 3)
        assert agg["reliable_at_k"] == pytest.approx(0.5)
        assert "pass@k 67%" in line
        assert "reliable@k 50%" in line

    def test_a_non_ragged_corpus_does_not_say_ragged(self):
        """`"k=1"` is a substring of `"k=1-1 RAGGED"`, so substring checks passed a false RAGGED."""
        line = rates.describe("d", rates.aggregate({"S-1": [True], "S-2": [False]}))
        assert "RAGGED" not in line
        assert line.endswith("k=1")

    def test_a_ragged_corpus_prints_its_range_low_to_high(self):
        line = rates.describe("d", rates.aggregate({"S-1": [True], "S-2": [True] * 8}))
        assert "k=1-8 RAGGED" in line
        assert "k=8-1" not in line

    def test_a_ragged_line_does_not_quote_a_pass_at_k_percentage(self):
        line = rates.describe("d", rates.aggregate({"S-1": [True], "S-2": [True] * 8}))
        assert "pass@k unavailable" in line
        assert "pass@k 100%" not in line

    def test_k_travels_with_every_number(self):
        """A rate at k=1 and a rate at k=5 read identically once k leaves the sentence."""
        line = rates.describe("grounding_fidelity", rates.aggregate({"S-001": [True]}))
        assert "k=1" in line
        assert "pass@k" in line and "reliable@k" in line
