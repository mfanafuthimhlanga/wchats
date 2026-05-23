"""Add strategy_resynthesis_flagged boolean column to agents table for M5 validation chain.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-23

Context:
    M5: Validation Chain — the Auditor tracks repeated "ungrounded" verdicts per agent.
    When three or more ungrounded verdicts occur within 24 hours the Strategist sets
    this flag to TRUE, signalling that the agent's retrieval strategy should be
    re-synthesised by the M5 Strategist re-synthesis job.

    Default is FALSE; flag is reset to FALSE after re-synthesis completes.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN strategy_resynthesis_flagged BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS strategy_resynthesis_flagged")
