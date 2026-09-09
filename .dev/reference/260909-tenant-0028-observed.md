# Widening the eval scenario SELECT: what tenant 0028 cost and what it was observed to do

Two findings from landing tenant migration 0028 (`turns` and `ambiguous` on
`eval_scenarios`, `turns` and `resolved_question` on `eval_samples`). The first applies to
any future column added to the nightly eval's scenario query. The second is the migration
measurement, so the next reader does not repeat it.

## A new column in the scenario SELECT is three changes, and shipping one of them is worse than shipping none

`run_eval_suite` fetches scenarios with two queries, golden and exploratory, and falls back
to a single pre-0014 query when PostgreSQL raises `UndefinedColumn`. That fallback has no
`dataset` column, so it returns every row as exploratory.

**Change one, the read ladder.** Name a new column in the two queries and nothing else, and a
tenant database one migration behind takes the pre-0014 fallback: it loses the golden set,
and with it the paired per-item delta that is the only way a regression between two runs can
be seen, while the log says the database predates 0014, which is false. So the fetch walks
projections widest first (`_fetch_scenario_rows` in
`apps/api/app/worker/tasks/runtime/eval.py`):

| Rung | Columns | Costs |
|---|---|---|
| 0028 | `dataset, turns, ambiguous` | nothing |
| 0014 | `dataset` | `turns` and `ambiguous`, which a database without them holds no row using |
| pre-0014 | neither | the golden split, so `dataset_column_available` returns False |

**Change two, the write path, and this is the one that bites.** `write_eval_samples` names
`turns` too. A tolerant read plus an intolerant write is strictly worse than no tolerance at
all: the 0027 tenant now runs every scenario, pays for every agent turn, and dies on the last
write before scoring, where `run_eval_suite` records the run `failed` with no `eval_results`
and no retry. Measured against the probe cluster at 0027: `write_eval_samples` raised
`UndefinedColumn`, and the same rows on the pre-0028 INSERT went down. `_INSERT_EVAL_SAMPLE`
now has `_INSERT_EVAL_SAMPLE_PRE_0028` behind it, matching `insert_eval_run`'s pre-0013
fallback in the same module. **Grep every writer for the column, not just the readers.**

The write tolerates one schema generation and the read tolerates two, which is not a gap:
`eval_samples` was created by 0027, so a database below that has no table to write to under
any schema. The parity that matters is per rung, and the rung that exists on both sides is
0027 against 0028.

**Change three, the coupling between the projection and the read.** The rows are keyed by the
projection that produced them (`_named`), and `_SCENARIO_COLUMNS` is one tuple of names each
rung is a prefix of, so the SELECT list and the mapping move together. Before that, the row
was read positionally 125 lines from the projection that filled it: swapping two names in a
projection put each scenario's reference answer to the agent as its question and scored the
answer against the question text, which is audit defect D1, on every tenant, with 77 tests
still green. A rung that is not a prefix now fails
`test_every_rung_is_a_prefix_of_one_column_order`.

The ladder's own test is `test_a_tenant_without_the_turns_column_keeps_its_golden_split`,
which fails when the middle rung is deleted.

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

**The order of the two bounds is part of the bound.** `_read_turn_history` takes
`LIMIT TURN_HISTORY_MAX_MESSAGES` in SQL and drops empty rows afterwards, so blank rows spend
their places. Dropping empties first and counting last let forty real messages followed by ten
blank ones travel as forty rows where the chat path sends thirty, and reach ten turns further
back. Filter roles, take the newest forty, then drop empties and cut.

**Emptiness is measured after the cut, which is one deliberate divergence from the chat
path.** Measured before it, a row whose first 4000 characters are whitespace survives one pass
and is dropped by the next, so the function is not idempotent, and the two callers
(`_scenario_dict` at the read, `_run_one_eval_turn` at the seam) disagree about what the model
was given. Both callers need idempotence, so the strip test reads the truncated string.

Every dropped turn is logged (`eval_scenario_history.turns_dropped`), and a `turns` column
that is not a JSON array is logged separately, because `ADD COLUMN IF NOT EXISTS` steps over a
pre-existing column of another type in silence and stamps 0028 anyway. Observed: a hand-added
`turns TEXT` at 0027 survived `alembic upgrade head` as `text`, nullable, no default. Without
the log line every scenario on that tenant would run single-turn forever with nothing said.
