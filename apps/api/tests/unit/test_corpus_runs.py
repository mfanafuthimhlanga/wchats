"""The corpus holds k runs per scenario, and nothing silently keeps only one.

BACKLOG 8.1. The shipped capture skipped a scenario whose response FILE EXISTED.
Under k > 1 that means "captured at some k, possibly 1", so a --runs 5 pass over
a tree of single-run files leaves a corpus that is 5 for some scenarios and 1 for
others, and a rate pooled over it is decided by which scenarios errored on the
previous run rather than by the agent.

The load-bearing tests here are `TestTopUpArithmetic`, which pins the arithmetic
deciding whether a scenario is re-captured, and `TestMissingDataIsRefusedNotCounted`,
which pins that absent data is refused rather than counted. They are the same
defect class the repo has already been bitten by twice: a check that reports a
number while the thing it measures is not the thing anyone thinks it is.

(An earlier version of this docstring claimed "the two that pin WHICH run each
consumer reads". No test in this file pins a consumer; they pin `corpus.load_run`.
The consumers are pinned in `test_calibration_harness.py` and
`test_eval_run_rates.py`. An adversarial review caught the miscredit, which is a
small instance of the same class: a file describing coverage it does not have.)
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


# ---------------------------------------------------------------------------
# Adversarial review 2026-08-18. Every test below killed a mutation or a defect
# the original module let through green.
# ---------------------------------------------------------------------------


class TestMissingDataIsRefusedNotCounted:
    """The BLOCK finding: a record of empty run dicts read as captured runs.

    `runs_to_capture` then saw three runs and never topped the scenario up, every
    scorer read the absent `response_text` as an empty answer, and the harness
    reported `never_passed` - "change the model, tools or architecture" - about a
    scenario that had never been captured at all. Three empty dicts are the same
    absence as an empty `runs` list, with a denominator attached.
    """

    def test_a_run_with_no_response_text_is_not_a_run(self):
        with pytest.raises(corpus.CorpusShapeError, match="no `response_text`"):
            corpus.runs_of({"scenario_id": "S-101", "runs": [{}, {}, {}]})

    def test_a_run_missing_only_its_tool_calls_log_is_fine(self):
        runs = corpus.runs_of({"runs": [{"response_text": "an answer"}]})
        assert runs == [{"response_text": "an answer", "tool_calls_log": []}]

    def test_an_empty_response_text_is_captured_data_and_is_kept(self):
        """Absent and empty are different, and only absent is missing data.

        A captured turn that returned nothing is a real observation, and
        `validate_corpus.py` reports it as FATAL. Refusing it here would hide a
        real defect behind a shape error.
        """
        assert corpus.runs_of({"runs": [{"response_text": ""}]})[0]["response_text"] == ""

    def test_a_record_carrying_both_shapes_is_refused(self):
        """The shape a legacy file topped up in place would have.

        Either resolution discards a real answer, and the top-level one is the
        row the human scored.
        """
        with pytest.raises(corpus.CorpusShapeError, match="BOTH"):
            corpus.runs_of({"response_text": "human scored this",
                            "runs": [{"response_text": "a later run"}]})


class TestTheWritePathCannotWriteWhatTheReadPathRejects:
    """`build_record` had no guard corresponding to any of `runs_of`'s four."""

    def test_a_single_run_passed_as_a_dict_is_refused(self):
        """`list({"a": 1})` is `["a"]`, so the corpus recorded a KEY as an answer.

        It failed at read time, in another process, after the live agent turns
        had been paid for.
        """
        with pytest.raises(corpus.CorpusShapeError, match="must be a list"):
            corpus.build_record("S-101", {"response_text": "an answer"})

    def test_an_empty_run_list_is_refused_at_write_time(self):
        with pytest.raises(corpus.CorpusShapeError, match="empty"):
            corpus.build_record("S-101", [])

    def test_what_it_writes_is_what_the_read_path_accepts(self):
        """The two paths share `runs_of` rather than duplicating its checks."""
        record = corpus.build_record("S-101", [{"response_text": "a"}, {"response_text": "b"}])
        assert record["scenario_id"] == "S-101"
        assert [r["response_text"] for r in corpus.runs_of(record)] == ["a", "b"]

    def test_it_keeps_the_scenario_id(self):
        """A mutation dropping `scenario_id` survived every earlier test."""
        assert corpus.build_record("S-101", [{"response_text": "a"}])["scenario_id"] == "S-101"

    def test_it_copies_rather_than_aliasing_the_callers_runs(self):
        runs = [{"response_text": "original"}]
        record = corpus.build_record("S-101", runs)
        record["runs"][0]["response_text"] = "MUTATED"
        assert runs[0]["response_text"] == "original"


