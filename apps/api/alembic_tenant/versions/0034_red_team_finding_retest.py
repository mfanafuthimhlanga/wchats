"""Tenant DB v34 migration: an owner re-tests a red-team finding.

`red_team_findings.status` gains `resolved`
    A finding whose recorded attack was replayed against the current agent and
    drew an explicit "nothing landed" report under today's red-team rules. Every
    gate reader counts `status = 'open'`, so a resolved finding stops blocking
    with no reader changed. `red_team_findings_status_check` is dropped and added
    again with the fourth value.

`red_team_findings.retest`
    JSONB, NULL until the first re-test is queued. What the latest re-test did:
    its id, when it started, whether it is running, its outcome (`resolved`,
    `still_lands`, `inconclusive`) and, when it still lands, the grade, evidence,
    claims, probe and reply it stood on and the grade the finding had before.
    Held to a JSON object by `red_team_findings_retest_object`.

A re-run of the upgrade adds nothing: the column is ADD COLUMN IF NOT EXISTS,
the status CHECK is dropped if present and added once, and the object CHECK is
added only when pg_constraint does not name it. Downgrade turns every resolved
finding back into `closed`, the nearest value the old CHECK allows, restores the
three-value CHECK, and drops the column and its CHECK.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS_CHECK = "red_team_findings_status_check"
_RETEST_CHECK = "red_team_findings_retest_object"


def _status_check(values: str) -> list[str]:
    return [
        f"ALTER TABLE red_team_findings DROP CONSTRAINT IF EXISTS {_STATUS_CHECK}",
        f"ALTER TABLE red_team_findings ADD CONSTRAINT {_STATUS_CHECK} CHECK (status IN ({values}))",
    ]


def upgrade() -> None:
    for statement in _status_check("'open', 'contained', 'closed', 'resolved'"):
        op.execute(statement)
    op.execute("ALTER TABLE red_team_findings ADD COLUMN IF NOT EXISTS retest JSONB")
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{_RETEST_CHECK}') THEN
                ALTER TABLE red_team_findings ADD CONSTRAINT {_RETEST_CHECK}
                    CHECK (retest IS NULL OR jsonb_typeof(retest) = 'object');
            END IF;
        END $$
    """)
    op.execute(
        "COMMENT ON COLUMN red_team_findings.retest IS "
        "'What the latest owner re-test did: id, started_at, status, outcome (resolved, "
        "still_lands, inconclusive) and, when it still lands, the grade, evidence, claims, "
        "probe and reply it stood on and the grade before. NULL until a re-test is queued.'"
    )


def downgrade() -> None:
    op.execute("UPDATE red_team_findings SET status = 'closed' WHERE status = 'resolved'")
    for statement in _status_check("'open', 'contained', 'closed'"):
        op.execute(statement)
    op.execute(f"ALTER TABLE red_team_findings DROP CONSTRAINT IF EXISTS {_RETEST_CHECK}")
    op.execute("ALTER TABLE red_team_findings DROP COLUMN IF EXISTS retest")
