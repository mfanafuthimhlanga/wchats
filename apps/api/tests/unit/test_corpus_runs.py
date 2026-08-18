"""The corpus holds k runs per scenario, and nothing silently keeps only one.

BACKLOG 8.1. The shipped capture skipped a scenario whose response FILE EXISTED.
Under k > 1 that means "captured at some k, possibly 1", so a --runs 5 pass over
a tree of single-run files leaves a corpus that is 5 for some scenarios and 1 for
others, and a rate pooled over it is decided by which scenarios errored on the
previous run rather than by the agent.

The load-bearing tests here are the two that pin `runs_to_capture` and the two
that pin WHICH run each consumer reads. They are the same defect class the repo
has already been bitten by twice: a check that reports a number while the thing
it measures is not the thing anyone thinks it is.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.evals import corpus


def _write(tmp_path: pathlib.Path, name: str, record: dict) -> pathlib.Path:
    path = tmp_path / name
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _run(text: str) -> dict:
    return {"response_text": text, "tool_calls_log": [{"tool_name": "retrieve"}]}


class TestTheShape:
    def test_a_three_run_record_round_trips_in_order(self, tmp_path):
        record = corpus.build_record("S-101", [_run("first"), _run("second"), _run("third")])
        runs = corpus.load_runs(_write(tmp_path, "S-101.json", record))
        assert [r["response_text"] for r in runs] == ["first", "second", "third"], (
            "position in `runs` IS the run index; a reordering silently renames every run"
        )

    def test_a_pre_8_1_record_loads_as_exactly_one_run(self, tmp_path):
        """The files on disk today, which validate_corpus still has to report on."""
        legacy = {"scenario_id": "S-101", "response_text": "only", "tool_calls_log": []}
        runs = corpus.load_runs(_write(tmp_path, "S-101.json", legacy))
        assert len(runs) == 1
        assert runs[0]["response_text"] == "only"

    def test_run_zero_is_the_first_run_not_the_last(self, tmp_path):
        record = corpus.build_record("S-101", [_run("human scores this"), _run("later"), _run("last")])
        path = _write(tmp_path, "S-101.json", record)
        assert corpus.load_run(path, corpus.RUN_ZERO)["response_text"] == "human scores this"
        assert corpus.load_run(path)["response_text"] == "human scores this", "the default is run 0"

    def test_run_zero_of_a_legacy_record_is_its_only_run(self, tmp_path):
        legacy = {"scenario_id": "S-101", "response_text": "only", "tool_calls_log": []}
        path = _write(tmp_path, "S-101.json", legacy)
        assert corpus.load_run(path, corpus.RUN_ZERO)["response_text"] == "only"

    def test_asking_for_a_run_the_record_does_not_have_raises(self, tmp_path):
        path = _write(tmp_path, "S-101.json", corpus.build_record("S-101", [_run("a")]))
        with pytest.raises(corpus.CorpusShapeError, match="holds 1 run"):
            corpus.load_run(path, 3)


class TestRecordsThatAreNotRecords:
    """Each raises rather than returning something a caller can average over."""

    def test_a_record_with_neither_key_raises(self):
        with pytest.raises(corpus.CorpusShapeError, match="neither"):
            corpus.runs_of({"scenario_id": "S-101"})

    def test_an_empty_runs_list_is_not_a_capture(self):
        with pytest.raises(corpus.CorpusShapeError, match="empty"):
            corpus.runs_of({"scenario_id": "S-101", "runs": []})

    def test_runs_that_is_not_a_list_raises(self):
        with pytest.raises(corpus.CorpusShapeError, match="not a list"):
            corpus.runs_of({"scenario_id": "S-101", "runs": {"response_text": "x"}})

    def test_a_run_that_is_not_an_object_raises(self):
        with pytest.raises(corpus.CorpusShapeError, match="run 1"):
            corpus.runs_of({"scenario_id": "S-101", "runs": [{"response_text": "x"}, "oops"]})


class TestTopUpArithmetic:
    """A capture is live agent turns against a live tenant, so this is spend."""

    @pytest.mark.parametrize(
        ("present", "target", "expected"),
        [(0, 5, 5), (3, 5, 2), (5, 5, 0), (7, 5, 0), (0, 1, 1), (1, 1, 0)],
    )
    def test_it_tops_up_to_the_target(self, present, target, expected):
        assert corpus.runs_to_capture(present, target) == expected

    def test_a_scenario_already_at_the_target_costs_nothing(self):
        """The property the existence-skip was reaching for, stated in runs."""
        assert corpus.runs_to_capture(present=5, target=5) == 0

    def test_a_scenario_short_of_the_target_is_topped_up_not_skipped(self):
        """The 8.1 defect itself.

        The shipped capture skipped on the file EXISTING, which under k means
        "captured at some k". Skipping a k=1 file during a --runs 5 pass is how a
        corpus ends up ragged, and a pooled rate over a ragged corpus is decided
        by the previous run's errors.
        """
        assert corpus.runs_to_capture(present=1, target=5) == 4

    def test_overwrite_recaptures_the_whole_target(self):
        assert corpus.runs_to_capture(present=5, target=5, overwrite=True) == 5
        assert corpus.runs_to_capture(present=0, target=3, overwrite=True) == 3

    def test_extra_runs_already_paid_for_are_never_deleted(self):
        assert corpus.runs_to_capture(present=9, target=2) == 0
