# The faithfulness benchmark

The platform's own set of answers with known truth per claim, for measuring the faithfulness
judge's recall and precision on every judge, prompt or model change. It is the owner's and never
a tenant's; a tenant's yes or no on a flagged claim lands here as a labelled row (owner decision
2026-09-17).

## Files

| File | One row per | Columns |
|---|---|---|
| `rows.csv` | answer | `scenario_id, source, question, resolved_question, response, retrieved_contexts, reference` |
| `truth.csv` | claim with known truth | `scenario_id, claim, supported, novel, source, added, note` |
| `claims_<identity>.csv` | claim the judge decided | `scenario_id, position, statement, supported, reason` |
| `scores_<identity>.csv` | answer the judge scored | `scenario_id, dimension, verdict, score, claims` |
| `ground_rows.py` | | scores `truth.csv` and `rows.csv` with the grounding rule and prints recall on the planted sentences and the real answers' scores at the gate; no model call |
| `score_claims.py` | | the scorer, vendored from `~/.claude/skills/calibrate-judge`; edits go to the skill first |
| `import_reviews.py` | | turns a run's Tenant answers into the REVIEWED benchmark below; never writes here |
| `reviewed/` | | a second benchmark of the same three file kinds, holding reviewed truth; absent until the first import |

`source` in `truth.csv` is `construction` for a sentence planted into an answer or `review` for a
claim a reviewer answered yes or no on. `novel` is the planted sentence's fabricated detail as
words, the words neither the retrieved text nor the original answer carries; a judge claim
counts as being about the plant only when it carries one. `<identity>` is
`<model>-<reasoning_effort>-<prompt_version>`, the judge identity `eval_results` stores.

The ten planted sentences are each appended after the answer's citations, uncited and in plain
prose where the agent's own answers carry bold, backticks and source parentheticals. A judge that
found them by style rather than by support would score the same here, so recall on planted
sentences is an upper bound on recall against a fluent fabrication.

`claims_*.csv` and `scores_*.csv` carry the judge's verdicts. A reviewer's sheet is built from
`rows.csv` and the judge's `statement` column only; `supported`, `reason`, `verdict` and `score`
never reach it.

## Run it

From `apps/api`:

```bash
.venv/Scripts/python.exe tests/evals/calibration/benchmark/score_claims.py \
  --benchmark tests/evals/calibration/benchmark \
  --claims tests/evals/calibration/benchmark/claims_<identity>.csv
```

Prints every match between a truth claim and the judge's claims, then recall with its interval,
precision or `unknown` with the confirmed and undecided counts, and the answer-level flag counts.
Precision reads `unknown` until `truth.csv` holds a supported claim, because until then a false
alarm cannot occur.

The grounding rule:

```bash
.venv/Scripts/python.exe tests/evals/calibration/benchmark/ground_rows.py \
  [--floor 0.4] [--threshold 0.80]
```

`--floor` defaults to `CARRIED_FLOOR` in `app/domain/grounding.py` and `--threshold` to 0.80.
A claims file added here needs its numbers in `PUBLISHED` in
`tests/unit/test_claims_benchmark.py`, which refuses a shipped claims file with no published row.

## Adding truth

A planted claim: append the sentence to a copy of an answer in `rows.csv` under the answer's
`scenario_id` suffixed `-seeded`, and add a `truth.csv` row with `supported=false`,
`source=construction` and `novel` listing the words neither the retrieved text nor the original
answer carries. The unit test refuses a planted sentence that is not verbatim in its answer, that
is in the original answer, or whose `novel` words appear in either.

A reviewed claim lands in `reviewed/`, never here, because a Tenant's answer is a label about
a real answer and the numbers in this directory are pinned per judge identity. From `apps/api`:

```bash
CALIBRATION_TENANT_DSN=... .venv/Scripts/python.exe   tests/evals/calibration/benchmark/import_reviews.py --run <eval_run_id>
```

writes `reviewed/truth.csv` (`supported` as the Tenant answered, `source=review`, `novel` empty,
so the scorer matches the judge's own statement exactly and credits no neighbour),
`reviewed/rows.csv` (the run and scenario id, no text) and `reviewed/claims_<identity>.csv`
(the judge's statements and whether it found them, no reason). Score it with `score_claims.py
--benchmark tests/evals/calibration/benchmark/reviewed`. A second import of a run replaces its
rows.
