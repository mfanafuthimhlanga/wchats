"""Add clerk_user_id to tenants.

Revision ID: 0005
Revises: 0004
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenants ADD COLUMN clerk_user_id TEXT UNIQUE;")
    op.execute(
        "CREATE INDEX tenants_clerk_user_id_idx ON tenants(clerk_user_id) WHERE clerk_user_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS tenants_clerk_user_id_idx")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS clerk_user_id")
