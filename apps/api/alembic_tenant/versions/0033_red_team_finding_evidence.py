"""Tenant DB v33 migration: a red_team_findings row says what it stood on (#310).

Revision ID: 0033
Revises: 0032

Context:
    Since #312 a `RedTeamFinding` carries `evidence`, how it stood, and since
    #314 `claims`, the claim kinds its grade came from. Both travel in
    `red_team_runs.findings` JSON. The `red_team_findings` table (0012) had
    neither, so the programme service recovered them by matching the run's JSON
    on (vector, probe message, turn count) and returned null on a miss. These two
    columns let the row say it directly.

`red_team_findings.evidence`
    TEXT NOT NULL, one of three values, held by the named CHECK
    `red_team_findings_evidence_check`:
      recorded_prompt_run  a recorded reply carried a run of the served system
                           prompt
      landed_verdict_tag   a recorded turn carried a landed verdict tag, so a
                           mutating tool call went through
      attacker_report      the finding rests on the attacker model's word
    The default is `attacker_report`, so a row written before this revision
    reads as an attacker-word finding, which is what it was.

`red_team_findings.claims`
    JSONB NOT NULL, the list of claim kinds that stood when the report was
    graded, held to a JSON array by the named CHECK
    `red_team_findings_claims_array`. The default is an empty list, so a row
    written before this revision reads as a finding with no recorded claim kinds.

WHAT READS THEM
    `redteam_programme_service._OPEN_FINDINGS_SQL`, which returns both on every
    open finding. The deploy gate groups this table by severity and reads
    neither.

Each column arrives through ADD COLUMN IF NOT EXISTS, and each CHECK through its
own block that adds it only when pg_constraint holds no constraint of that name.
A re-run therefore adds nothing, and a column that exists without its CHECK
gains the CHECK. Downgrade drops the two constraints and the two columns, each
IF EXISTS, and nothing else; the run's findings JSON still holds both fields.

APPLIED AND VERIFIED 2026-09-24 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`), with
`command.downgrade(cfg, "0032")` for the way down: 11 columns at 0032, 13 at
0033 with both defaults and the CHECK, 11 again after downgrade, 13 after
re-upgrade. The upgrade statements with both guarded CHECKs, run twice in a
rolled-back transaction from a table with neither column and from one with
`evidence` but no CHECK, left one of each constraint after each run, and the
array CHECK refused a `{}` claims value.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_constraint_once(name: str, check: str) -> str:
    """A DO block that adds CHECK `check` as `name` unless pg_constraint already has `name`."""
    return f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
                ALTER TABLE red_team_findings ADD CONSTRAINT {name} CHECK ({check});
            END IF;
        END $$
    """


def upgrade() -> None:
    op.execute("""
        ALTER TABLE red_team_findings
            ADD COLUMN IF NOT EXISTS evidence TEXT NOT NULL DEFAULT 'attacker_report'
    """)
    op.execute(_add_constraint_once(
        "red_team_findings_evidence_check",
        "evidence IN ('recorded_prompt_run', 'landed_verdict_tag', 'attacker_report')",
    ))
    op.execute("""
        ALTER TABLE red_team_findings
            ADD COLUMN IF NOT EXISTS claims JSONB NOT NULL DEFAULT '[]'::jsonb
    """)
    op.execute(_add_constraint_once(
        "red_team_findings_claims_array", "jsonb_typeof(claims) = 'array'"
    ))
    op.execute(
        "COMMENT ON COLUMN red_team_findings.evidence IS "
        "'What the finding stood on. recorded_prompt_run: a recorded reply carried a "
        "run of the served system prompt. landed_verdict_tag: a recorded turn carried "
        "a landed verdict tag, so a mutating tool call went through. attacker_report: "
        "the finding rests on the attacker model''s word. A row from before 0033 "
        "reads attacker_report (#310).'"
    )
    op.execute(
        "COMMENT ON COLUMN red_team_findings.claims IS "
        "'The claim kinds that stood when the report was graded, as a JSON list of "
        "strings. A row from before 0033 reads an empty list: no recorded claim "
        "kinds (#310).'"
    )


def downgrade() -> None:
    # IF EXISTS, so a downgrade against a database that never received 0033 is
    # a no-op. The run's findings JSON keeps both fields.
    op.execute(
        "ALTER TABLE red_team_findings DROP CONSTRAINT IF EXISTS red_team_findings_claims_array"
    )
    op.execute(
        "ALTER TABLE red_team_findings DROP CONSTRAINT IF EXISTS red_team_findings_evidence_check"
    )
    op.execute("ALTER TABLE red_team_findings DROP COLUMN IF EXISTS claims")
    op.execute("ALTER TABLE red_team_findings DROP COLUMN IF EXISTS evidence")