class TestReadsDoNotAliasTheRecord:
    def test_mutating_a_returned_run_does_not_touch_the_record(self):
        record = {"runs": [{"response_text": "original"}]}
        corpus.runs_of(record)[0]["response_text"] = "MUTATED"
        assert record["runs"][0]["response_text"] == "original"

    def test_the_legacy_branch_normalises_the_tool_calls_log(self):
        """Two mutations survived here, and both would score every legacy file 0.

        Returning the record raw, or dropping `tool_calls_log`, leaves any
        tool-use dimension reading an absent log as "no tools were called".
        """
        runs = corpus.runs_of({"response_text": "a",
                               "tool_calls_log": [{"tool_name": "retrieve"}]})
        assert runs == [{"response_text": "a", "tool_calls_log": [{"tool_name": "retrieve"}]}]

    def test_a_legacy_record_with_no_tool_calls_log_gets_an_empty_one(self):
        assert corpus.runs_of({"response_text": "a"})[0]["tool_calls_log"] == []


class TestArgumentsAreValidated:
    """`runs_to_capture` accepted anything numeric, and some things that were not."""

    @pytest.mark.parametrize("present", [-1, -3])
    def test_a_negative_present_is_refused_rather_than_inflating_the_target(self, present):
        """`present=-3, target=5` used to return 8: MORE runs than were asked for."""
        with pytest.raises(corpus.CorpusShapeError, match="not be negative"):
            corpus.runs_to_capture(present, 5)

    @pytest.mark.parametrize("value", [1.5, True, "1"])
    def test_a_non_integer_is_refused_by_name(self, value):
        with pytest.raises(corpus.CorpusShapeError, match="must be an int"):
            corpus.runs_to_capture(value, 5)

    def test_a_negative_target_is_refused_too(self):
        with pytest.raises(corpus.CorpusShapeError, match="not be negative"):
            corpus.runs_to_capture(0, -1)

    def test_overwrite_returns_the_target_and_the_docstring_says_so(self):
        """The "nothing is deleted" guarantee is scoped to the top-up branch.

        The earlier docstring stated it unconditionally, and under overwrite it
        is false by construction: the caller asked for exactly `target` fresh
        runs.
        """
        assert corpus.runs_to_capture(9, 2, overwrite=True) == 2
        assert "scoped to `overwrite=False`" in corpus.runs_to_capture.__doc__


class TestErrorsNameTheFile:
    def test_a_shape_error_from_a_file_carries_its_name(self, tmp_path):
        """Over 250 scenarios, "run 2 has no response_text" names none of them."""
        path = tmp_path / "S-101.json"
        path.write_text(json.dumps({"runs": [{}]}), encoding="utf-8")
        with pytest.raises(corpus.CorpusShapeError, match="S-101.json"):
            corpus.load_runs(path)

    def test_invalid_json_becomes_a_corpus_shape_error_not_a_json_error(self, tmp_path):
        """A caller catching CorpusShapeError should not also need json's."""
        path = tmp_path / "S-102.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(corpus.CorpusShapeError, match="S-102.json"):
            corpus.load_runs(path)
