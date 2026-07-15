"""Tenant DB v9 migration — turn_metrics + message_feedback tables.

Revision ID: 0009
Revises: 0008

Context:
    OPS-01: adds `turn_metrics` — one row per completed agent turn, capturing the
    SDK `ResultMessage` fields (cost_usd, num_turns, latency_ms, stop_reason) plus
    escalated/tool_count, written by `run_agent_turn` after the terminal
    `agent.response` SSE emit. This table is the source of every containment/
    escalation/latency/cost KPI surfaced by `GET /agents/{id}/metrics` (OPS-03).

    OPS-02: adds `message_feedback` — one row per widget thumbs-up/down (+ optional
    1-5 CSAT score) submitted against a specific assistant message.

    prompt_version_id (RESEARCH.md Open Question 2 — resolved): added nullable on
    `turn_metrics` in THIS migration (Wave 1) rather than via a later Wave-4 ALTER
    TABLE, to avoid a cross-wave migration dependency. It is unused until OPS-16
    (Wave 5 canary analysis) — costs nothing today, and an IF NOT EXISTS-guarded
    ALTER TABLE would have been trivially safe either way, but adding it upfront
    avoids touching this table again later.

    Both tables follow the established raw-SQL + IF NOT EXISTS convention (mirrors
    0008_customer_identities.py) — no SQLAlchemy ORM model, consistent with every
    other tenant-DB table (eval_scenarios, verified_qa, red_team_runs, conversations).

    Schema — turn_metrics:
        id                 UUID PK DEFAULT gen_random_uuid()
        job_id             TEXT NOT NULL           — correlates to control-DB job_events
                                                       and the Langfuse trace (OPS-04)
        conversation_id    UUID                    — tenant conversations.id
        agent_id           UUID
        cost_usd           NUMERIC                 — ResultMessage.total_cost_usd
        num_turns          INT                     — ResultMessage.num_turns
        latency_ms         INT                     — wall-clock around the SDK turn
        escalated          BOOLEAN NOT NULL DEFAULT false
        tool_count         INT                     — len(tool_calls_log)
        stop_reason        TEXT                    — ResultMessage.stop_reason
        prompt_version_id  UUID                    — nullable; reserved for OPS-16
                                                       canary correlation, unused until
                                                       Wave 5 (RESEARCH.md Q2)
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()

    Schema — message_feedback:
        id                 UUID PK DEFAULT gen_random_uuid()
        message_id         UUID
        conversation_id    UUID
        rating             TEXT NOT NULL CHECK (rating IN ('up','down'))
        csat_score         INT CHECK (csat_score BETWEEN 1 AND 5)
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()

    Indexes:
        ix_turn_metrics_job_id           ON turn_metrics (job_id)
        ix_turn_metrics_created_at       ON turn_metrics (created_at)
        ix_message_feedback_message_id   ON message_feedback (message_id)

    All DDL statements use IF NOT EXISTS guards so the migration is safe to re-run
    on tenant DBs that may have been manually altered (established convention, see
    0008's T-17-03 note).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS turn_metrics (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id              TEXT NOT NULL,
            conversation_id     UUID,
            agent_id            UUID,
            cost_usd            NUMERIC,
            num_turns           INT,
            latency_ms          INT,
            escalated           BOOLEAN NOT NULL DEFAULT false,
            tool_count          INT,
            stop_reason         TEXT,
            prompt_version_id   UUID,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_turn_metrics_job_id
        ON turn_metrics (job_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_turn_metrics_created_at
        ON turn_metrics (created_at)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS message_feedback (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id          UUID,
            conversation_id     UUID,
            rating              TEXT NOT NULL,
            csat_score          INT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_message_feedback_rating CHECK (rating IN ('up', 'down')),
            CONSTRAINT ck_message_feedback_csat_score CHECK (csat_score BETWEEN 1 AND 5)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_message_feedback_message_id
        ON message_feedback (message_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_message_feedback_message_id")
    op.execute("DROP TABLE IF EXISTS message_feedback")
    op.execute("DROP INDEX IF EXISTS ix_turn_metrics_created_at")
    op.execute("DROP INDEX IF EXISTS ix_turn_metrics_job_id")
    op.execute("DROP TABLE IF EXISTS turn_metrics")
