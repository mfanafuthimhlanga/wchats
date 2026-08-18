"""The eval harness checks every run, and says which of the two failures it saw.

BACKLOG 8.1. `run_evals.py` made one pass per scenario, so a dimension that
failed reported a single FAIL and nothing about whether the agent CANNOT do the
task or only sometimes does. Those prescribe opposite work: never-passed means
the model, tools or architecture is the fix, and flaky means variance is.

The load-bearing test is `test_every_run_is_checked_not_just_the_first`. A
collector that reads run 0 and stops turns a 2-of-3 scenario into a clean PASS,
which is the shape of every "green for the wrong reason" defect this repo has
already paid for.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.evals import run_evals

CITED = (
    "Unopened bags may be returned within 14 days of delivery for a full refund, and we "
    "cover return shipping when the fault is ours.\n"
    "CITATIONS:\n- Document: HANDBOOK.pdf | Section: Returns"
)
UNCITED = "Unopened bags may be returned within 14 days of delivery for a full refund."

SCENARIO = {
    "id": "S-101",
    "category": "golden_path",
    "turns": [{"role": "user", "message": "what is your return policy"}],
    "deterministic_checks": {"D5": {}},
}


@pytest.fixture
def corpus_dir(tmp_path, monkeypatch) -> callable:
    """Write a k-run record for S-101 and point run_evals at it."""
    monkeypatch.setattr(run_evals, "RESPONSES_DIR", tmp_path)

    def write(*texts: str) -> pathlib.Path:
        path = tmp_path / "S-101.json"
        path.write_text(
            json.dumps({
                "scenario_id": "S-101",
                "runs": [{"response_text": t, "tool_calls_log": []} for t in texts],
            }),
            encoding="utf-8",
        )
        return path

    return write


class TestCollection:
    def test_every_run_is_checked_not_just_the_first(self, corpus_dir):
        """Run 0 passes, run 1 does not, run 2 does.

        A collector that stops after run 0 records [True] and the scenario reads
        as a clean pass at 100%. The corpus paid for three live agent turns and
        two of them would go unread.
        """
        corpus_dir(CITED, UNCITED, CITED)
        outcomes, reasons, skipped = run_evals.collect_deterministic([SCENARIO])

        assert outcomes["D5"]["S-101"] == [True, False, True]
        assert skipped == []
        assert "run 1" in reasons["D5"]["S-101"][0], "a reason must name the run it came from"

    def test_a_never_captured_scenario_is_skipped_not_failed(self, corpus_dir):
        outcomes, _reasons, skipped = run_evals.collect_deterministic([
            SCENARIO | {"id": "S-999"}
        ])
        assert skipped == ["S-999"]
        assert outcomes["D5"] == {}

    def test_a_pre_8_1_record_is_one_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_evals, "RESPONSES_DIR", tmp_path)
        (tmp_path / "S-101.json").write_text(
            json.dumps({"scenario_id": "S-101", "response_text": CITED, "tool_calls_log": []}),
            encoding="utf-8",
        )
        outcomes, _reasons, _skipped = run_evals.collect_deterministic([SCENARIO])
        assert outcomes["D5"]["S-101"] == [True]


class TestWhatAFailureSays:
    def test_a_flaky_scenario_is_named_flaky_with_its_count(self, corpus_dir):
        corpus_dir(CITED, UNCITED, CITED)
        outcomes, reasons, _ = run_evals.collect_deterministic([SCENARIO])

        messages = run_evals._dimension_failures("D5", outcomes["D5"], reasons["D5"])
        joined = "\n".join(messages)
        assert "FLAKY [S-101] 2/3" in joined, "the count is what says how much variance there is"
        assert "NEVER" not in joined
        assert "pass@k 100%" in joined and "reliable@k 67%" in joined

    def test_a_scenario_that_never_passes_is_named_never(self, corpus_dir):
        corpus_dir(UNCITED, UNCITED, UNCITED)
        outcomes, reasons, _ = run_evals.collect_deterministic([SCENARIO])

        joined = "\n".join(run_evals._dimension_failures("D5", outcomes["D5"], reasons["D5"]))
        assert "NEVER passed [S-101]" in joined, (
            "prompt tuning is wasted effort here; this is a capability failure"
        )
        assert "FLAKY" not in joined

    def test_every_run_passing_is_the_only_thing_that_passes(self, corpus_dir):
        corpus_dir(CITED, CITED, CITED)
        outcomes, reasons, _ = run_evals.collect_deterministic([SCENARIO])
        assert run_evals._dimension_failures("D5", outcomes["D5"], reasons["D5"]) == []

    def test_one_bad_run_out_of_five_still_fails_the_gate(self, corpus_dir):
        """The gate is reliable@k == 1.0, not "mostly passes".

        A P0 dimension that holds four times in five is a P0 dimension that does
        not hold, and at k=1 the fifth run is the one a customer gets.
        """
        corpus_dir(CITED, CITED, CITED, CITED, UNCITED)
        outcomes, reasons, _ = run_evals.collect_deterministic([SCENARIO])
        assert run_evals._dimension_failures("D5", outcomes["D5"], reasons["D5"]) != []

    def test_a_dimension_nobody_captured_reports_nothing_rather_than_passing(self):
        assert run_evals._dimension_failures("D5", {}, {}) == []


class TestNothingCheckedIsNotEverythingPassing:
    """Missing data is never passing data, one level up from `scenario_rates`.

    With `responses/` empty this dimension asserts over three empty sets. The
    pre-8.1 harness reported that as a pass and exited 0, which is what a shell,
    a checklist or a summary reads as success.
    """

    def test_an_unmeasured_dimension_contributes_no_failure(self):
        assert run_evals._dimension_failures("D5", {}, {}) == []

    def test_but_an_unmeasured_dimension_is_not_counted_as_measured(self):
        from tests.evals import rates

        agg = rates.aggregate({})
        assert agg["scenarios"] == 0
        assert agg["pass_at_k"] is None and agg["reliable_at_k"] is None, (
            "a rate over zero observations is unknown; the CLI keys 'NOT MEASURED' on this"
        )

    def test_a_negative_run_index_is_refused_rather_than_wrapped(self, tmp_path):
        """`runs[-1]` is the run that MOVES under a top-up."""
        import json

        from tests.evals import corpus

        path = tmp_path / "S-101.json"
        path.write_text(
            json.dumps({"scenario_id": "S-101", "runs": [
                {"response_text": "first", "tool_calls_log": []},
                {"response_text": "last", "tool_calls_log": []},
            ]}),
            encoding="utf-8",
        )
        with pytest.raises(corpus.CorpusShapeError):
            corpus.load_run(path, -1)
