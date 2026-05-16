"""Add soul fields to agents table for M4 reasoning engine.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-16

Context:
    AGT-02 / AGT-08: Each agent stores structured soul fields (soul_voice, soul_do_list,
    soul_donot_list, soul_role) that replace the legacy opaque soul JSONB blob for M4.

    These four columns are additive — the legacy soul JSONB and role TEXT columns from M1
    are preserved for backward compatibility (D-Schema decision in CONTEXT.md).

    soul_do_list and soul_donot_list use JSONB NOT NULL DEFAULT '[]'::jsonb so that
    the application layer can safely iterate them without null guards.

    soul_voice and soul_role are TEXT nullable — the build_system_prompt() function
    applies sensible defaults when these are None.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN soul_voice TEXT")
    op.execute(
        "ALTER TABLE agents ADD COLUMN soul_do_list JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE agents ADD COLUMN soul_donot_list JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute("ALTER TABLE agents ADD COLUMN soul_role TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS soul_role")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS soul_donot_list")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS soul_do_list")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS soul_voice")
