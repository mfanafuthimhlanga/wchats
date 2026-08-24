# 8.1 · pass-at-k-and-reliable-at-k

**Landed 2026-08-18 on `chore/m0-gate-followups`.** The eval corpus records k runs per scenario and
every rate the harness prints now carries its own k, beside the two numbers that separate a
capability failure from a variance one.

**What is NOT closed:** the twenty response files on disk are still the contaminated k=1 E2E-6 set.
The harness reports that in its own output rather than implying otherwise. The re-capture at k>1 is
blocked on `7.32` (control DB credential) and the plaintext tenant API key.

## What changed, and why each piece is where it is

### `tests/evals/corpus.py`, new: one reader for the record shape

```
{"scenario_id": "S-001", "runs": [{"response_text": ..., "tool_calls_log": [...]}, ...]}
```

Position in `runs` is the run index; there is no `run_index` field to drift out of step with the
position every consumer slices on. Run 0 is the run the human scores and the judge is calibrated
against.

Five call sites across four files each parsed the record themselves. Five parsers is five chances to
disagree, and disagreement here does not crash, it produces a plausible number. One module now owns
the shape and everything imports it.

The pre-8.1 single-run shape normalises to a one-element `runs` list, in that module and nowhere
else. Every such file is due for deletion before the next capture, but until then `validate_corpus.py`
has to keep reporting the FATAL and BLIND rows that are the reason for the re-capture, rather than
fail on their shape and report nothing.

### `tests/evals/rates.py`, new: the two metrics and the diagnosis between them

`reliable_at_k` aggregates as the **mean of per-scenario rates**, never total successes over total
runs. The two agree only while every scenario has the same k and diverge exactly when it matters: a
capture that errored partway leaves some scenarios at k=5 and some at k=1, and total-over-total then
lets the scenarios that happened to get more runs decide the number.

`never_passed` and `flaky` are separate lists because they prescribe opposite work. A k=1 corpus
reports both as one FAIL.

### `capture_responses.py`: `--runs K` tops up, it does not skip

The shipped capture skipped a scenario whose file EXISTED. Under k that means "captured at some k,
possibly 1", so a `--runs 5` pass over a k=1 tree would leave a ragged corpus. `runs_to_capture`
replaces it and is a separate pure function, so the arithmetic is testable on a machine with no live
agent, which is every machine while `7.32` is open.

Two consequences that were not in the plan and are worth naming:

- **Each run is a fresh conversation and mints its own widget JWT.** A run that continued the
  previous run's conversation is turn k+1 of one session, not an independent attempt, and reliable@k
  over those measures the session. The JWT expiry (900s) is now crossed k times sooner, which is why
  minting moved from per scenario to per run.
- **A run that errors mid-scenario writes what was already captured.** Those turns are paid for. The
  next invocation tops the scenario up from where this one stopped instead of re-running them.

### `validate_corpus.py`: every run, and the run named in the finding

A finding reads `run 2: response is the PII firewall's deflection`. The prefix appears only above
k=1, so a single-run corpus reports exactly what it reported before runs existed.

The report gained one line: the run count per scenario, and the word RAGGED with the short scenarios
named when they are not equal. Ragged k is not FATAL (the rows are scorable) and not BLIND (no judge
dimension is blind); it is a fact about the corpus that has to be visible where a number is read off
it, so it is printed rather than given a fifth exit code.

### `run_evals.py`: the gate becomes reliable@k == 1.0

At k=1 "no FAIL" was the only expressible rule. The P0 gate is now that the dimension holds on every
run of every scenario, which is strictly stronger, and the failure message says which of the two
failures it saw:

```
D5: pass@k 50%, reliable@k 50% over 6 scenario(s), k=1
  D5 NEVER passed [S-002] over 1 run(s): run 0: No citation block matching CITATIONS regex
```

The CLI summary table carries `k`, `pass@k` and `reliable@k` per dimension, and prints a standing
warning while any dimension is at k=1.

### `compute_correlation.py`: run 0, and only run 0

The owner's sheet is one row per (scenario, dimension) with no concept of a run, so scoring every run
multiplies the only human step in the system by k. Run 0 rather than the last run, because the last
run MOVES under a top-up: a scenario re-captured from 3 to 5 would change the row the human already
scored, and the correlation would then be against text nobody labelled.

