# Calibrate the Judge from an eval run (#58)

The deploy gate reads a calibration artifact that names the Judge which scored the run. The
harness that writes that artifact scores with a retired judge over a rubric the platform
never runs, so no artifact it writes can name the platform's Judge (#58, comment of
2026-08-30). Two eval runs finished on 2026-09-07 with the platform's Judge, and nothing
they scored can be labelled: the agent's answers and the contexts it retrieved live only in
memory during scoring. This plan lands the text, then a harness mode that reads verdicts off
a run instead of asking a judge.

## The number and its baseline

Cohen's kappa between the owner's pass or fail and the Judge's `binary_verdict`, per row,
over the two gated metrics (faithfulness, answer relevancy), for the Judge identity every
`eval_results` row of the run carries. Baseline is `not_calibrated_yet`, reason
`no_artifact`, on every checklist run to date. Seed is `agreement.BOOTSTRAP_SEED`.

## PR A: the text survives the run

```sql
-- tenant migration 0027
CREATE TABLE eval_samples (
    id                 UUID PRIMARY KEY,
    eval_run_id        UUID NOT NULL,
    scenario_id        TEXT NOT NULL,
    dataset            TEXT,
    user_input         TEXT NOT NULL,
    response           TEXT NOT NULL,
    retrieved_contexts JSONB NOT NULL,
    reference          TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON eval_samples (eval_run_id);
```

One row per scenario the run scored, the exact four strings Ragas was handed. Written by
`eval_service.write_eval_samples(run_id, scored_scenarios, conn_str)` from `run_eval_suite`
after the agent turns and before `run_ragas_eval`, so a run that dies inside scoring still
leaves what it was scoring. Same shape as `write_eval_results`: psycopg2, one INSERT per
row, commit once, close in `finally`.

Tests, unit:

- `test_migration_tenant_0027.py`: revision chain, the table and its columns, downgrade
  drops it and nothing else. Round trip against `wchats_tenant_probe` through
  `run_tenant_migrations`, observed output recorded in the docstring.
- `write_eval_samples` inserts one row per scored scenario with the four strings verbatim,
  and no row for an empty list.
- `run_eval_suite` calls `write_eval_samples` with the rows it then hands to
  `run_ragas_eval`, and calls it first.

## PR B: the harness reads a run

`compute_correlation.py --sheet-from-run <run_id>` reads `eval_samples` for the run over
`CALIBRATION_TENANT_DSN` and writes `run_<id>.csv`: one row per (scenario, gated metric) with
question, response, contexts, reference and an empty `human_verdict`. No judge verdict on the
sheet. `--emit-second-pass` works on it unchanged.

`compute_correlation.py --score-run <run_id>` reads the labelled sheet and its second pass,
joins each row to the run's `eval_results` row on (scenario_id, metric) for `binary_verdict`
and `judge_identity`, and runs the existing kappa, ceiling and difference. The judge adapter
is a lookup, not a call, so the artifact's `judge_identity` is the one the run stamped and
`--score-run` spends nothing. `calibration.json` lands where `load_calibration_status` reads
today.

## Then

Staging up, one checklist run, the owner labels 31 rows twice on the sheet, `--score-run`,
and the first artifact with a Judge on it. The number goes to `.dev/reference/`.
