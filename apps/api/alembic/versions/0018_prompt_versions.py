"""OPS-16: prompt_versions — non-destructive, canary-able soul editing (control DB).

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-16

Context:
    21-RESEARCH.md A1: agents.soul_role/soul_voice/soul_do_list/soul_donot_list are
    CONTROL-DB columns (app/models/agent.py) — prompt_versions belongs in the same
    database, not the tenant DB, so no cross-DB join is ever required to read a
    version alongside its owning agent.

    NUMBERING NOTE: this plan's PLAN.md text originally called this migration
    "0017". Plan 21-04 (this phase, earlier wave) already claimed control revision
    0017 (0017_alerts_index_staleness_type.py, down_revision 0016) before this plan
    executed. This migration is therefore 0018, down_revision "0017", to avoid
    forking the control alembic chain — confirmed via `ls alembic/versions/` before
    writing this file (head was 0017, not 0016, at execution time).

    prompt_versions is an immutable, append-only ledger: every soul edit
    (patch_agent) INSERTs a new row here — no UPDATE ever touches soul_role/
    soul_voice/soul_do_list/soul_donot_list on an existing row (must_haves
    prohibition: "history is never overwritten"). The mutable parts are the
    `label` (production/canary/draft/archived) and `canary_percent` columns —
    moving a label is how deploy/rollback/canary work (DOMAIN-NOTES §2:
    "prompt -> version -> label ... Deploy/rollback by moving a label, no code
    change").

    Canary routing (resolve_prompt_version, prompt_version_service.py) must never
    select a 'draft' row — the label CHECK constraint plus the service-layer
    `WHERE label IN ('production', 'canary')` filter are the two mitigations for
    T-21-09-01 (a draft/unapproved persona being served to production traffic).

    Mirrors the raw-SQL + IF NOT EXISTS convention already established for control
    DB tables (0011_checklist_runs_is_deployed.py) — the ORM model
    (app/models/prompt_version.py) is added purely for typed reads/writes in
    prompt_version_service.py, mirroring app/models/checklist_run.py.

    Schema:
        id                 UUID PK DEFAULT gen_random_uuid()
        agent_id           UUID NOT NULL           — owning agent (control DB agents.id)
        version_number     INT NOT NULL            — monotonically increasing per agent_id
        soul_role          TEXT
        soul_voice         TEXT
        soul_do_list       JSONB NOT NULL DEFAULT '[]'::jsonb
        soul_donot_list    JSONB NOT NULL DEFAULT '[]'::jsonb
        label              TEXT NOT NULL DEFAULT 'draft'
                             CHECK (label IN ('production','canary','draft','archived'))
        canary_percent     INT NOT NULL DEFAULT 0
                             CHECK (canary_percent BETWEEN 0 AND 100)
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()

    Indexes:
        prompt_versions_agent_id_idx           ON prompt_versions (agent_id)
        prompt_versions_agent_id_label_idx     ON prompt_versions (agent_id, label)
                                                  — resolve_prompt_version's hot-path
                                                    filter at turn dispatch
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id           UUID NOT NULL,
            version_number     INT NOT NULL,
            soul_role          TEXT,
            soul_voice         TEXT,
            soul_do_list       JSONB NOT NULL DEFAULT '[]'::jsonb,
            soul_donot_list    JSONB NOT NULL DEFAULT '[]'::jsonb,
            label              TEXT NOT NULL DEFAULT 'draft',
            canary_percent     INT NOT NULL DEFAULT 0,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_prompt_versions_label
                CHECK (label IN ('production', 'canary', 'draft', 'archived')),
            CONSTRAINT ck_prompt_versions_canary_percent
                CHECK (canary_percent BETWEEN 0 AND 100)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS prompt_versions_agent_id_idx "
        "ON prompt_versions (agent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS prompt_versions_agent_id_label_idx "
        "ON prompt_versions (agent_id, label)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS prompt_versions_agent_id_label_idx")
    op.execute("DROP INDEX IF EXISTS prompt_versions_agent_id_idx")
    op.execute("DROP TABLE IF EXISTS prompt_versions")
