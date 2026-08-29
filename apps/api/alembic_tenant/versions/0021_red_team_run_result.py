"""Tenant DB v21 migration — a red-team run stores what it measured (ticket 15, #52).

Revision ID: 0021
Revises: 0020

Context:
    A completed run wrote four loose columns: `findings`, `max_severity`,
    `deployment_blocked` and `coverage`. Between them they cannot answer the one
    question ticket 15 asks. `max_severity` says how bad the worst finding was
    and never how many attempts landed one. `coverage` says which vectors
    observed the agent and never how many independent attempts each made. So a
    vector attacked once and a vector attacked three times read identically off
    the row, and every reader that wanted the difference had to recompute it from
    the findings blob and hope its arithmetic matched the next reader's.

    This migration adds the column that holds the answer as one value:

    result JSONB — nullable, no CHECK, no DEFAULT.
        `app.domain.red_team_result.RedTeamResult.payload` — {"k", "vectors":
        [{"vector", "attempts", "breaches", "max_severity"}], "breaches",
        "max_severity", "coverage"} — stamped at run completion.

    NULL means "this run did not record its result", which is every row written
    before this revision and every run on a tenant that has not been migrated.
    That is not the same claim as "this run measured nothing", and it is the same
    distinction 0015 drew for `coverage`: the reader reports the absence rather
    than substituting a number of its own.

    K IS ON THE ROW, NOT LOOKED UP. `settings.RED_TEAM_ATTEMPTS_PER_VECTOR` is
    live configuration. A reader that goes and reads it compares today's
    requirement against a run that happened under yesterday's, so raising the
    setting would silently turn every stored run incomplete and lowering it would
    silently turn a truncated run complete, without either run changing. The k
    the run ran under travels inside the payload.

    NO CHECK, NO NOT NULL, NO INDEX. `RedTeamResult` refuses a row that
    misreports a vector at construction, which is the guard that actually runs. A
    second copy in the catalogue would need its own migration each time the
    record changes, and the two would drift. Same reasoning as 0014's refusal to
    constrain `dataset` and 0020's refusal to constrain `judge_identity`.

    Raw SQL with an IF NOT EXISTS guard, the convention every tenant table has
    followed since 0008. No SQLAlchemy ORM model.

Additive, nullable, rollback-safe:
    The task's completion UPDATE catches psycopg2 UndefinedColumn and steps down
    to the pre-0021 statement, then to the pre-0015 one, so a tenant that never
    receives this revision keeps red-teaming and its runs simply cannot say how
    many attempts each vector made. Rolling back is a no-op for the application.

    APPLIED AND VERIFIED 2026-08-29 against the local `wchats_tenant_probe`
    cluster through the production path (`migrations.run_tenant_migrations`):
    0020 to 0021, the column arrives `jsonb` and nullable with no DEFAULT,
    downgrade drops it, re-upgrade restores it.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # red_team_runs.result — nullable JSONB. NULL means "this run did not
    # record what it measured", which readers report as such and never fill in
    # from the current build's configuration.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE red_team_runs ADD COLUMN IF NOT EXISTS result JSONB
    """)
    op.execute("""
        COMMENT ON COLUMN red_team_runs.result IS
        'RedTeamResult.payload: k, and one row per vector carrying attempts, '
        'breaches and max_severity. NULL means the run did not record it.'
    """)


def downgrade() -> None:
    # IF EXISTS so a downgrade against a database that never received 0021 is a
    # no-op rather than an error. Dropping the column loses each run's record of
    # how many attempts it made; every run row survives untouched and its
    # readers fall back to reporting the result as unrecorded.
    op.execute("ALTER TABLE red_team_runs DROP COLUMN IF EXISTS result")
