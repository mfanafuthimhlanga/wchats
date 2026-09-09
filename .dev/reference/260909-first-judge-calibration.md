# The first Judge calibration: the relevancy Judge does not measure what the owner does

Measured 2026-09-09 on eval run `27882551` (agent `ee8087ed`, 31 scenarios, the Ragas
Judge `gpt-5.6-luna`, effort none, prompt `ragas-0.4.3`), against the owner's labels on
the fifteen scenarios he read twice. Artifact: `tests/evals/calibration/calibration.json`,
status `not_calibrated`. Sheets: `tests/evals/calibration/runs/27882551-.../`.

| | value |
|---|---|
| rows scored | 29 of 30 (one faithfulness cell null, #216) |
| Cohen's kappa, owner against Judge | -0.012, interval [-0.261, +0.220] |
| owner's own ceiling, two blind passes | 0.791, interval [+0.442, +1.000] |
| owner minus Judge | +0.804, interval [+0.406, +1.159] |
| Judge failed, owner passed | 15 rows |
| Judge passed, owner failed | 2 rows |
| both failed | 3 rows |

The Judge does not beat chance and is distinguishably worse than the owner's agreement
with himself. The gate reads `not_calibrated`, a measured fail, where every run before
read `not_calibrated_yet`.

## Why no threshold fixes it

Answer relevancy, the owner's label against the Judge's score:

| owner said | Judge scores |
|---|---|
| pass (10 rows) | 0.53, 0.56, 0.61, 0.66, 0.69, 0.69, 0.73, 0.77, 0.85, 0.96 |
| fail (5 rows) | 0.70, 0.77, 0.81, 0.94, 0.95 |

The fails sit higher than most of the passes. Sweeping the threshold from 0.50 to 0.90
gives kappa between 0.00 and -0.57. The 0.9 rule (#58's original question) is not what is
wrong; the number itself ranks the rows the other way from the owner.

The five fails are questions that named no project and were answered from a guessed one
(#226). Ragas relevancy asks whether the response reads as an answer to the question as
worded, and an answer about the wrong project reads as a perfect one. The owner asks
whether the customer got what they needed. Those are different questions, and the second
has no signal in the score.

Faithfulness: the owner passed all 14 rows; the Judge scored them 0.42 to 1.00 and failed
six against 0.9. With no owner fail on faithfulness the corpus says nothing about where a
faithfulness threshold should sit, only that the Judge is stricter than the owner on
answers the owner reads as grounded.

## What this decides

- The relevancy Judge cannot gate a deploy on this corpus, at any threshold. The gate
  stays `block` on `judge_not_calibrated`, and that is the correct reading.
- The owner's relevancy signal lives in the ambiguity behaviour (#226) and in multi-turn
  scenarios (#227), not in a Ragas score. Build those before spending on the Judge.
- ADR 0009: the choice between B (owned one-call Judge) and C is now informed. B is a
  new Judge that would need this labelling again; the labelling exists and takes an
  afternoon, so B's cost is known.

## How to produce it again

1. A checklist run on staging after 0027: `eval_samples` rows exist.
2. `calibrate_run.py --sheet <eval_run_id>` with `CALIBRATION_TENANT_DSN` exported; the
   owner labels on a page seeded from the sheet, the labels come back through the page's
   database, and `human_scores.csv` is written from them.
3. `--second-pass`, the owner labels blind, `human_scores_pass2.csv`.
4. `--score`. The artifact lands at `tests/evals/calibration/calibration.json`.

The first sheet was restricted to the fifteen scenarios labelled twice; the full 62-row
first pass is beside it as `human_scores_all62.csv`. Twenty of those 62 were auto-passed
on the owner's instruction and were not read.
