# The faithfulness benchmark, per claim (#290 step 2)

Branch `feat/claims-benchmark`. Backend only, no labelling, gate unchanged.

## Shape

- `tests/evals/calibration/benchmark/`: `rows.csv` (30 real 735fb9fa answers and the 10 seeded
  ones), `truth.csv` (the ten planted sentences, `supported=false`, `source=construction`),
  `claims_<identity>.csv` and `scores_<identity>.csv` from one run of the production judge,
  `score_claims.py` vendored from the calibrate-judge skill, `BENCHMARK.md`.
- The scorer matches a judge claim to a truth claim by word containment both ways, prints every
  match, then recall over unsupported truth claims, precision over decided judge-unsupported
  claims with the undecided count beside it, and answer-level flag counts.
- `tests/unit/test_claims_benchmark.py`: the match rule, the three-way precision split, the
  answer level, and the benchmark files' integrity (each planted sentence verbatim in its answer
  and absent by word from its contexts).

## Observed 2026-09-19

The judge run: 40 rows, the first attempt lost 33 to provider connection errors and the
resumable second attempt scored the rest at concurrency 2. 81 judge calls in all, 779 claims.
The scorer's output over the shipped claims file, after the adversarial review moved the match
rule from a second overlap threshold to the `novel` word test:

```
recall    10 of 10 unsupported truth claims flagged, 95% interval [0.72, 1.00]
precision unknown: truth.csv holds no supported claim, so a false alarm cannot occur; 16 flags confirmed against planted sentences; 161 undecided, no truth yet
answers flagged (any unsupported claim): 10 of 10 with a planted claim, 28 of 30 without
unsupported claims per scored answer (40): min 0, upper median 4, max 25
```

The seeded rows at the 0.80 fraction gate on this run: 6 fail, 4 pass.

## Adversarial review, 2026-09-19

Four blockers: the note quoted the 2026-09-17 run's 7 of 10 where this run's scores file says 6
of 10; "precision 15 of 15" was a ratio whose denominator could hold only true alarms; one of the
15 was a claim the original answer already made and the judge already flagged before the plant;
no test pinned a published number and the one test reading the judge CSV broke on a second
identity. Fixed by the `novel` column and rule, the `unknown` precision line, the Wilson
interval, the published-numbers test per identity, and the committed `judge_rows.py` runner.
Also fixed: ambiguous matches leave the decided set, a flagged supported truth prints as a
false alarm, zero unsupported truth prints `unknown`, an answer with no judge claim is unscored,
a claims file naming an answer the benchmark lacks is refused, two-digit numbers count as words.
