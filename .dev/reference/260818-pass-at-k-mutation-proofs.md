# Mutation proofs, BACKLOG 8.1

**Observed 2026-08-18** against `chore/m0-gate-followups`, first at `d22379c` and re-run unchanged at `6819591` after the adversarial pass. Each guard was mutated, run,
observed RED, restored from `HEAD` unconditionally, and observed GREEN. What follows is the output,
not a description of it.

**Why these five and not others.** Every one of them mutates a *silence*: the mutated code returns a
number rather than raising, and the number is plausible. That is the failure this row exists to stop
(`260818-green-for-the-wrong-reason.md` is the general case), so a test that does not go red under
these mutations is a test that would have watched the defect land.

## M1 - `runs_to_capture` ignores what is already recorded

`return max(target - present, 0)` becomes `return max(target, 0)`. This is the existing
skip-on-file-exists defect restated in the new code: a scenario is re-captured from zero every time
rather than topped up, so a corpus is never comparable across a resumed capture.

```
FAILED tests/unit/test_corpus_runs.py::TestTopUpArithmetic::test_a_scenario_short_of_the_target_is_topped_up_not_skipped
FAILED tests/unit/test_corpus_runs.py::TestTopUpArithmetic::test_extra_runs_already_paid_for_are_never_deleted
7 failed, 3 passed in 1.25s
---- restore from HEAD ----
10 passed in 0.58s
```

## M2 - the corpus validator reads run 0 only

`enumerate(runs)` becomes `enumerate(runs[:1])`. A record whose run 0 answers and whose run 2
deflects then validates clean, which is the most informative row the corpus can carry going unread.

```
FAILED tests/unit/test_corpus_validator.py::test_a_defect_in_a_later_run_is_found
1 failed in 1.30s
---- restore from HEAD ----
1 passed in 0.80s
```

## M3 - reliable@k pools over runs instead of scenarios

`sum(per-scenario rates) / n_scenarios` becomes `sum(passes) / sum(ks)`. On the ragged fixture
(S-001 fails eight times, S-002 passes once) the mutant reports 11 percent where the guard requires
50 percent, so the scenario that happened to get more runs decides the number.

```
FAILED tests/unit/test_pass_at_k.py::TestAcrossScenarios::test_a_ragged_corpus_weights_scenarios_not_runs
1 failed in 1.05s
---- restore from HEAD ----
1 passed in 0.56s
```

## M4 - calibration reads the last run

`corpus.load_run(path, corpus.RUN_ZERO)` becomes `corpus.load_run(path, -1)`. The judge is then
correlated against a human score for text the human never saw, and the mismatch appears only as a
lower rho, which reads as a judge problem.

**The mechanism changed between the two runs and the verdict did not.** At `d22379c` the mutant
returned `runs[-1]` and the test failed on the transcript assertion. `6819591` made `load_run`
refuse a negative index, so the mutant now raises `CorpusShapeError` and the test fails on that.
The second is the better failure: it stops at the call rather than at the consequence.

```
FAILED tests/unit/test_calibration_harness.py::TestCalibrationReadsRunZero::test_the_judge_is_shown_run_zero_not_the_last_run
1 failed in 1.20s
---- restore from HEAD ----
1 passed in 0.62s
```

## M5 - the eval harness checks run 0 only

`enumerate(runs)` becomes `enumerate(runs[:1])` in `collect_deterministic`. A 2-of-3 scenario then
reports as a clean 100 percent pass, and k runs were paid for to read one.

```
FAILED tests/unit/test_eval_run_rates.py::TestCollection::test_every_run_is_checked_not_just_the_first
1 failed in 1.28s
---- restore from HEAD ----
1 passed in 0.78s
```

## Tree after

```
 M .dev/BACKLOG.md
?? .dev/plans/260818-judge-temperature-zero.md
?? .dev/plans/260818-pass-at-k-corpus-shape.md
```

No source file left mutated.


## What these proofs do NOT cover

Named because a proof list reads as a completeness claim otherwise.

- **The CLI's NOT MEASURED branch is verified by one manual run, not by a test.** Pointing
  `run_evals.RESPONSES_DIR` at a directory that does not exist printed `**Result: NOT MEASURED ...**`
  and exited 1, where the same tree previously printed `All checked dimensions PASSED` and exited 0.
  The unit test beside it asserts the input that branch keys on (`aggregate({})` returning `None`
  rates), not the branch.
- **`capture_all`'s top-up loop is not exercised end to end.** `runs_to_capture` is proven and
  `capture_one_run` is proven; the loop that joins them needs a live agent, so what happens when a
  run fails halfway through a 5-run top-up is reasoned about and not observed.
- **No mutation was run against the judged half of `run_evals.py`.** It needs an API key.


## Reproduce these

The house convention is that a mutation-proof note carries its own commands, so the proofs are
re-runnable from the tracked artifact rather than from a script in a temp directory. From `apps/api`,
with `PY=.venv/Scripts/python.exe`. Each block: mutate, expect RED, restore, expect GREEN.

```bash
# M1
sed -i 's|^    return max(target - present, 0)$|    return max(target, 0)|' tests/evals/corpus.py
$PY -m pytest tests/unit/test_corpus_runs.py::TestTopUpArithmetic -q
git checkout HEAD -- apps/api/tests/evals/corpus.py

# M2
sed -i 's|^        for index, run in enumerate(runs):$|        for index, run in enumerate(runs[:1]):|' tests/evals/validate_corpus.py
$PY -m pytest tests/unit/test_corpus_validator.py::test_a_defect_in_a_later_run_is_found -q
git checkout HEAD -- apps/api/tests/evals/validate_corpus.py

# M3  replace aggregate()'s reliable_at_k line with:
#     "reliable_at_k": sum(r["passes"] for r in rated.values()) / sum(ks),
$PY -m pytest "tests/unit/test_pass_at_k.py::TestAcrossScenarios::test_a_ragged_corpus_weights_scenarios_not_runs" -q
git checkout HEAD -- apps/api/tests/evals/rates.py

# M4
sed -i 's|^    return corpus.load_run(path, corpus.RUN_ZERO)$|    return corpus.load_run(path, -1)|' tests/evals/calibration/compute_correlation.py
$PY -m pytest "tests/unit/test_calibration_harness.py::TestCalibrationReadsRunZero::test_the_judge_is_shown_run_zero_not_the_last_run" -q
git checkout HEAD -- apps/api/tests/evals/calibration/compute_correlation.py

# M5
sed -i 's|^            for index, run in enumerate(runs):$|            for index, run in enumerate(runs[:1]):|' tests/evals/run_evals.py
$PY -m pytest "tests/unit/test_eval_run_rates.py::TestCollection::test_every_run_is_checked_not_just_the_first" -q
git checkout HEAD -- apps/api/tests/evals/run_evals.py
```

Finish with `git status --porcelain` and confirm no source file is left mutated.
