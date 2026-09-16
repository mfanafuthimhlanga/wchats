# 0014: Answer relevancy is reported, not gated

Status: accepted. Decided with the owner 2026-09-16 on issue #285. Related: #270, #274,
#276, #277.

## What was decided

Faithfulness is the one judged metric a deploy gates on, beside the golden pass rate, the
coverage floor and the red-team floor. Answer relevancy is computed, stored and reported
on every run, on the console, on `get_eval_results` and over MCP, and it carries no
verdict and blocks nothing.

`GATED_METRIC_KEYS` in `app/services/eval_service.py` is `("faithfulness",)` and
`threshold_for("answer_relevancy")` returns None. Every reader downstream follows from
those two, because a metric with no threshold already had a defined meaning here.
`context_precision` and `context_recall` have never carried a verdict.

## The two measurements

The owner labelled relevancy on run 735fb9fa, passed every one of its 30 rows, and states
that every answer has been relevant. The earlier run 0a99f7ab stands at 28 relevancy
passes over 46 labelled rows. A gate needs a dimension the owner sometimes fails. Without
one, kappa is undefined by construction and the gate can never be shown to catch anything
it was put there to catch.

Neither instrument has anything to gate on either. Ragas answer relevancy failed 47 of
the 49 rows the owner passed, so gating on it blocks deploys the owner would ship. The
typed relevance Judge of #276 passed every row the owner passed, so gating on it blocks
nothing the owner would not already ship.

## What changes

- A relevancy row in `eval_results` carries `threshold` NULL and `binary_verdict` NULL,
  the shape the two context metrics have always had. Its `score` is written as before.
- A scenario's verdict is `scenario_verdict` over one stored verdict. `scenarios_passed`,
  `scenarios_failed` and `scenarios_unmeasured`, on the run record and on the results
  route, move only on faithfulness.
- `golden_failure` and `golden_unconfirmed` in `app/domain/verdict.py` read those counts,
  so a golden scenario that answered relevantly enough for the owner and ungroundedly for
  faithfulness still blocks, and the reverse no longer does.
- `_unmeasured_gated_metrics` refuses a run that measured no faithfulness. A run that
  measured no relevancy is admissible.
- `EVAL_RELEVANCY_THRESHOLD` is gone from `Settings` and from `.env.example`. A deployment
  that still sets it in the environment is ignored rather than refused, because pydantic
  settings tolerate an unknown key.
- The calibration sheet writes one row per scenario. `calibrate_run.GATED_METRICS` is
  `("faithfulness",)`, so `--sheet` asks the owner for the labels that decide something
  and the second pass and the human ceiling read whatever the sheet holds.

## What does not change

- Relevancy is still scored by the same Judge under the same purpose,
  `judge_answer_relevancy`, and still billed to it.
- ADR 0010 stands. A scenario carrying turns still has its question rewritten before
  relevancy scores it, because a relevancy number is only readable against the question
  it was scored on, gated or not.
- ADR 0011 stands, and it is the one place a fact about relevancy can still refuse a
  launch. `_relevancy_provenance_cause` blocks when most multi-turn rows were scored
  against a raw follow-up, on the ground that the number cannot be read rather than that
  it is low. Whether a reported metric may refuse a launch over its own provenance is the
  next decision and it amends ADR 0011, not this one.
- Rows written before this decision keep the threshold and the verdict they were judged
  against. `JudgeRecord` stores the gate on the row, so an old run reads back as the run
  it was and moving the gate afterwards does not restate it.
- The console keeps all four channels on the telemetry chart and keeps relevancy in the
  scenario ledger. Its verdict chip reads the API's `passed`, so it followed the new set
  with no client change.
- The change is prospective, and the deployment checklist is where a reader meets that. A
  run's scenario counts are built at scoring time under the gate then in force and are
  never restated, and `_fetch_eval_summary_sync` lifts `record.scenarios_failed` off the
  stored record rather than recomputing it. A tenant whose latest run predates this merge
  therefore keeps that run's relevancy failures in its checklist until the next eval run.
  The results route is the exception, because it computes `passed` live over
  `GATED_METRIC_KEYS` from the stored verdict columns, so the per-scenario screen reads
  faithfulness alone from the moment this ships. The deploy-readiness headline average in
  `apps/admin/app/agents/[id]/deploy/evalReadiness.ts` still pools all four measured
  metrics, and that headline is not the gate.

## Consequences a reader will meet

- A calibration artifact measured before this decision holds relevancy rows. They are a
  record of an agreement figure for a Judge that no longer gates, and `compute_correlation`
  still reads them if a sheet carries them.
- Kappa for the deploy gate is now a single dimension's number. The gate's calibration is
  as good as faithfulness labelling is, and nothing else averages into it.
- A run can now ship with every relevancy row failing. That is the decision, and the
  relevancy column is where an owner sees it.
