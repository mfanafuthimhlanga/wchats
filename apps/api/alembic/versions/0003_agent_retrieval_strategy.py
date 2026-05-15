"""Add retrieval_strategy JSONB column to agents for M3 hybrid retrieval.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-16

Context:
    RET-01: Each agent stores its hybrid retrieval configuration as a JSONB
    document so that vector-weight, BM25-weight, rerank-model, and
    strategy-specific parameters can evolve without schema migrations.

    Default is an empty object '{}'; the application layer applies
    per-tenant or per-agent defaults at query time.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN retrieval_strategy JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS retrieval_strategy")
