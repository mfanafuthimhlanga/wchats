"""Control DB initial schema — tenants, agents, jobs, job_events.

Revision ID: 0001
Revises: None
Create Date: 2026-05-12

Schema source: prd-M1.md §5

Key design notes:
- gen_random_uuid() is a Postgres 13+ built-in (no pgcrypto extension needed)
- agents.neon_connection_string BYTEA: Fernet-encrypted pooled URI (application traffic)
- agents.neon_direct_connection_string BYTEA: Fernet-encrypted direct URI (Alembic only)
- job_events uses BIGSERIAL (autoincrement BIGINT) for high-cardinality event logging
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # tenants
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE tenants (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name        TEXT NOT NULL,
            api_key     TEXT NOT NULL UNIQUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at  TIMESTAMPTZ
        )
    """)

    # ------------------------------------------------------------------
    # agents
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE agents (
            id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id                     UUID NOT NULL REFERENCES tenants(id),
            name                          TEXT NOT NULL,
            soul                          JSONB NOT NULL,
            role                          TEXT NOT NULL,
            neon_project_id               TEXT,
            neon_connection_string        BYTEA,
            neon_direct_connection_string BYTEA,
            schema_version                TEXT,
            status                        TEXT NOT NULL DEFAULT 'pending',
            created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at                    TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX agents_tenant_id_idx ON agents(tenant_id)")

    # ------------------------------------------------------------------
    # jobs
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE jobs (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   UUID NOT NULL REFERENCES tenants(id),
            agent_id    UUID REFERENCES agents(id),
            kind        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            error       TEXT,
            started_at  TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX jobs_agent_id_idx ON jobs(agent_id)")

    # ------------------------------------------------------------------
    # job_events
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE job_events (
            id          BIGSERIAL PRIMARY KEY,
            job_id      UUID NOT NULL REFERENCES jobs(id),
            event_type  TEXT NOT NULL,
            payload     JSONB,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX job_events_job_id_created_at_idx ON job_events(job_id, created_at)"
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP TABLE IF EXISTS job_events")
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP TABLE IF EXISTS agents")
    op.execute("DROP TABLE IF EXISTS tenants")
