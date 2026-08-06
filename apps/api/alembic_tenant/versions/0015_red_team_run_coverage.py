"""Tenant DB v15 migration — a red-team run records its own coverage (P2 review).

Revision ID: 0015
Revises: 0014

Context:
    P2 computed `red_team_coverage()` in the red-team task and put it in a
    structlog line and the Celery return dict. Neither survives the request:
    the `UPDATE red_team_runs` wrote findings, max_severity and
    deployment_blocked and nothing else, so `GET /agents/{id}/red-team-runs`
    still answered `{findings: [], max_severity: null, deployment_blocked:
    false}` for a run in which four of seven attackers could not probe at all
    (audit D4) — byte-identical to a genuinely clean seven-vector run. The
    console reads that route. "Unknown" and "pass" render the same on screen,
    which is the recurrence .dev/retro.md Family B exists to stop.

    This migration adds the one column that lets a stored run answer for itself:

    coverage JSONB — nullable, no CHECK, no DEFAULT.
        {"vectors_attempted": int, "vectors_valid": int,
         "invalid_vectors": [str], "invalid_reason": str|null,
         "complete": bool} — exactly red_team_service.red_team_coverage()'s
        payload, stamped at run completion.

    NULL means "this run did not record its coverage" — every row written before
    this revision. That is not the same claim as "this run covered nothing", and
    the readers keep them apart: deployment_service._fetch_red_team_summary_sync
    falls back to the CURRENT BUILD's capability and labels the substitution
    `coverage_source='current_build'`, and the red-team routes report
    `coverage: null` with `coverage_recorded: false` rather than substituting
    anything at all.

    Why the run must carry it rather than the reader deriving it: coverage is a
    property of the code that ran, not of the code doing the reading. The day P4
    wires the four SDK attackers and flips SDK_ATTACKERS_CAN_PROBE, a reader
    that derives coverage from the shipped build would silently re-describe
    every historical three-of-seven run as seven-of-seven — rewriting the
    security history of every agent on the platform in one deploy.

    No CHECK, no NOT NULL, no index. The shape lives in red_team_service and
    _coverage_from_run() treats any payload missing one of the four keys as
    absent, so an unexpected value degrades to "not recorded" rather than
    breaking an UPDATE on a live tenant. Same reasoning as 0014's refusal to
    constrain `dataset`.

Additive, nullable, rollback-safe (the plan's risk register):
    Cannot be verified against a live database on this machine (no local
    PostgreSQL — every `-m integration` harness skips, and a skip is unobserved,
    never a pass). Strictly additive and strictly nullable: ADD COLUMN IF NOT
    EXISTS only, no CHECK, no NOT NULL, no DEFAULT, no backfill, no index, no
    constraint touched.

    Rolling back is a no-op for the application. The task's UPDATE catches
    psycopg2 UndefinedColumn and falls back to the pre-0015 statement; the deploy
    gate and both red-team routes do the same on their SELECTs. A tenant that
    predates this revision keeps red-teaming, its runs simply cannot say how much
    of the surface they covered — and they say THAT rather than implying full
    coverage.

    Follows the established raw-SQL convention (mirrors 0009/0010/0011/0012/
    0013/0014) — no SQLAlchemy ORM model, consistent with every other tenant-DB
    table.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # red_team_runs.coverage — nullable JSONB. NULL means "this run did not
    # record what it could test", which readers report as such and never
    # substitute with the current build's numbers.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE red_team_runs ADD COLUMN IF NOT EXISTS coverage JSONB
    """)


def downgrade() -> None:
    # IF EXISTS so a downgrade against a database that never received 0015 is a
    # no-op rather than an error. Dropping the column loses each run's record of
    # its own coverage; every run row survives untouched and its readers fall
    # back to reporting the coverage as unrecorded.
    op.execute("ALTER TABLE red_team_runs DROP COLUMN IF EXISTS coverage")
