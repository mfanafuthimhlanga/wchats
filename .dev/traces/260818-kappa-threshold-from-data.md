# 8.2c · the calibration gate stops containing a number anyone chose

**Landed 2026-08-18** on `chore/m0-gate-followups`. Plan: `.dev/plans/260818-kappa-threshold-from-data.md`.

`KAPPA_THRESHOLD = 0.6` is deleted. What decides now is two intervals bootstrapped from the
labels themselves, and both are required:

```
(a) beats chance      judge_ci_low  > 0
(b) reaches ceiling   judge_ci_high >= human_ci_low
```

`agreement.py` already implemented both and passed 13 tests; nothing called it. This wires it in,
and the wiring turned out to carry three decisions the plan had not made.

## What changed

| File | Change |
|---|---|
| `tests/evals/calibration/agreement.py` | owns `confusion` and `cohens_kappa` now |
| `tests/evals/calibration/compute_correlation.py` | imports the gate, drops the constant, gains `read_second_pass`, `ceiling_pairs_for`, `emit_second_pass`, `--emit-second-pass` |
| `tests/unit/test_calibration_harness.py` | +17 tests, 2 classes; the fixture writes a second-pass sheet |
| `tests/unit/test_agreement_threshold.py` | one test hardened, see "what the mutations found" |
| `tests/unit/test_agreement_statistics.py` | the two moved functions imported from where they live |

## Three decisions the plan did not make

### The second pass is a separate FILE, not a `human_verdict_2` column

The plan said `human_scores.csv` gains a `human_verdict_2` column. It does not.

A column sits on the same row as the first verdict, so the labeller reads their own answer while
writing the new one, and what comes out measures their memory rather than their consistency. The
ceiling would inflate towards 1.0, half (b) would refuse judges for a reason that has nothing to do
with the judges, and nothing downstream could tell.

`human_scores_pass2.csv` carries `scenario_id`, `dimension`, `human_verdict` and nothing else. No
notes: pass one's notes are the owner's own reasoning about the row, and reading them back is
reading back the answer.

It is read by the SAME function as the first sheet, so the BOM handling, the header check and the
pass/fail validation that two adversarial reviews put into `read_human_score_rows` all apply to it
without a second implementation.

### `--emit-second-pass` writes the sheet, and refuses three times

Building it by hand is a chore, and a hand-built one would not be shuffled. The emitter writes the
question and never an answer, and refuses when:

- the file already exists (it holds labels only the owner can produce)
- the first pass is unfinished (labelling both in one sitting is one pass copied, not a test-retest)
- there are no rows

Exit code 5, `EXIT_SECOND_PASS_EMITTED`. It shares a code with no outcome that measured anything,
which is the same rule audit D7 set when three outcomes were all exiting 0.

### The ceiling is measured over the rows the JUDGE was measured on

Not over every row with a second verdict. Two reasons, and the second is load-bearing:

- The ceiling caps the judge, so it has to describe the rows the judge was scored on.
- The two intervals are then computed at the same n. A ceiling over thirty rows against a judge over
  ten is the tighter interval by construction, and the judge would fail (b) for being outnumbered.

A judged row missing its second verdict is named and the ceiling is withheld, rather than computed
over whichever rows the labeller got to. That subset is not a random sample of the sheet.

## The status rule, which is where the four exit codes earn their keep

```
gate calibrated                              -> CALIBRATED       (0)
a half was MEASURED and failed               -> NOT CALIBRATED   (1)
a half is MISSING (no ceiling, or the judge
interval is not a measurement)               -> NOT CALIBRATED YET (3)
```

A judge whose interval includes zero has been measured and has failed, even with no ceiling on file.
A judge that beats chance with no ceiling has not been measured at all. One tells the owner to fix
the judge; the other tells them the measurement has not been made.

## What it changes in practice

A judge wrong about four of twenty rows, rendered twice. Same judge, same confusion matrix, same
point estimate. Only the labeller's second pass differs.

```
                       PERFECT LABELLER          LABELLER WHO CONTRADICTED ONE ROW

Cohen's kappa          0.600                     0.600
judge 95% CI           [+0.200, +0.900]          [+0.200, +0.900]
human ceiling          [+1.000, +1.000]          [+0.687, +1.000]

(a) beats chance       yes                       yes
(b) reaches ceiling    NO                        yes

status                 not_calibrated  exit 1    calibrated  exit 0
```

