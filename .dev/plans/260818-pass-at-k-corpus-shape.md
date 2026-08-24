# 8.1 · pass-at-k-and-reliable-at-k

**Goal:** the corpus records k runs per scenario, and every rate read off it is reported as a pair:
pass@k (did the agent EVER succeed) beside reliable@k (how OFTEN). One capture, then those two
numbers, for every deterministic dimension and every judged one.

**Rule this encodes:** a success rate from one run per scenario cannot say whether a failure is
"cannot" or "sometimes", and those two have opposite fixes. pass@k near zero means prompt work is
wasted effort. High pass@k with low reliable@k means the work is variance.

## Why this is not "loop k"

The corpus is single-response by construction. `responses/{scenario_id}.json` holds one
`response_text` and one `tool_calls_log`, and five call sites across four files key on that:

| Site | What it assumes |
|---|---|
| `capture_responses.py:344` | the file's EXISTENCE means the scenario is captured, so it skips |
| `capture_responses.py` write | one record per scenario, written whole |
| `run_evals.py:78` `_load_response` | one response per scenario for D3, D5, D6 and every judge call |
| `compute_correlation.py:267` `load_response` | one response per scenario, the one the human scored |
| `compute_correlation.py:299` `deflected_response_ids` | one `response_text` per file |
| `validate_corpus.py` | one record per `S-*.json` |

The existence-skip is the sharp edge. Under k it means "captured at SOME k", so a `--runs 5` pass
over a tree holding k=1 files leaves a corpus that is 5 for some scenarios and 1 for others, and a
pooled rate over that is decided by which scenarios errored rather than by the agent.

## Record shape

```json
{
  "scenario_id": "S-001",
  "runs": [
    {"response_text": "...", "tool_calls_log": [...]},
    {"response_text": "...", "tool_calls_log": [...]}
  ]
}
```

- **Position in `runs` IS the run index.** No `run_index` field to drift out of step with position.
- **Run 0 is the human's row.** `human_scores.csv` is one row per (scenario, dimension) with no
  concept of a run, so calibration reads run 0 and only run 0. Scoring all k multiplies the only
  human step in the system by k and buys nothing: the judge is calibrated at k=1 against human
  labels first, then the calibrated judge scores the other k-1.
- **One file per scenario, not one per run.** A run count then survives a partial write as a list
  length rather than as a directory listing nobody checks.
- **The pre-8.1 shape normalises to one run**, in one place. Every legacy file on disk is going to
  be deleted before the re-capture, but until then `validate_corpus.py` must still report the FATAL
  and BLIND rows that are the reason for the re-capture, rather than crash on their shape.

## The one reader

New `apps/api/tests/evals/corpus.py`, imported by all four call sites, so exactly one function
knows the shape:

```python
RUN_ZERO = 0                                   # the run the human scores

runs_of(record)        -> list[dict]           # normalises; raises CorpusShapeError
load_runs(path)        -> list[dict]
load_run(path, index)  -> dict
build_record(sid, runs)-> dict
runs_to_capture(present, target, overwrite) -> int
```

`runs_to_capture` is separate from the capture loop so the top-up arithmetic is testable on a
machine with no live agent, which is every machine until `7.32` is refreshed.

## Per file

- **`tests/evals/corpus.py`** — new. The shape, and nothing else.
- **`tests/evals/capture_responses.py`** — `--runs K` (default 1). Existence-skip becomes a top-up:
  read the present count, capture `K - present` more, append. Each run is a FRESH conversation
  (`conversation_id` resets per run, not per scenario) and mints its own widget JWT, because the
  900s window is now crossed k times sooner. `--overwrite` starts from zero.
- **`tests/evals/validate_corpus.py`** — validates every run of every scenario; a finding names its
  run. Prints the run count per scenario, so a ragged corpus is visible where a number is read
  rather than inferred later.
- **`tests/evals/run_evals.py`** — D3, D5, D6 and every judged dimension run per run. The report
  carries `k`, `pass@k` and `reliable@k` per dimension. The P0 gate becomes reliable@k == 1.0,
  which is strictly stronger than today's k=1 gate, and the failure message distinguishes a
  scenario that NEVER passed from one that passed sometimes.
- **`tests/evals/calibration/compute_correlation.py`** — `load_response` and
  `deflected_response_ids` read run 0.

**Aggregation across scenarios is the mean of per-scenario rates, not total successes over total
runs.** Mean-of-rates weights each scenario equally whatever its k; the other form lets a scenario
that happened to get more runs dominate the number.

## Out of scope, and still open after this

`8.2` (judge temperature, kappa, intervals) and `8.3` (per category rather than pooled) are separate
rows. `8.3` slots into the same per-dimension aggregation this adds.

## Tests

`tests/unit/test_corpus_runs.py` and `tests/unit/test_pass_at_k.py`, each written to fail first.

1. A k=3 record round-trips through `build_record` and `load_runs` with order preserved.
2. A pre-8.1 record loads as exactly one run.
3. `load_run(path, RUN_ZERO)` is the first run of a k=3 record and the only run of a legacy one.
4. `runs_of` raises `CorpusShapeError` on a record with neither `runs` nor `response_text`, on
   `runs: []`, and on `runs` that is not a list.
5. `runs_to_capture`: (0, 5) is 5, (3, 5) is 2, (5, 5) is 0, (7, 5) is 0, and overwrite is target.
6. All five runs pass: pass@k 1.0, reliable@k 1.0.
7. One of five passes: pass@k True, reliable@k 0.2. The capability-versus-consistency case.
8. Zero of five: pass@k False. "cannot", not "sometimes".
9. k=1: pass@k equals reliable@k, and the report carries k so no reader can read consistency into it.
10. Ragged k across scenarios weights scenarios equally.
11. A corpus whose run 0 is clean and run 2 is a deflection is FATAL, naming run 2.
12. `compute_correlation` scores run 0's text when runs 1 and 2 differ from it.

**Mutation proofs**, per the repo's negative-test rule. Each mutated, observed red, restored from
`HEAD` unconditionally, observed green, with the observed output recorded in the trace:

| Mutation | Test that must go red |
|---|---|
| `runs_to_capture` returns `target` always | 5 — the "silently keeps only the last run" class |
| `validate_corpus` reads run 0 only | 11 — the same class, one file over |
| aggregate switches to total successes over total runs | 10 |
| calibration reads `runs[-1]` | 12 |

## Exit

- `apps/api` `scripts/gates.py full` green, run detached.
- `validate_corpus.py` against the current tree still reports the FATAL and BLIND rows that make the
  re-capture necessary, rather than a shape error over them.
