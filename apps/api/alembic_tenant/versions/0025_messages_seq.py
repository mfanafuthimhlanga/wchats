"""Tenant DB v25 migration — messages.seq, the ordering tiebreaker (issue #79).

Revision ID: 0025
Revises: 0024

Context:
    `messages` was created in 0001 with `created_at TIMESTAMPTZ DEFAULT now()`
    and never altered. `_persist_messages` inserts a turn's user row and its
    assistant row in ONE transaction, and Postgres `now()` is
    `transaction_timestamp()`, so both rows carry byte-identical timestamps.
    `id` is `gen_random_uuid()`, which sorts arbitrarily. Four readers ordered
    by `created_at` alone and relied on user-before-assistant inside a turn:

        actor_seam._fetch_history                     the Actor gate's transcript
        bench_service._fetch_customer_turn            the question a graded answer came from
        scenario_service._fetch_messages_for_conversation   the mined transcript
        retrieval_eval._fetch_last_user_message       the question Ragas scores against

    A tie has no order, so each of those was reading whatever the plan produced.
    `seq` is the tiebreaker, and it is monotonic in INSERT order rather than in
    clock order, which is the property the four readers were assuming of
    `created_at` all along.

WHY A SEQUENCE AND NOT `created_at, id`
    `id` is a v4 uuid. Sorting by it is deterministic and MEANINGLESS: it would
    give a stable wrong answer instead of an unstable one, which is worse,
    because a stable wrong answer never looks like a bug.

WHY THE BACKFILL ORDERS BY ROLE
    Existing rows cannot be recovered exactly — the information the tie destroyed
    is gone. Within one `created_at` the backfill puts the user row before the
    assistant row, which is the order `_persist_messages` writes and the order
    every reader assumed. It reconstructs the intended history rather than
    freezing a plan's arbitrary answer into a column that now looks
    authoritative. `id` is the last term so the backfill is repeatable.

WHY NOT NULL WITH A DEFAULT, unlike 0017's strictly-nullable column
    0017 kept NULL and `[]` apart because they were different OBSERVATIONS.
    Here a NULL `seq` is not an observation, it is a row the ordering cannot
    place, and a reader ordering by a nullable column puts those rows at one end
    with no ordering among them — the defect again, wearing a new column. Every
    row gets one, and the DEFAULT means every future row does too without any
    writer naming the column.

THE UNIQUE INDEX IS THE READ PATH AND THE GUARD IN ONE OBJECT
    Every one of the four readers filters on `conversation_id` and orders by
    `seq`, so `(conversation_id, seq)` is the index they want. UNIQUE because a
    duplicate `seq` inside a conversation is the original defect returning.

APPLIED AND VERIFIED 2026-09-04 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`). The observed
round trip is recorded in tests/unit/test_migration_tenant_0025.py.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Every statement is guarded, so a re-run is a no-op and a half-applied
    # attempt can be resumed rather than unpicked by hand.
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS seq BIGINT")
    op.execute("CREATE SEQUENCE IF NOT EXISTS messages_seq_seq OWNED BY messages.seq")

    # The backfill. `WHERE seq IS NULL` so a resumed run does not renumber rows
    # an earlier attempt already placed.
    op.execute("""
        WITH ordered AS (
            SELECT id,
                   row_number() OVER (
                       ORDER BY created_at,
                                CASE role WHEN 'user' THEN 0 WHEN 'assistant' THEN 1 ELSE 2 END,
                                id
                   ) AS position
            FROM messages
            WHERE seq IS NULL
        )
        UPDATE messages
        SET seq = ordered.position
        FROM ordered
        WHERE messages.id = ordered.id
    """)

    # The sequence starts after the highest backfilled value. is_called=false so
    # the next nextval RETURNS this number rather than the one after it.
    op.execute("""
        SELECT setval(
            'messages_seq_seq',
            COALESCE((SELECT MAX(seq) FROM messages), 0) + 1,
            false
        )
    """)
    op.execute("ALTER TABLE messages ALTER COLUMN seq SET DEFAULT nextval('messages_seq_seq')")
    op.execute("ALTER TABLE messages ALTER COLUMN seq SET NOT NULL")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS messages_conversation_seq_idx
        ON messages (conversation_id, seq)
    """)
    op.execute("""
        COMMENT ON COLUMN messages.seq IS
        'Issue #79. Monotonic in INSERT order, which created_at is not: a turn writes its user and assistant rows in one transaction and transaction_timestamp() gives both the same created_at. Every reader of this table orders by seq.'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS messages_conversation_seq_idx")
    # The sequence is OWNED BY the column, so the column drop takes it too. The
    # explicit drop after it is the belt for a database where the ownership was
    # lost, and it is a no-op everywhere else.
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS seq")
    op.execute("DROP SEQUENCE IF EXISTS messages_seq_seq")
