"""Tenant DB v4 migration — verified_qa_candidates staging table for M5 validation chain.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-23

Context:
    M5: Validation Chain — the Auditor writes high-confidence grounded QA pairs into
    this table for human review and potential inclusion in the verified training set.

    Columns (D-20 LOCKED):
      id               — UUID primary key
      conversation_id  — references the conversation where the QA pair was generated
      question         — user question text
      answer           — agent answer text
      citations        — JSONB array of citation spans (claim, source_chunk, supported)
      auditor_confidence — float [0,1] returned by the Auditor judge
      queued_at        — timestamp when the row was inserted
      status           — lifecycle state: 'pending' → 'approved' | 'rejected'

    Two indexes speed up:
      - Listing all candidates for a conversation (conversation_id lookup)
      - Filtering by review status (admin queue UI)
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE verified_qa_candidates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            citations JSONB NOT NULL DEFAULT '[]'::jsonb,
            auditor_confidence FLOAT NOT NULL,
            queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected'))
        )
    """)
    op.execute(
        "CREATE INDEX vqa_candidates_conversation_idx ON verified_qa_candidates(conversation_id)"
    )
    op.execute(
        "CREATE INDEX vqa_candidates_status_idx ON verified_qa_candidates(status)"
    )
    # C-02 fix: idempotency requires UNIQUE on (conversation_id, question) so
    # ON CONFLICT DO NOTHING has a conflict target and retries don't duplicate rows
    op.execute(
        "CREATE UNIQUE INDEX vqa_candidates_dedup_idx ON verified_qa_candidates(conversation_id, question)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verified_qa_candidates")
