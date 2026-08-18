"""pass@k and reliable@k say different things, and the difference is the diagnosis.

BACKLOG 8.1. Every eval scenario ran exactly once, so no number this system has
produced separates "cannot" from "sometimes". They prescribe opposite work:
pass@k near zero means prompt tuning is wasted effort, and high pass@k with low
reliable@k means the remaining work is variance.

The load-bearing test is `test_a_ragged_corpus_weights_scenarios_not_runs`. A
capture that errors partway leaves some scenarios at k=5 and some at k=1, and
totalling successes over runs then lets the scenarios that happened to get more
runs decide the number.
"""

from __future__ import annotations

import pytest

from tests.evals import rates


class TestOneScenario:
    def test_five_of_five_is_capable_and_consistent(self):
        r = rates.scenario_rates([True] * 5)
        assert r["k"] == 5
        assert r["pass_at_k"] is True
        assert r["reliable_at_k"] == 1.0

    def test_one_of_five_is_capable_and_not_consistent(self):
        """The case a k=1 corpus cannot see, and the reason 8.1 exists.

        A single run of this scenario returns PASS 20% of the time and FAIL 80%,
        and either reading looks exactly like a deterministic verdict.
        """
        r = rates.scenario_rates([False, True, False, False, False])
        assert r["pass_at_k"] is True, "it CAN: the work is variance, not capability"
        assert r["reliable_at_k"] == pytest.approx(0.2)

    def test_zero_of_five_is_cannot_not_sometimes(self):
        r = rates.scenario_rates([False] * 5)
        assert r["pass_at_k"] is False, (
            "prompt tuning is wasted effort here; the model, tools or architecture is the fix"
        )
        assert r["reliable_at_k"] == 0.0

    def test_at_k_one_the_two_metrics_are_the_same_number(self):
        """Which is the whole problem with the corpus as captured."""
        r = rates.scenario_rates([True])
        assert r["k"] == 1
        assert float(r["pass_at_k"]) == r["reliable_at_k"]

    def test_zero_runs_is_unknown_not_a_pass(self):
        with pytest.raises(ValueError, match="unknown, not a pass"):
            rates.scenario_rates([])


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

    def test_a_ragged_corpus_weights_scenarios_not_runs(self):
        """Mean of per-scenario rates, never total successes over total runs.

        S-001 has eight runs and always fails; S-002 has one run and passes.
        Total-over-total reports 1/9 = 11%. Weighting the two scenarios equally
        reports 50%, which is the statement about the SCENARIO SET that a rate is
        supposed to be. The two agree only while every scenario has the same k,
        and they diverge exactly when a capture errored partway.
        """
        agg = rates.aggregate({"S-001": [False] * 8, "S-002": [True]})
        assert agg["reliable_at_k"] == pytest.approx(0.5)
        assert agg["reliable_at_k"] != pytest.approx(1 / 9)

    def test_ragged_k_is_visible_in_the_report_line(self):
        agg = rates.aggregate({"S-001": [True] * 5, "S-002": [True]})
        assert agg["k_min"] == 1 and agg["k_max"] == 5
        assert "RAGGED" in rates.describe("grounding_fidelity", agg)

    def test_k_travels_with_every_number(self):
        """A rate at k=1 and a rate at k=5 read identically once k leaves the sentence."""
        line = rates.describe("grounding_fidelity", rates.aggregate({"S-001": [True]}))
        assert "k=1" in line
        assert "pass@k" in line and "reliable@k" in line

    def test_an_empty_dimension_reports_no_rate_rather_than_a_perfect_one(self):
        agg = rates.aggregate({})
        assert agg["pass_at_k"] is None and agg["reliable_at_k"] is None
        assert "no scenarios rated" in rates.describe("grounding_fidelity", agg)
