"""
Tests for Migration 0011 (tenant) — eval_scenarios provenance (OPS-11/OPS-12).

Covers:
  1. Migration source assertions (file exists, revision, down_revision,
     widened source values, new columns, dynamic constraint-drop present).
  2. Migration DB roundtrip (guarded by INTEGRATION_TESTS_ENABLED=1): upgrade
     to 0011, verify a source='production' INSERT succeeds (proving the CHECK
     was widened, not just that the column exists), downgrade to 0010 removes
     the new columns, re-upgrade to 0011 re-creates them (idempotent).

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
        "../../alembic_tenant/versions/0011_eval_scenarios_provenance.py",
    )
)
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

# ---------------------------------------------------------------------------
# Migration source assertions
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), (
        f"Migration 0011 not found at expected path: {MIGRATION_FILE}"
    )


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0011", MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0011", f"Expected revision '0011', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0010", (
        f"Expected down_revision '0010', got {mod.down_revision!r}"
    )


@pytest.mark.parametrize(
    "expected_token",
    [
        "production",
        "red_team",
        "provenance",
        "origin_trace_id",
    ],
)
def test_migration_source_contains_token(expected_token):
    """Migration source must mention every new source value / column."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert expected_token in source, (
        f"Migration 0011 source must include {expected_token!r}"
    )


def test_migration_widens_source_check():
    """Migration must widen the CHECK to allow both new source values in one clause."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "'generated', 'mined', 'production', 'red_team'" in source, (
        "Migration 0011 must widen the source CHECK to include all four values "
        "in the same CHECK clause (Pitfall 2 — must land together with provenance)"
    )


def test_migration_drops_source_check_dynamically():
    """Migration source must dynamically discover + DROP the source CHECK constraint,
    not hardcode a specific auto-generated name (Pitfall 2 / Assumption A5)."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "DROP CONSTRAINT" in source.upper(), (
        "Migration 0011 must DROP the existing source CHECK constraint"
    )
    assert "pg_constraint" in source, (
        "Migration 0011 must discover the constraint name via pg_constraint, "
        "not hardcode 'eval_scenarios_source_check' blindly"
    )


def test_migration_provenance_and_origin_trace_id_nullable():
    """provenance and origin_trace_id must be nullable — no backfill (Runtime State Inventory)."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    for col_ddl in (
        "ADD COLUMN IF NOT EXISTS provenance TEXT",
        "ADD COLUMN IF NOT EXISTS origin_trace_id TEXT",
    ):
        assert col_ddl in source, f"Expected exact DDL fragment: {col_ddl!r}"


def test_migration_uses_if_not_exists_guards():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "ADD COLUMN IF NOT EXISTS provenance" in source
    assert "ADD COLUMN IF NOT EXISTS origin_trace_id" in source
    assert "CREATE INDEX IF NOT EXISTS ix_eval_scenarios_origin_trace_id" in source


def test_migration_no_pg_search_ddl():
    """No pg_search/pgbm25 extension/index DDL (CLAUDE.md rule 8)."""
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
def test_migration_0011_db_roundtrip():
    """Integration: upgrade to 0011 -> INSERT source='production' succeeds ->
    downgrade to 0010 removes new columns -> re-upgrade to 0011 (idempotent).

    The INSERT assertion is the load-bearing check: it proves the CHECK
    constraint was actually widened at INSERT time, not just that the
    migration ran without raising (Pitfall 2's warning sign: broad
    try/except insert helpers silently swallow CheckViolation).
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect as sa_inspect, pool, text as sa_text

    admin_url = os.environ.get(
        "TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres"
    )
    local_base = os.environ.get(
        "TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432"
    )

    db_name = f"wchats_test_0011_{uuid.uuid4().hex[:12]}"
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

        # Upgrade to 0011 (chains through 0001-0010 first)
        command.upgrade(cfg, "0011")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        cols = {c["name"] for c in insp.get_columns("eval_scenarios")}
        assert "provenance" in cols
        assert "origin_trace_id" in cols
        engine.dispose()

        # Load-bearing: a source='production' INSERT must succeed post-upgrade
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            conn.execute(
                sa_text("""
                    INSERT INTO eval_scenarios
                      (id, source, question, reference_answer, retrieved_contexts,
                       provenance, origin_trace_id)
                    VALUES
                      (gen_random_uuid(), 'production', 'q', 'a', '[]'::jsonb,
                       'trace-abc', 'trace-abc')
                """)
            )
            conn.execute(
                sa_text("""
                    INSERT INTO eval_scenarios
                      (id, source, question, reference_answer, retrieved_contexts)
                    VALUES
                      (gen_random_uuid(), 'red_team', 'q2', 'a2', '[]'::jsonb)
                """)
            )
        engine.dispose()

        # Old CHECK values must still be accepted (widened, not replaced)
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            conn.execute(
                sa_text("""
                    INSERT INTO eval_scenarios
                      (id, source, question, reference_answer, retrieved_contexts)
                    VALUES
                      (gen_random_uuid(), 'generated', 'q3', 'a3', '[]'::jsonb)
                """)
            )
        engine.dispose()

        # An invalid source value must still be rejected
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with pytest.raises(Exception):
            with engine.begin() as conn:
                conn.execute(
                    sa_text("""
                        INSERT INTO eval_scenarios
                          (id, source, question, reference_answer, retrieved_contexts)
                        VALUES
                          (gen_random_uuid(), 'bogus_source', 'q4', 'a4', '[]'::jsonb)
                    """)
                )
        engine.dispose()

        # Downgrade to 0010 — new columns must disappear
        command.downgrade(cfg, "0010")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        cols_after = {c["name"] for c in insp.get_columns("eval_scenarios")}
        assert "provenance" not in cols_after
        assert "origin_trace_id" not in cols_after
        engine.dispose()

        # Re-upgrade to 0011 — idempotent, no error
        command.upgrade(cfg, "0011")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        cols_re = {c["name"] for c in insp.get_columns("eval_scenarios")}
        assert "provenance" in cols_re
        assert "origin_trace_id" in cols_re
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
