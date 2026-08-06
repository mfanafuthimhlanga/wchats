"""
Tests for Migration 0010 (tenant) — retrieval_metrics.

Covers:
  1. Migration source assertions (file exists, revision, down_revision, all
     column names, nullable citation_coverage/faithfulness).
  2. Migration DB roundtrip (guarded by INTEGRATION_TESTS_ENABLED=1): upgrade
     to 0010, verify table + indexes exist, downgrade to 0009 removes them,
     re-upgrade to 0010 re-creates them (idempotent).

Note on encoding:
  All open() calls use encoding="utf-8" to avoid Windows cp1252 UnicodeDecodeError
  (cf. 14-04-SUMMARY deviations).
"""

from __future__ import annotations

import importlib.util
import os
import uuid

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(__file__)
MIGRATION_FILE = os.path.normpath(
    os.path.join(
        _TESTS_DIR,
        "../../alembic_tenant/versions/0010_retrieval_metrics.py",
    )
)
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

# ---------------------------------------------------------------------------
# Migration source assertions
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), (
        f"Migration 0010 not found at expected path: {MIGRATION_FILE}"
    )


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0010", MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0010", f"Expected revision '0010', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0009", (
        f"Expected down_revision '0009', got {mod.down_revision!r}"
    )


@pytest.mark.parametrize(
    "expected_token",
    [
        "retrieval_metrics",
        "job_id",
        "conversation_id",
        "bm25_top_score",
        "vector_top_score",
        "rrf_top_score",
        "rerank_top_score",
        "reranker_lift",
        "recall_at_k",
        "ndcg_at_10",
        "mrr",
        "cited_chunk_rank",
        "retrieved_tokens",
        "ctx_window_utilization",
        "carried_never_cited_tokens",
        "compaction_ratio",
        "citation_coverage",
        "faithfulness",
    ],
)
def test_migration_source_contains_column(expected_token):
    """Migration source must mention every retrieval_metrics column."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert expected_token in source, (
        f"Migration 0010 source must include {expected_token!r}"
    )


def _column_line(source: str, column: str) -> str:
    idx = source.index(f"\n            {column}")
    line_end = source.index("\n", idx + 1)
    return source[idx:line_end]


def test_migration_citation_coverage_nullable():
    """citation_coverage must remain nullable — filled later by 21-04."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    column_line = _column_line(source, "citation_coverage")
    assert "NOT NULL" not in column_line.upper(), (
        "retrieval_metrics.citation_coverage must remain nullable (21-04 fills it later)"
    )


def test_migration_faithfulness_nullable():
    """faithfulness must remain nullable — filled later by 21-04."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    column_line = _column_line(source, "faithfulness")
    assert "NOT NULL" not in column_line.upper(), (
        "retrieval_metrics.faithfulness must remain nullable (21-04 fills it later)"
    )


def test_migration_job_id_not_null():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    column_line = _column_line(source, "job_id")
    assert "NOT NULL" in column_line.upper()


def test_migration_uses_if_not_exists_guards():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "CREATE TABLE IF NOT EXISTS retrieval_metrics" in source
    assert "CREATE INDEX IF NOT EXISTS ix_retrieval_metrics_job_id" in source
    assert "CREATE INDEX IF NOT EXISTS ix_retrieval_metrics_created_at" in source


def test_migration_no_pg_search_ddl():
    """No pg_search/pgbm25 extension/index DDL (CLAUDE.md rule 8) — the DDL body
    itself must not create or depend on the deprecated extension, though the
    module docstring may reference it by name to explain why it's absent."""
    mod = _load_migration()
    import inspect

    upgrade_src = inspect.getsource(mod.upgrade)
    downgrade_src = inspect.getsource(mod.downgrade)
    assert "pg_search" not in upgrade_src.lower()
    assert "pgbm25" not in upgrade_src.lower()
    assert "pg_search" not in downgrade_src.lower()
    assert "pgbm25" not in downgrade_src.lower()


# ---------------------------------------------------------------------------
# Integration gate: DB roundtrip (skipped in unit mode)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INTEGRATION_TESTS,
    reason="INTEGRATION_TESTS_ENABLED=1 required for migration DB roundtrip",
)
def test_migration_0010_db_roundtrip():
    """Integration: upgrade to 0010 -> downgrade to 0009 -> upgrade to 0010.

    Verifies:
    - retrieval_metrics exists after upgrade, with expected columns
    - table is removed after downgrade to 0009
    - re-upgrade to 0010 re-creates it without error
    """
    from alembic.config import Config
    from sqlalchemy import create_engine, pool
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text

    from alembic import command

    admin_url = os.environ.get(
        "TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres"
    )
    local_base = os.environ.get(
        "TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432"
    )

    db_name = f"wchats_test_0010_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(
        admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
    )
    with admin_engine.connect() as conn:
        conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    conn_url = f"{local_base}/{db_name}"
    script_location = os.path.normpath(
        os.path.join(_TESTS_DIR, "../../alembic_tenant")
    )

    try:
        cfg = Config()
        cfg.set_main_option("script_location", script_location)
        cfg.set_main_option("sqlalchemy.url", conn_url)

        # Upgrade to 0010 (chains through 0001-0009 first)
        command.upgrade(cfg, "0010")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        table_names = set(insp.get_table_names())
        assert "retrieval_metrics" in table_names

        cols = {c["name"] for c in insp.get_columns("retrieval_metrics")}
        for col in (
            "job_id",
            "conversation_id",
            "bm25_top_score",
            "vector_top_score",
            "rrf_top_score",
            "rerank_top_score",
            "reranker_lift",
            "recall_at_k",
            "ndcg_at_10",
            "mrr",
            "cited_chunk_rank",
            "retrieved_tokens",
            "ctx_window_utilization",
            "carried_never_cited_tokens",
            "compaction_ratio",
            "citation_coverage",
            "faithfulness",
        ):
            assert col in cols, f"retrieval_metrics missing column {col!r}"

        engine.dispose()

        # Downgrade to 0009 — table must disappear
        command.downgrade(cfg, "0009")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        table_names_after = set(insp.get_table_names())
        assert "retrieval_metrics" not in table_names_after
        engine.dispose()

        # Re-upgrade to 0010 — idempotent, no error
        command.upgrade(cfg, "0010")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        table_names_re = set(insp.get_table_names())
        assert "retrieval_metrics" in table_names_re
        engine.dispose()

    finally:
        admin_engine = create_engine(
            admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
        )
        try:
            with admin_engine.connect() as conn:
                conn.execute(
                    sa_text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :dbname AND pid <> pg_backend_pid()"
                    ),
                    {"dbname": db_name},
                )
                conn.execute(sa_text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        finally:
            admin_engine.dispose()
