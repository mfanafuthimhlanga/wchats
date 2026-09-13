# 0012: A clarifying question is checked by a rule, not by a fifth Judge

Status: proposed. Drafted 2026-09-12 from issue #226 and the plan in
`.dev/plans/260909-multi-turn-eval-scenarios.md`. Tenant migration 0028 added the
`ambiguous` column and nothing reads it yet. The decision that is open is where the
check's result lives.

## What is decided

A scenario whose correct reply is a clarifying question, rather than an answer, is marked
`eval_scenarios.ambiguous = true`, and its reference is the clarifying question. Whether
the agent's response passes is a deterministic check on the response text: it passes when
the response asks and fails when it answers. No model call, no threshold, the same shape
as the drafter's `lead_in_binds_the_question`.

The reasons this is a rule and not a fifth Judge:

- ADR 0008 makes a Judge one typed tool call, and ADR 0009 measured the four existing
  Judges at about 87 seconds per scenario on a shared rate. A fifth call per row makes
  every run slower for a question a regular expression settles.
- A model-generated label never gates a deploy (CLAUDE.md, measurement honesty). A rule
  can be mutated and observed to fail; a Judge's verdict on "is this a question" cannot.
- The four Ragas metrics measure the wrong thing for such a row. A correct clarifying
  question retrieves nothing, so the row never enters the scored set, and one that does
  retrieve scores near zero on faithfulness against chunks it should not have used.

## What is open: where the result lives

| Option | Grain | Effect on the deploy gate |
|---|---|---|
| A. `eval_samples` | one row per scenario, beside `resolved_question` | the checklist sums a fifth pass rate from samples; `eval_results` and every count it feeds are untouched |
| B. `eval_results` | one row per scenario per metric | a fifth metric key inflates every existing count by a quarter, and every reader of `datasets` sees a metric Ragas never produced |

Recommendation: A. The sample row already holds the conversation and the rewrite, so the
sheet and the owner read one row. The gate gains one rule over ambiguous rows, with its own
floor, in the style of ADR 0011's table.

## What building it takes

- `GoldenPair.ambiguous` on the API and MCP schema, and `insert_authored_golden_scenario`
  writing the column.
- The check itself, and a scoring path that admits an ambiguous row with no retrieved
  context instead of counting it as `no_retrieval`, since otherwise five such goldens push
  coverage under `EVAL_COVERAGE_FLOOR` and read as an agent failure.
- The rule in `deployment_service.py` and a row in this ADR's table once the grain is
  chosen.