`deflected_response_ids` reads run 0 for the same reason. A deflection in a later run is a real
finding and `validate_corpus.py` reports it; here the only question is whether the labelled row can
be labelled.

## Deviations from the plan

- **`8.3` was not absorbed.** Reporting per scenario category is a separate row and the aggregation
  it needs is the one this added, so it slots in without rework. Nothing is read off the corpus
  before the re-capture, so it is not urgent.
- **A run that errors mid-scenario now writes what it captured**, which the plan did not say. Those
  turns are paid for. It makes a ragged corpus reachable, which is why the validator reports the run
  count per scenario in the same change.
- **The JWT moved from per scenario to per run**, which the plan also did not say. It follows from a
  run being an independent attempt, and from the 900s window being crossed k times sooner.
- **A docstring naming the owner's score sheet tripped an existing guard.**
  `test_no_module_in_the_repo_opens_the_calibration_set_for_writing` asserts that only the
  calibration harness and its own test reference that filename anywhere under `apps/api`, and it
  fired on a comment in `corpus.py`. The guard was not weakened; the docstring was reworded to
  describe the sheet without naming it, and to say why.

## The adversarial pass, and what it found

Run against the six landed commits. Two findings, both fixed in `6819591` rather than filed.

1. **An empty `responses/` directory reported success.** The deterministic harness asserted over
   three empty sets: the CLI printed "All checked dimensions PASSED" and exited 0, and the pytest
   test passed. True before this branch too. `8.1` only made it visible, because the new summary
   table prints `0 | - | - | -` for every dimension directly above that sentence. The CLI now prints
   NOT MEASURED and exits 1, the pytest test skips rather than passes, and the success line names
   the dimensions actually measured. Verified by pointing `RESPONSES_DIR` at a directory that does
   not exist.
2. **`load_run` let a negative index wrap.** It refused `index >= len(runs)` and passed `-1` through
   to Python. `-1` is the run that moves under a top-up, so a caller that meant run 0 and got the
   last run would correlate a judge against text the human never labelled, and nothing would raise.

A third finding was closed with a test rather than a fix (`6b16212`): `capture_one_run`'s docstring
claimed a fresh conversation and a per-run JWT, and nothing checked either. The difference is
invisible in the recorded corpus.

**This pass was run by the implementer, not by an independent agent**, because subagents are off in
this session. The repo's own rule is that a self-review does not count as one, so treat it as a
first pass and not as the adversarial gate.

## Proofs

Five mutations, each observed red and restored from `HEAD`, re-run unchanged after the adversarial
fixes: `.dev/reference/260818-pass-at-k-mutation-proofs.md`. Its last section names what they do not
cover.

## What the harness says about the tree today

```
Corpus validation: 20 recorded response(s)
  runs per scenario: k=1 across all 20

  FATAL  S-001  1 of 1 tool call(s) have no tool_name
  FATAL  S-002  response is the PII firewall's deflection
  ...
```

```
| Dimension              | Scenarios | k | pass@k | reliable@k |
| D3 (injection regex)   | 4         | 1 | 1.00   | 1.00       |
| D5 (citation regex)    | 6         | 1 | 0.50   | 0.50       |
| D6 (tool correctness)  | 3         | 1 | 1.00   | 1.00       |

**k=1: D3, D5, D6 cannot separate a capability failure from a variance one.
Re-capture with `capture_responses.py --runs 5`.**
```

`tests/evals/run_evals.py::test_deterministic_dimensions_d5_d6_d7` fails on this tree, and failed at
`HEAD` for the same reason: S-002, S-003 and S-005 are the PII deflections and a deflection carries
no citation block. Verified by running `git show HEAD:...run_evals.py` as a separate file before the
change. It is not in any gate (`gates.py` runs `tests/unit`), and it clears with the re-capture.

## One measurement about the battery, so the next session does not misread it

**`gates.py full` spends most of its early wall clock in `tests/unit/test_agent_task.py`, at roughly
11 seconds a test, and then accelerates.** Observed 2026-08-18: 126 of 2470 tests at 14:31:43 and
1400 at 14:34:34. A `full` run that looks stalled at 2 percent for twenty minutes is not stalled,
and this session wasted time twice concluding otherwise, once blaming concurrent pytest processes
and once blaming a source scan walking `.venv`. Both were wrong. The 468.8s whole-suite figure
stands.
