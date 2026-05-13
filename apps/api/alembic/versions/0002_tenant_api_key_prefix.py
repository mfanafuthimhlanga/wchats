"""Add api_key_prefix to tenants for O(1) auth lookup.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-13

Context:
    WR-01: get_current_tenant was O(N) argon2 hashes per request.
    This migration adds an indexed api_key_prefix column (first 16 hex chars of
    HMAC-SHA256(raw_key, ADMIN_KEY)) so auth can do a single indexed lookup before
    running one argon2 verify() instead of verifying every tenant hash.

    NULL is allowed for existing rows (legacy tenants without a prefix).
    get_current_tenant falls back to a full scan only for NULL-prefix rows.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("api_key_prefix", sa.Text(), nullable=True),
    )
    op.create_index(
        "tenants_api_key_prefix_idx",
        "tenants",
        ["api_key_prefix"],
    )


def downgrade() -> None:
    op.drop_index("tenants_api_key_prefix_idx", table_name="tenants")
    op.drop_column("tenants", "api_key_prefix")
