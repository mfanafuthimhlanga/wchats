# 0012: A clarifying question is checked by a rule, not by a fifth Judge

Status: accepted. Decided with the owner 2026-09-12 on issue #226, built the same day on
tenant migration 0029.

## What was decided

A scenario whose correct reply is a clarifying question, rather than an answer, is marked
`eval_scenarios.ambiguous = true`, and its reference answer is that question. Whether the
agent's response passes is a deterministic check on the response text,
`is_clarifying_question` in `app/services/clarifying_check.py`: the response asks when its
last sentence ends in a question mark and the whole reply is at most `CLARIFYING_MAX_WORDS`
long. Both halves are needed. "Run pnpm dev from the repo root. Anything else?" ends in a
question and is an answer.

The verdict lives on the sample row, `eval_samples.clarifying_check`, beside the strings the
rule read. An ambiguous row has no `eval_results`: it never reaches the Judge.

The verdict is counted where a Judge verdict is counted. The invocation loop decides the
row, `_score_run` writes it and keeps it out of the Ragas set, and the run's validity
report carries the mapping of scenario to verdict. `summarise_run_validity` counts each as
scored and `dataset_verdict_counts` counts asked as passed and answered as failed, in the
same per-dataset counts `decide()` already reads. So an ambiguous golden that answered is a
`golden_failure` and an ambiguous exploratory row that answered lowers the exploratory pass
rate, with no new rule and no fifth metric.

The golden writer holds an ambiguous pair's reference to the same rule, so a pair cannot
demand a behaviour its own reference would fail.

## Why a rule

- ADR 0008 makes a Judge one typed tool call, and ADR 0009 measured the four existing
  Judges at about 87 seconds per scenario on a shared rate. A fifth call per row makes
  every run slower for a question a rule settles.
- A model-generated label never gates a deploy. A rule can be mutated and observed to
  fail; the six mutations that proved this build were.
- The four Ragas metrics measure the wrong thing for such a row. A correct clarifying
  question retrieves nothing, so before this decision the row never entered the scored
  set and five such goldens blocked the checklist as unconfirmed, reading as an agent
  failure.

## Why the sample row and not a fifth metric

`eval_results` is one row per scenario per metric, and every count the console and the
deploy gate derive from it would have grown by a quarter for a column Ragas never
produced. `eval_samples` is already one row per scenario and already holds the
conversation and the rewrite, so the owner, the sheet and the gate read one row.

## Consequences a reader will meet

- `CLARIFYING_MAX_WORDS` is 40 and untuned. The first ambiguous run's rows are what tunes
  it, on the same sheet #58 labels.
- `register_golden_scenarios` takes `ambiguous` on each pair, over the API and over MCP.
- A tenant behind 0029 records an ambiguous row without its verdict and the writer logs
  which rung it fell to. The run still completes.
- The calibration sheet does not yet show the column. A labeller reading an ambiguous row
  sees the response and the reference and no verdict beside them.
