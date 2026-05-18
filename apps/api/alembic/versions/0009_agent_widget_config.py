"""Add widget_config JSONB column to agents table for M4.2 widget customization.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-18

Context:
    M4.2: Admin UI phase — widget customization panel.
    Stores appearance, color palette, and typography settings configured by
    the tenant owner through the Deploy page. The widget.py endpoint reads
    this column to serve theming to the Preact iframe widget.

    Default is an empty object '{}'; the admin UI merges defaults at render
    time so the widget always has a complete color scheme even before the
    owner customizes it.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN widget_config JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS widget_config")
