"""The eval corpus record shape, in one place, because k > 1 splits it five ways.

BACKLOG 8.1. Until now a scenario's recorded response was one file holding one
`response_text` and one `tool_calls_log`, and five call sites across four files
each parsed that themselves. A single run per scenario cannot separate the two
failures a rate can describe:

    pass@k       over k tries, did the agent EVER succeed?     capability
    reliable@k   over k tries, how OFTEN did it succeed?       consistency

pass@k near zero means the system cannot do the task and prompt work is wasted
effort. High pass@k with low reliable@k means it can, and the work is variance.
A deterministic system has the two equal; the gap between them IS the AI problem,
and one run per scenario is silent about which one is being looked at.

THE SHAPE

    {
      "scenario_id": "S-001",
      "runs": [
        {"response_text": "...", "tool_calls_log": [...]},
        {"response_text": "...", "tool_calls_log": [...]}
      ]
    }

Position in `runs` IS the run index. A `run_index` field beside it would be a
second copy of the same fact, free to drift out of step with the position the
readers actually slice on.

RUN 0 IS THE HUMAN'S ROW

The owner's score sheet is one row per (scenario, dimension) with no concept of
a run, so scoring all k multiplies the only human step in the system by k. The
sequence that avoids it: capture once at k > 1, the human scores run 0, the
judge is calibrated against those labels at k=1, and the calibrated judge then
scores the other k-1 runs. Calibration therefore reads run 0 and only run 0.

(The sheet is deliberately not named here. `test_calibration_harness.py` pins
that only the calibration harness may reference it by name, so that a future
"seed the calibration set" helper trips a test rather than filling the one
column an agent must never write.)

THE PRE-8.1 SHAPE

A record with `response_text` at the top level and no `runs` is one run, and it
is normalised HERE rather than at each call site. Every such file on disk is due
to be deleted before the next capture, but until then `validate_corpus.py` has
to keep reporting the FATAL and BLIND rows that are the reason for that
re-capture, rather than fail on their shape and report nothing at all.
"""

from __future__ import annotations

import json
import pathlib

#: The run the human scores, and the run the judge is calibrated against.
RUN_ZERO = 0


class CorpusShapeError(ValueError):
    """A recorded response is neither the k-run shape nor the pre-8.1 one."""


def runs_of(record: dict) -> list[dict]:
    """Every run in one scenario's record, in capture order.

    Raises rather than returning an empty list, because a record holding no run
    is not a captured scenario and a caller averaging over zero runs would get a
    ZeroDivisionError somewhere further away from the cause.
    """
    if not isinstance(record, dict):
        raise CorpusShapeError(f"record is {type(record).__name__}, not an object")

    if "runs" in record:
        runs = record["runs"]
        if not isinstance(runs, list):
            raise CorpusShapeError(f"`runs` is {type(runs).__name__}, not a list")
        if not runs:
            raise CorpusShapeError("`runs` is empty; a record with no run is not a capture")
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                raise CorpusShapeError(f"run {index} is {type(run).__name__}, not an object")
        return runs

    if "response_text" in record:
        # Pre-8.1: one record, one run. Normalised here and nowhere else.
        return [{
            "response_text": record.get("response_text", ""),
            "tool_calls_log": record.get("tool_calls_log", []),
        }]

    raise CorpusShapeError("record has neither `runs` nor `response_text`")


def load_runs(path: pathlib.Path) -> list[dict]:
    """Every run recorded for the scenario at `path`."""
    return runs_of(json.loads(path.read_text(encoding="utf-8")))


def load_run(path: pathlib.Path, index: int = RUN_ZERO) -> dict:
    """One run of the scenario at `path`. Defaults to the run the human scores."""
    runs = load_runs(path)
    # `index < 0` is rejected rather than wrapped. Python would silently return
    # runs[-1] for -1, and the last run is the one that MOVES under a top-up:
    # a scenario re-captured from 3 to 5 changes it, so a caller that meant run
    # 0 and got the last one would correlate a judge against text the human
    # never labelled, and nothing would raise.
    if index < 0 or index >= len(runs):
        raise CorpusShapeError(
            f"{path.name} holds {len(runs)} run(s); run {index} was asked for"
        )
    return runs[index]


def build_record(scenario_id: str, runs: list[dict]) -> dict:
    """The record as it is written to disk."""
    return {"scenario_id": scenario_id, "runs": list(runs)}


def runs_to_capture(present: int, target: int, overwrite: bool = False) -> int:
    """How many more live agent turns this scenario needs to reach `target`.

    The whole of BACKLOG 8.1's silent-failure mode lives in this arithmetic. The
    shipped capture skipped a scenario whose FILE EXISTED, which under k means
    "captured at some k, possibly 1", so a --runs 5 pass over a tree of k=1 files
    left a corpus that was 5 for some scenarios and 1 for others. A rate pooled
    over that is decided by which scenarios errored on the previous run rather
    than by the agent.

    Topping up rather than skipping also keeps the partial re-capture workflow:
    delete a scenario's file and it has zero runs, so it captures the full k.

    `present` above `target` returns 0 rather than a negative: extra runs already
    paid for are kept, and nothing is deleted to satisfy a smaller k.
    """
    if overwrite:
        return max(target, 0)
    return max(target - present, 0)
