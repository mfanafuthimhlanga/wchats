"""Replace unconditional UNIQUE on clerk_user_id with partial unique index.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-17

Context:
    WR-03 fix: The migration 0005 added clerk_user_id with an unconditional UNIQUE
    constraint. This prevents a soft-deleted user from re-registering via Clerk —
    the ON CONFLICT DO NOTHING in the webhook handler silently skips the INSERT
    because the clerk_user_id already exists in the soft-deleted row.

    Fix: Drop the unconditional UNIQUE constraint and replace it with a partial
    unique index that only enforces uniqueness among active (non-deleted) tenants
    where clerk_user_id IS NOT NULL.

    After this migration a user can be soft-deleted and re-register; a new tenant
    row can be INSERTed for them without conflicting with the old soft-deleted row.

    The basic non-unique index from 0005 (tenants_clerk_user_id_idx) is also dropped
    and recreated as part of this migration since the partial unique index subsumes it
    for the non-null active case.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the unconditional UNIQUE constraint added in 0005.
    # PostgreSQL names the constraint based on the column; it may be
    # "tenants_clerk_user_id_key" (default) or have a different name.
    # Using ALTER TABLE ... DROP CONSTRAINT is safest.
    op.execute(
        "ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_clerk_user_id_key;"
    )
    # Drop the plain non-unique index from 0005 (superseded by the partial unique index below)
    op.execute("DROP INDEX IF EXISTS tenants_clerk_user_id_idx;")
    # Add partial unique index: only active (non-deleted) rows with a non-null clerk_user_id
    op.execute(
        "CREATE UNIQUE INDEX tenants_clerk_user_id_active_uniq "
        "ON tenants(clerk_user_id) "
        "WHERE deleted_at IS NULL AND clerk_user_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS tenants_clerk_user_id_active_uniq;")
    # Restore the plain non-unique index from 0005
    op.execute(
        "CREATE INDEX tenants_clerk_user_id_idx "
        "ON tenants(clerk_user_id) WHERE clerk_user_id IS NOT NULL;"
    )
    # Restore the unconditional UNIQUE constraint (data must allow it for downgrade to succeed)
    op.execute(
        "ALTER TABLE tenants ADD CONSTRAINT tenants_clerk_user_id_key UNIQUE (clerk_user_id);"
    )
