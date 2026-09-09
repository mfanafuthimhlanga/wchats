# Widening the eval scenario SELECT: what tenant 0028 cost and what it was observed to do

Two findings from landing tenant migration 0028 (`turns` and `ambiguous` on
`eval_scenarios`, `turns` and `resolved_question` on `eval_samples`). The first applies to
any future column added to the nightly eval's scenario query. The second is the migration
measurement, so the next reader does not repeat it.

## Adding a column to the scenario SELECT costs a tenant its golden set, unless a rung is added with it

`run_eval_suite` fetches scenarios with two queries, golden and exploratory, and falls back
to a single pre-0014 query when PostgreSQL raises `UndefinedColumn`. That fallback has no
`dataset` column, so it returns every row as exploratory.

Name a new column in the two queries and nothing else, and a tenant database one migration
behind takes the fallback. It keeps evaluating, which is the intent, but it loses the
golden set, and with it the paired per-item delta that is the only way a regression between
two runs can be seen. The log then says the database predates 0014, which is false.

The fetch now walks projections widest first (`_fetch_scenario_rows`, `_DATASET_PROJECTIONS`
in `apps/api/app/worker/tasks/runtime/eval.py`):

| Rung | Columns | Costs |
|---|---|---|
| 0028 | `dataset, turns, ambiguous` | nothing |
| 0014 | `dataset` | `turns` and `ambiguous`, which a database without them holds no row using |
| pre-0014 | neither | the golden split, so `dataset_column_available` returns False |

**A new column belongs in a new top rung, never appended to the existing one.** A rung may
only drop trailing columns: `_scenario_dict` reads the row positionally behind a
`len(row) >` guard per optional column, so reordering a projection puts a question where a
reference belongs.

The test is `test_a_tenant_without_the_turns_column_keeps_its_golden_split` in
`tests/unit/test_eval_task.py`, which fails when the middle rung is deleted.

## The migration, measured

Local cluster, PostgreSQL 17.6, `wchats_tenant_probe`, through the production path
`migrations.run_tenant_migrations`.

Round trip on empty tables:

```
before                 revision 0027, columns 0
after upgrade head     revision 0028, columns 4
after downgrade 0027   revision 0027, columns 0
after re-upgrade       revision 0028, columns 4
```

Both tables were empty in that run, which is not the case any tenant presents. A second run
seeded one `eval_scenarios` row and one `eval_samples` row at 0027 and carried them up:

```
at 0027, columns absent
seeded rows at 0027: [1, 1]
upgrade to 0028 against populated tables: statement returned, no error
   eval_scenarios -> [([], False)]
   eval_samples   -> [([], None)]
```

So the defaults describe a pre-0028 row correctly, and no backfill is owed. Both scripts are
transient; the reproduction is nine lines of `psycopg2` plus `run_tenant_migrations(dsn)` and
`command.downgrade(cfg, "0027")` with `script_location` set to `_ALEMBIC_TENANT_DIR`.

`resolved_question` is nullable on purpose. The question resolution step is a model call, and
a failed one scores the raw question and marks the row, so NULL and the empty string are
different states.

## The history a scenario may carry is the chat path's, not its own

`_scenario_history` filters a scenario's `turns` to `user` and `assistant` roles and applies
`TURN_HISTORY_MAX_MESSAGES` and `TURN_HISTORY_MAX_ROW_CHARS`, imported from
`app.worker.tasks.runtime.agent` rather than copied. A scenario's turns arrive from a JSONB
column an author, a miner or a drafter wrote, so an unfiltered read could put a second system
prompt or sixty long messages through the model, which no live conversation reaches, and then
report the score as representative.

It is bounded at the READ (`_scenario_dict`), not at the seam, because two readers need the
same list: the model gets it as `history`, and `write_eval_samples` puts it in front of the
owner to label. Bounding it later would show a labeller turns the model never saw. The
function is idempotent, so the seam bounds it again for a caller that skipped the read.
