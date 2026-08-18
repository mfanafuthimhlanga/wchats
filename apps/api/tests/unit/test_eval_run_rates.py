"""The eval harness checks every SCORABLE run, and says which failure it saw.

BACKLOG 8.1. `run_evals.py` made one pass per scenario, so a dimension that
failed reported a single FAIL and nothing about whether the agent CANNOT do the
task or only sometimes does. Those prescribe opposite work: never-passed means
the model, tools or architecture is the fix, and flaky means variance is.

Two load-bearing tests here.

`test_every_run_is_checked_not_just_the_first`: a collector that reads run 0 and
stops turns a 2-of-3 scenario into a clean PASS.

`test_a_deflection_is_not_counted_as_an_agent_failure`: the harness and
`validate_corpus.py` disagreed about the same three files. The validator called
S-002 FATAL because its `response_text` is the PII firewall's deflection; the
harness scored it anyway and reported `D5 NEVER passed [S-002]`. A deflection has
no citation block because it is not an answer, so that number was about the
corpus and was printed as product quality.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.utils.pii_firewall import PII_DEFLECTION
from tests.evals import run_evals

CITED = (
    "Unopened bags may be returned within 14 days of delivery for a full refund, and we "
    "cover return shipping when the fault is ours.\n"
    "CITATIONS:\n- Document: HANDBOOK.pdf | Section: Returns"
)
#: A real answer of ordinary length that simply carries no CITATIONS block. It
#: must clear validate_corpus's 120-char answer floor, or the harness correctly
#: drops it as unscorable and this file measures the wrong thing.
UNCITED = (
    "Unopened bags may be returned within 14 days of delivery for a full refund, and we "
    "cover return shipping when the fault is ours."
)

SCENARIO = {
    "id": "S-101",
    "category": "golden_path",
    "turns": [{"role": "user", "message": "what is your return policy"}],
    "deterministic_checks": {"D5": {}},
}


@pytest.fixture
def corpus_dir(tmp_path, monkeypatch):
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


def _collect(scenario: dict = SCENARIO) -> dict:
    return run_evals.collect_deterministic([scenario])


class TestCollection:
    def test_every_run_is_checked_not_just_the_first(self, corpus_dir):
        """Run 0 passes, run 1 does not, run 2 does.

        A collector that stops after run 0 records [True] and the scenario reads
        as a clean pass at 100%. The corpus paid for three live agent turns and
        two of them would go unread.
        """
        corpus_dir(CITED, UNCITED, CITED)
        c = _collect()

        assert c["outcomes"]["D5"]["S-101"] == [True, False, True]
        assert c["skipped"] == []
        assert "run 1" in c["reasons"]["D5"]["S-101"][0], "a reason must name its run"

    def test_a_never_captured_scenario_is_skipped_not_failed(self, corpus_dir):
        c = _collect(SCENARIO | {"id": "S-999"})
        assert c["skipped"] == ["S-999"]
        assert c["outcomes"]["D5"] == {}

    def test_a_pre_8_1_record_is_one_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_evals, "RESPONSES_DIR", tmp_path)
        (tmp_path / "S-101.json").write_text(
            json.dumps({"scenario_id": "S-101", "response_text": CITED, "tool_calls_log": []}),
            encoding="utf-8",
        )
        assert _collect()["outcomes"]["D5"]["S-101"] == [True]


class TestTheCorpusIsNotTheAgent:
    """A row the validator rejects may not enter a rate about the agent."""

    def test_a_deflection_is_not_counted_as_an_agent_failure(self, corpus_dir):
        corpus_dir(PII_DEFLECTION)
        c = _collect()

        assert c["outcomes"]["D5"] == {}, (
            "a deflection has no citation block because it is not an answer; scoring it "
            "reports the firewall as an agent failure"
        )
        assert "deflection" in c["unscorable"]["S-101"][0]
        assert "run 0" in c["unscorable"]["S-101"][0]

    def test_the_scorable_runs_of_a_mixed_scenario_are_still_scored(self, corpus_dir):
        """Run 1 is unscorable; runs 0 and 2 are real answers and still count."""
        corpus_dir(CITED, PII_DEFLECTION, UNCITED)
        c = _collect()

        assert c["outcomes"]["D5"]["S-101"] == [True, False], (
            "the deflection is dropped, not scored and not silently passed"
        )
        assert c["unscorable"]["S-101"] == ["run 1: response is the PII firewall's deflection"]

    def test_contamination_is_reported_as_contamination(self, corpus_dir):
        corpus_dir(PII_DEFLECTION)
        c = _collect()

        joined = "\n".join(run_evals._contamination_failures(c["unscorable"]))
        assert "CORPUS CONTAMINATED" in joined
        assert "NOT agent failures" in joined
        assert "Re-capture" in joined

    def test_a_clean_corpus_reports_no_contamination(self, corpus_dir):
        corpus_dir(CITED, UNCITED)
        assert _collect()["unscorable"] == {}
        assert run_evals._contamination_failures({}) == []

    def test_the_validator_owns_the_rule_and_it_is_not_copied(self):
        """One definition of unscorable, or the two go out of step.

        `unscorable_reasons` delegates to `validate_corpus`. A second copy of the
        deflection text here would go stale the first time the wording changed,
        and this harness would quietly start grading deflections again.
        """
        from tests.evals import validate_corpus as vc

        run = {"response_text": PII_DEFLECTION, "tool_calls_log": []}
        assert run_evals.unscorable_reasons("S-101", run) == vc.fatal_findings("S-101", run)

    def test_grounding_alone_is_unscorable_without_a_retrieved_chunk(self):
        """BLIND is per dimension. A judge call is money; do not refuse the row."""
        run = {
            "response_text": CITED,
            "tool_calls_log": [{"tool_name": "retrieve", "input": {"query": "q"}, "result": {}}],
        }
        assert run_evals.unscorable_reasons("S-101", run, "grounding_fidelity"), (
            "grounding_fidelity's rubric needs a chunk in the log, so its PASS branch "
            "is unreachable here and the FAIL would be decided by the capture format"
        )
        assert run_evals.unscorable_reasons("S-101", run, "escalation_accuracy") == []
        assert run_evals.unscorable_reasons("S-101", run) == []


class TestWhatAFailureSays:
    def test_a_flaky_scenario_is_named_flaky_with_its_count(self, corpus_dir):
        corpus_dir(CITED, UNCITED, CITED)
        c = _collect()

        joined = "\n".join(
            run_evals._dimension_failures("D5", c["outcomes"]["D5"], c["reasons"]["D5"])
        )
        assert "FLAKY [S-101] 2/3" in joined, "the count says how much variance there is"
        assert "NEVER" not in joined
        assert "pass@k 100%" in joined and "reliable@k 67%" in joined

    def test_a_scenario_that_never_passes_is_named_never(self, corpus_dir):
        corpus_dir(UNCITED, UNCITED, UNCITED)
        c = _collect()

        joined = "\n".join(
            run_evals._dimension_failures("D5", c["outcomes"]["D5"], c["reasons"]["D5"])
        )
        assert "NEVER passed [S-101]" in joined, (
            "prompt tuning is wasted effort here; this is a capability failure"
        )
        assert "FLAKY" not in joined

    def test_every_run_passing_is_the_only_thing_that_passes(self, corpus_dir):
        corpus_dir(CITED, CITED, CITED)
        c = _collect()
        assert run_evals._dimension_failures("D5", c["outcomes"]["D5"], c["reasons"]["D5"]) == []

    def test_one_bad_run_out_of_five_still_fails_the_gate(self, corpus_dir):
        """The gate is reliable@k == 1.0, not "mostly passes".

        A P0 dimension that holds four times in five does not hold, and at k=1
        the fifth run is the one a customer gets.
        """
        corpus_dir(CITED, CITED, CITED, CITED, UNCITED)
        c = _collect()
        assert run_evals._dimension_failures("D5", c["outcomes"]["D5"], c["reasons"]["D5"]) != []

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
