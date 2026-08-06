"""Tenant DB v13 migration — the eval configuration tuple (measurement layer P1).

Revision ID: 0013
Revises: 0012

Context:
    `eval_runs` was `(id, kind, started_at, finished_at, status)` — 0001:152.
    A score written against that row has no idea what produced it, so two runs
    that differ on exactly one dimension cannot be compared and "what changed?"
    is unanswerable. The unit of evaluation is not "the agent" but a
    configuration tuple:

        (prompt_version_id, model_id, retrieval_config_hash, envelope_hash,
         corpus state, embedding provider) -> run over dataset D -> scores S

    Every one of those dimensions is already captured somewhere else in the
    system (prompt_versions in the control DB, capability_envelopes via
    canonical_envelope_hash, agents.retrieval_strategy, chunks); none of them
    was ever stamped on the run that consumed them.

    This migration adds the two columns that carry the tuple:

    prompt_version_id UUID  — nullable, no FK. Follows the precedent set by
        turn_metrics.prompt_version_id (0009:86), which is likewise a nullable
        bare UUID: prompt_versions lives in the CONTROL DB and every tenant has
        its own Neon project, so a cross-database foreign key is not
        expressible. NULL means "this agent had no production prompt version
        at run start" — a real state, not an error.

    config JSONB           — nullable. Carries the remaining dimensions as one
        document: model_id, judge_model_id, retrieval_config_hash,
        envelope_hash, corpus_chunk_count, embedding_provider, plus the
        verified_qa promotion decision in force for the run and an
        `unavailable` list naming any dimension that could not be read. A
        dimension that could not be read is recorded as null AND named in
        `unavailable` — "we did not look" and "we looked and there is nothing"
        are different claims and the reader must be able to tell them apart.

Additive, nullable, rollback-safe (P1 risk register):
    This is the first tenant migration since 0012 and it cannot be verified
    against a live database on the development machine (no local PostgreSQL).
    It is therefore strictly additive and strictly nullable — ADD COLUMN IF NOT
    EXISTS only, no CHECK, no NOT NULL, no backfill, no index, no constraint
    touched. Existing rows keep working untouched.

    The downgrade drops both columns, and the application tolerates their
    absence: eval_service.insert_eval_run() catches psycopg2 UndefinedColumn
    on the wide INSERT and retries the pre-0013 narrow INSERT, reporting
    config_recorded=False. That fallback is not decorative — tenant DBs are
    migrated with `alembic upgrade head` at provision time only, so every
    tenant provisioned before this revision runs without these columns until
    it is re-migrated. Rolling back is therefore a no-op for the application:
    runs keep completing, they simply stop carrying attribution.

    Follows the established raw-SQL convention (mirrors 0009/0010/0011/0012) —
    no SQLAlchemy ORM model, consistent with every other tenant-DB table.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # eval_runs.prompt_version_id — nullable bare UUID, no FK (the
    # referenced prompt_versions table lives in the control DB, mirroring
    # turn_metrics.prompt_version_id at 0009:86).
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS prompt_version_id UUID
    """)

    # ------------------------------------------------------------------
    # eval_runs.config — nullable JSONB carrying the rest of the tuple.
    # No DEFAULT: an absent config must read as "this run predates
    # attribution", never as an empty-but-present configuration.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS config JSONB
    """)


def downgrade() -> None:
    # Reverse order of the adds. Both are IF EXISTS so a downgrade against a
    # database that never received 0013 is a no-op rather than an error.
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS config")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS prompt_version_id")
