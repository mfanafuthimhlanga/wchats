# A finding's evidence and claims reach the table the gate reads (#310)

Branch `feat/red-team-finding-evidence-column` off `main` at `af7928f`. Since #312 and #314 a
`RedTeamFinding` carries `evidence` (`recorded_prompt_run`, `landed_verdict_tag`,
`attacker_report`) and `claims` (the kinds that stood). Both travel in `red_team_runs.findings`
JSON; the `red_team_findings` table, which the deploy gate groups by severity and the
programme reads for open findings, has neither, so the programme service recovers them by
correlating the run's JSON on (vector, probe message, turn count) and returns null on a miss.

## Migration 0033

`alembic_tenant/versions/0033_red_team_finding_evidence.py`, revises 0032. Additive: two
columns on `red_team_findings`, `evidence TEXT NOT NULL DEFAULT 'attacker_report'` with
`red_team_findings_evidence_check` on the three values, and `claims JSONB NOT NULL DEFAULT
'[]'` with `red_team_findings_claims_array` (`jsonb_typeof(claims) = 'array'`), because the
reader lists the value and an object or a scalar stored there would fail the whole programme
read. Each constraint is added in a guarded `DO` block reading `pg_constraint`, so a column
that exists without its constraint gets it on a re-run. A row written before this revision
reads as an attacker-word finding with no recorded claim kinds, which is what it was.
Comments on both columns. Downgrade drops the two constraints and the two columns, IF EXISTS. The test file
`tests/unit/test_migration_tenant_0033.py` follows the 0032 one: the statements are read by
patching `alembic.op.execute`, the tree is unforked, 0033 is the head; the 0032 head assertion
moves to "no longer the head".

## Writer and reader

- `run_red_team` in `app/worker/tasks/runtime/red_team.py` (the `INSERT INTO red_team_findings`
  around line 917) carries `evidence` and `claims` (as JSON) from the finding.
- `redteam_programme_service`: one tuple of column expressions builds the SELECT and names
  the row's fields, so the reader is by name and a reordered SELECT cannot swap evidence and
  claims. The JSON correlation remains only for `description`, which the table still lacks.
- `report_claims` caps a report at eight deduplicated labels, since any label the attacker
  types reaches the column as untrusted text.
- `deployment_service` groups by severity and needs no change. The API routes that return
  findings from the run JSON already carry both fields.

## Round trip

`run_tenant_migrations(dsn)` against `wchats_tenant_probe` (`TEST_TENANT_PROBE_URL`, default
`postgresql://wchats:wchats@localhost:5432/wchats_tenant_probe`, at 0032 on 2026-09-24), then
`command.downgrade(cfg, "0032")` on the same injected connection, then up again; the observed
revision and column set at each step recorded here, the way `.dev/plans/260920-claims-review.md`
records 0032's.

## Gates

- The migration test file, the programme service test with a row carrying both columns, the
  task test asserting the insert's parameters carry evidence and claims.
- Round trip observed 2026-09-24 on `wchats_tenant_probe` through `run_tenant_migrations`
  and `command.downgrade(cfg, "0032")` on the same injected connection:

```
before          revision 0032, 11 columns, no evidence check
after upgrade   revision 0033, 13 columns; evidence text NOT NULL default 'attacker_report';
                claims jsonb NOT NULL default '[]'; red_team_findings_evidence_check
                CHECK (evidence = ANY (ARRAY['recorded_prompt_run','landed_verdict_tag','attacker_report']))
after downgrade revision 0032, 11 columns, no check
after re-upgrade revision 0033, 13 columns, the same defaults and check
```

  The task's exact INSERT executed on the probe database stored
  `('landed_verdict_tag', ['mutating_call_landed'], 'jsonb')`; `evidence='model_said_so'`
  was refused by the check.
- `gates.py fast`; touched test files alone.
- Console: out of scope here; the programme route now returns the two fields from the table,
  and the panel showing them is the UI half of #310.