**The deleted threshold was 0.6, and this judge scores exactly 0.600.** It would have passed in both
columns. What decides now is whether the judge is distinguishably worse than the person who wrote
the labels, and against a labeller who reproduced every one of their own verdicts, it is.

## What the mutations found

Thirteen mutations, each run against the test that is supposed to own it, restored from a snapshot
(the work was uncommitted, so `git checkout HEAD` would have deleted it). **13/13 red**, but two
were green on the first sweep and both were real gaps rather than harness mistakes.

| Mutation | Owned by |
|---|---|
| the gate reads the POINT estimate instead of the interval | `test_a_judge_whose_interval_includes_zero_is_not_calibrated` |
| an unmeasured ceiling counts as a passed one | `test_no_second_pass_is_never_a_pass` |
| the ceiling compares point estimates instead of overlapping intervals | `test_the_same_judge_passes_against_a_labeller_who_is_not_perfect` |
| a one-sided bootstrap returns a number from the surviving resamples | `test_a_nearly_one_sided_corpus_is_also_refused` |
| a partial second pass is computed over the rows that were finished | `test_a_partial_second_pass_is_refused_rather_than_used` |
| a MEASURED failure is reported as "not calibrated yet" | `test_a_judge_below_a_perfect_labeller_beats_chance_and_still_fails` |
| the ceiling is measured over rows the judge never scored | `test_the_same_judge_passes_against_a_labeller_who_is_not_perfect` |
| the emitter overwrites an existing second-pass sheet | `test_it_refuses_to_overwrite_an_existing_sheet` |
| the emitter runs while the first pass is unfinished | `test_it_refuses_while_the_first_pass_is_unfinished` |
| the emitter pre-fills the verdict it is asking for | `test_it_writes_every_row_with_an_empty_verdict` |
| the emitted sheet keeps the first sheet's order | `test_the_rows_come_back_in_a_different_order` |
| readiness stops naming the missing ceiling | `test_readiness_names_the_missing_ceiling` |
| an unlabelled first sheet hides the missing ceiling | `test_an_unlabelled_sheet_names_BOTH_missing_owner_inputs` |

### `MAX_UNDEFINED_FRACTION` was pinned by nothing

Deleting the fraction check left every test green. `test_a_one_sided_corpus_is_refused_rather_than_scored`
feeds ten identical pairs, so EVERY resample is one-sided and the check one line later (`not defined`)
catches it regardless. The test that would have caught it, `test_a_nearly_one_sided_corpus_is_also_refused`,
called `pytest.skip` when the resamples stayed definable, so it could not fail.

Nine pass and one fail is the shape that separates them: 35% of resamples lose the minority label and
65% survive, so a percentile COULD be taken and must not be. That test now asserts both ends of the
fraction rather than skipping.

This is the shape `260818-green-for-the-wrong-reason.md` describes, found inside code written to
that note's own standard one day earlier.

### `--check` named one missing input where two were missing

The ceiling paragraph was printed only when the first sheet already had verdicts. On the shipped
tree, which has ten unlabelled rows, that means the owner labels the sheet, comes back, and is told
there is a second pass they had not been told about. Both are named now.

## Observed

```
--check on the real, unlabelled sheet     exit 3, names BOTH missing owner inputs
tests/unit/test_calibration_harness.py    59 passed
tests/unit/test_agreement_threshold.py    13 passed
tests/unit/test_agreement_statistics.py   15 passed
whole unit suite, restored tree           2574 passed, 13 skipped, 528.7s
mutations                                 13 of 13 red
```

## The one number still chosen, named rather than hidden

`MAX_UNDEFINED_FRACTION = 0.2` in `agreement.py`. It is a different kind of number from the 0.6 that
was deleted, and the difference is the direction it fails in: 0.6 decided whether a judge PASSED, so
choosing it wrong let a judge through. This one decides whether a measurement EXISTS, and choosing it
wrong makes the harness refuse a corpus it could have read. Nothing passes because of it.

## Still open

- **The sheet size.** `[owner]`, unchanged by this. 45 (scenario, dimension) rows exist, the sheet
  uses 10, and 10 can establish nothing: the interval at 10 rows includes zero.
- **The corpus.** Contaminated, so there is nothing to label until the k=5 re-capture.
- The owner's ceiling has the same size problem as the judge's: at 10 rows it is `[0.19, 1.00]`, too
  wide to cap anything.
