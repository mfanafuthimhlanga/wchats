"""transactional substrate: capability_envelopes, tool_calls_audit, pending_confirmations, tool_idempotency_keys

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-29

CAP-01 / capability_envelopes
  Per-skill authorization envelope for each agent.
  enabled server_default=false enforces fail-closed at the schema level (T-14-01-01).
  UNIQUE(agent_id, skill) named uq_capability_envelopes_agent_skill.

AUD-01 / tool_calls_audit
  Immutable audit log for every tool call (success and error paths).
  actor_decision/actor_rationale default to empty string; Phase 15 writes them.

AUD-02 / pending_confirmations
  Human-confirmation workflow: an agent writes a row here when requires_confirmation=true.

TXN-02 / tool_idempotency_keys
  Durable idempotency guard for mutating tool calls.
  Survives Redis restarts. UNIQUE(agent_id, skill, idempotency_key) prevents
  double-execution under Celery acks_late=True (T-14-01-02).

All four tables live on the CONTROL DB (not per-tenant Neon).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # CAP-01: capability_envelopes
    # enabled DEFAULT false = fail-closed (T-14-01-01)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS capability_envelopes (
            id                              UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            agent_id                        UUID NOT NULL,
            skill                           TEXT NOT NULL,
            enabled                         BOOLEAN NOT NULL DEFAULT false,
            rate_limit                      TEXT NULL,
            constraints                     JSONB NOT NULL DEFAULT '{}'::jsonb,
            requires_confirmation           BOOLEAN NOT NULL DEFAULT false,
            requires_identity_verification  BOOLEAN NOT NULL DEFAULT false,
            updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_capability_envelopes_agent_skill UNIQUE (agent_id, skill)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS capability_envelopes_agent_id_idx
        ON capability_envelopes (agent_id)
    """)

    # ------------------------------------------------------------------
    # AUD-01: tool_calls_audit
    # actor_decision/actor_rationale: NOT NULL DEFAULT '' (Phase 15 fills)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_calls_audit (
            id                   UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            agent_id             UUID NOT NULL,
            conversation_id      UUID NULL,
            skill                TEXT NOT NULL,
            arguments            JSONB NULL,
            result               JSONB NULL,
            actor_decision       TEXT NOT NULL DEFAULT '',
            actor_rationale      TEXT NOT NULL DEFAULT '',
            capability_snapshot  JSONB NULL,
            latency_ms           INTEGER NULL,
            error                TEXT NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS tool_calls_audit_agent_skill_idx
        ON tool_calls_audit (agent_id, skill)
    """)

    # ------------------------------------------------------------------
    # AUD-02: pending_confirmations
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS pending_confirmations (
            id            UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            agent_id      UUID NOT NULL,
            skill         TEXT NOT NULL,
            arguments     JSONB NULL,
            requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at    TIMESTAMPTZ NULL,
            resolved_at   TIMESTAMPTZ NULL,
            resolution    TEXT NULL
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS pending_confirmations_agent_id_idx
        ON pending_confirmations (agent_id)
    """)

    # ------------------------------------------------------------------
    # TXN-02: tool_idempotency_keys
    # UNIQUE(agent_id, skill, idempotency_key) — durable double-execute guard
    # (T-14-01-02: survives Redis restarts; correct under acks_late=True)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_idempotency_keys (
            id              UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            agent_id        UUID NOT NULL,
            skill           TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            result          JSONB NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_tool_idempotency_keys UNIQUE (agent_id, skill, idempotency_key)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS tool_idempotency_keys_agent_skill_idx
        ON tool_idempotency_keys (agent_id, skill)
    """)


def downgrade() -> None:
    # Drop in reverse dependency order (no FK cross-references between these tables)
    op.execute("DROP TABLE IF EXISTS tool_idempotency_keys")
    op.execute("DROP TABLE IF EXISTS pending_confirmations")
    op.execute("DROP TABLE IF EXISTS tool_calls_audit")
    op.execute("DROP TABLE IF EXISTS capability_envelopes")
