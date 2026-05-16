"""Tenant DB v3 migration — fix conversations schema for M4 agent session continuity.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-16

Changes:
  conversations: ADD COLUMN agent_id UUID
                 ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                 ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                 (Keep external_id, started_at, ended_at — safe; M1-M3 do not write these)

Rationale: R-01 from 04-RESEARCH.md — conversations table from migration 0001 is missing
agent_id and metadata columns required for M4 agent session continuity (run_agent_turn
task writes agent_id + SDK session_id into metadata on every turn). Without these columns,
run_agent_turn crashes on the first agent turn call.

M1-M3 tasks do not write to conversations — no data-loss risk from adding columns.

Index: conversations_agent_id_idx on (agent_id) enables efficient lookups of all
conversations belonging to a specific agent (GET /agents/{id}/conversations route).

T-04-01-03: Tenant DB schema drift mitigated — this migration is added to
alembic_tenant/versions and tracked in git; new tenant provisioning picks it up
automatically via apply_migrations() which runs alembic upgrade head.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations ADD COLUMN agent_id UUID")
    op.execute(
        "ALTER TABLE conversations ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE conversations ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    # Index for agent_id lookups (conversation ownership validation + list endpoint)
    op.execute("CREATE INDEX conversations_agent_id_idx ON conversations(agent_id)")


def downgrade() -> None:
    # Drop index before dropping the column it covers
    op.execute("DROP INDEX IF EXISTS conversations_agent_id_idx")
    # Drop three new columns in reverse order — DO NOT drop external_id/started_at/ended_at
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS metadata")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS created_at")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS agent_id")
