"""Rename tenants.api_key column to api_key_hash.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-17

Context:
    WR-01 fix: The DB column was named 'api_key' but the ORM attribute is
    'api_key_hash'. Renaming aligns the column name with the attribute intent —
    the column stores an argon2id hash, not the raw key. This prevents future
    developers from misreading DB dumps and accidentally treating the stored
    value as a plaintext key.

    Raw SQL in webhooks.py references 'api_key'; that code is updated in the
    same fix to use 'api_key_hash'.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("tenants", "api_key", new_column_name="api_key_hash")


def downgrade() -> None:
    op.alter_column("tenants", "api_key_hash", new_column_name="api_key")
