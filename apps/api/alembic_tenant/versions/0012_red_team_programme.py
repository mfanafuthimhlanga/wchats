"""Tenant DB v12 migration — red-team programme (OPS-13).

Revision ID: 0012
Revises: 0011

Context:
    OPS-13 makes the red-team adversary a programme, not a per-run JSONB
    blob. Per 21-DOMAIN-NOTES.md §4 (promptfoo naming: plugins = harm
    class, strategies = attack technique), the natural object is the
    harm-category x attack-strategy coverage matrix — ASR per cell. This
    migration creates three first-class tables:

    red_team_strategies — one row per distinct attack_vector (e.g.
        prompt_injection, data_leakage, hallucination — see
        red_team_service.py:RedTeamFinding.attack_vector). Each agent has
        its own dedicated Neon DB (agent.neon_connection_string), so no
        agent_id column is needed — the connection itself is already
        agent-scoped (mirrors red_team_runs, which also has no agent_id).
        UNIQUE(attack_vector) makes the run_red_team upsert idempotent via
        ON CONFLICT DO NOTHING.

    red_team_probes — one row per probe_message run against the agent,
        linked to its strategy via strategy_id.

    red_team_findings — created here but populated + gate-rewired in 21-08
        (which also switches deployment_service._fetch_red_team_summary_sync
        to read from this table instead of the findings JSONB column on
        red_team_runs — NOT done in this migration/plan). severity/status
        CHECK constraints mirror RedTeamFinding (severity) and the
        eval_scenarios-style lifecycle vocabulary (status: open handled
        by 21-08, contained/closed for future remediation tracking).

    All three use CREATE TABLE IF NOT EXISTS (mirrors 0006/0011
    convention) — safe to re-run. IF NOT EXISTS indexes on
    red_team_findings(run_id) and red_team_findings(status, severity)
    support the coverage rollup query and the future deploy-gate read.
    No SQLAlchemy ORM model — raw SQL only, consistent with the rest of
    alembic_tenant.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # red_team_strategies — one row per distinct attack_vector
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS red_team_strategies (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            attack_vector TEXT NOT NULL,
            description   TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(attack_vector)
        )
    """)

    # ------------------------------------------------------------------
    # red_team_probes — one row per probe_message, linked to its strategy
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS red_team_probes (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            strategy_id   UUID REFERENCES red_team_strategies(id) ON DELETE SET NULL,
            harm_category TEXT,
            probe_message TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ------------------------------------------------------------------
    # red_team_findings — created here, populated + gate-rewired in 21-08
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS red_team_findings (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id         UUID REFERENCES red_team_runs(id) ON DELETE CASCADE,
            strategy_id    UUID REFERENCES red_team_strategies(id) ON DELETE SET NULL,
            probe_id       UUID REFERENCES red_team_probes(id) ON DELETE SET NULL,
            severity       TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
            status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'contained', 'closed')),
            attack_vector  TEXT,
            probe_message  TEXT,
            agent_response TEXT,
            turn_count     INT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_red_team_findings_run_id
        ON red_team_findings (run_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_red_team_findings_status_severity
        ON red_team_findings (status, severity)
    """)


def downgrade() -> None:
    # Drop in reverse dependency order: findings (FKs to runs/strategies/probes)
    # -> probes (FK to strategies) -> strategies.
    op.execute("DROP INDEX IF EXISTS ix_red_team_findings_status_severity")
    op.execute("DROP INDEX IF EXISTS ix_red_team_findings_run_id")
    op.execute("DROP TABLE IF EXISTS red_team_findings")
    op.execute("DROP TABLE IF EXISTS red_team_probes")
    op.execute("DROP TABLE IF EXISTS red_team_strategies")
