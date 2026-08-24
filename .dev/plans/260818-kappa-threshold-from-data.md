# 8.2c · kappa-threshold-from-data

**Goal:** the calibration gate stops containing a number anyone chose. A judge is calibrated when
the data says it beats chance AND reaches a measured human ceiling, both with intervals.

**Owner instruction, 2026-08-18:** *"the kappa measurement must not be a choice it must be derived
from data"*. `KAPPA_THRESHOLD = 0.6` is currently admitted in its own comment to be a choice, taken
from a band in a talk. That is the last invented number in the harness.

## What "derived from data" can mean, and which ones are available

| Construction | What it needs | Available |
|---|---|---|
| **Beats chance, with evidence.** Bootstrap CI on kappa; require the lower bound above 0 | only the rows already being labelled | **yes, immediately** |
| **Reaches the human ceiling.** The same rows labelled TWICE by the same person, blind and separated. Their self-agreement kappa is the ceiling | one extra labelling pass over the same 10 rows | **yes, one owner action** |
| Inter-rater ceiling from two different people | a second labeller | no, and not worth hiring one for 10 rows |

**A judge cannot be expected to agree with a human more than that human agrees with themself.** That
is the ceiling, and it is why the second row is the real answer rather than a nicety: without it,
"kappa 0.62" has no scale to be read against.

## The gate

Both halves required. Either one missing means NOT CALIBRATED YET, never a pass.

```
(a) evidence it beats chance     judge_kappa_ci_low  > 0
(b) it reaches the ceiling       judge_kappa_ci_high >= human_kappa_ci_low
```

(b) is an overlap test, not a point comparison: the judge passes when it is **not distinguishably
worse** than the human is with themself. Comparing point estimates would fail a judge for noise on
ten rows, and comparing to a point ceiling would treat the human's own uncertainty as exact.

**With no ceiling measured, the harness says so and refuses.** That is the same rule as everywhere
else here: missing data is never passing data.

## Shape

`compute_correlation.py`, no new dependency:

```python
kappa_bootstrap_ci(pairs, *, iterations=10000, confidence=0.95, seed=20260818)
    -> {"low", "high", "point", "undefined_fraction"}
```

- **Seeded.** An unseeded bootstrap makes the gate flicker between runs, which is the same defect as
  an unset judge temperature one level up.
- **Resamples that yield an undefined kappa are counted, not silently dropped.** A resample where
  every label came out the same has no kappa; if that is most of them, the interval is not a
  measurement and the harness says the corpus is too one-sided rather than reporting a number.
- `human_scores.csv` gains `human_verdict_2`, empty, for the blind second pass. The ceiling is
  `cohens_kappa` over `(human_verdict, human_verdict_2)`.

## The risk this must state rather than discover

**Ten rows on a mostly-good corpus may not contain enough FAILs for any of this to be computable.**
Kappa is undefined when one label dominates completely, and the bootstrap inherits that. If the
owner's ten verdicts come back nine-pass-one-fail, both the judge kappa and the ceiling will have
wide or undefined intervals, and the honest output is "this corpus cannot calibrate a judge",
pointing at `8.4` (mine a real failure taxonomy, get more and more varied rows).

That is a better outcome than the current state, where a chosen 0.6 would have produced a verdict
regardless. It is also the reason to land this BEFORE the owner labels: the shape of the sheet
changes.

## Files

- `apps/api/tests/evals/calibration/compute_correlation.py` — the bootstrap, the two-part gate,
  `KAPPA_THRESHOLD` deleted rather than demoted.
- `apps/api/tests/evals/calibration/human_scores.csv` — `human_verdict_2`, empty.
- `apps/api/tests/unit/test_agreement_statistics.py` — the bootstrap's own tests.
- `apps/api/tests/unit/test_calibration_harness.py` — the gate's two halves.
- `.planning/phases/04-reasoning-engine-widget/AI-SPEC.md` — already updated 2026-08-18.

## Tests

1. A seeded bootstrap returns the same interval twice.
2. Perfect agreement over 20 balanced pairs gives a lower bound above 0.
3. A coin-flip judge gives a lower bound at or below 0, and does not pass (a).
4. A judge that beats chance but sits below the ceiling fails (b) while passing (a), and the report
   says which half failed.
5. No `human_verdict_2` anywhere means NOT CALIBRATED YET naming the missing ceiling, never a pass.
6. A one-sided corpus (all pass) reports the interval as undefined and refuses, rather than
   returning a number from the surviving resamples.
7. `undefined_fraction` is reported, and a run above a stated fraction refuses.
8. No constant threshold survives: `grep -n "KAPPA_THRESHOLD\|0.6" compute_correlation.py` returns
   nothing that gates.

**Mutation proof:** make the gate `judge_kappa_point > 0` (drop the interval) and observe test 3 go
red; restore from `HEAD`; observe green.

## Exit

- `apps/api` `scripts/gates.py full` green.
- `compute_correlation.py --check` on the real, unlabelled sheet names BOTH missing inputs: the
  verdicts and the ceiling.
