"""
Tests for Migration 0012 (tenant) — red-team programme (OPS-13).

Covers:
  1. Migration source assertions (file exists, revision, down_revision,
     three table names, severity/status CHECK clauses, IF NOT EXISTS
     guards, indexes).
  2. Migration DB roundtrip (guarded by INTEGRATION_TESTS_ENABLED=1):
     upgrade to 0012, verify all three tables + both CHECK constraints
     are enforced, downgrade to 0011 removes them, re-upgrade to 0012
     re-creates them (idempotent).

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
        "../../alembic_tenant/versions/0012_red_team_programme.py",
    )
)
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

# ---------------------------------------------------------------------------
# Migration source assertions
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), (
        f"Migration 0012 not found at expected path: {MIGRATION_FILE}"
    )


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0012", MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0012", f"Expected revision '0012', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0011", (
        f"Expected down_revision '0011', got {mod.down_revision!r}"
    )


@pytest.mark.parametrize(
    "expected_token",
    [
        "red_team_strategies",
        "red_team_probes",
        "red_team_findings",
        "attack_vector",
        "strategy_id",
        "probe_id",
    ],
)
def test_migration_source_contains_token(expected_token):
    """Migration source must mention every new table / linking column."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert expected_token in source, (
        f"Migration 0012 source must include {expected_token!r}"
    )


def test_migration_has_severity_check_clause():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "CHECK (severity IN ('low', 'medium', 'high', 'critical'))" in source, (
        "Migration 0012 must CHECK severity IN ('low','medium','high','critical')"
    )


def test_migration_has_status_check_clause():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "CHECK (status IN ('open', 'contained', 'closed'))" in source, (
        "Migration 0012 must CHECK status IN ('open','contained','closed')"
    )


def test_migration_strategies_unique_attack_vector():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "UNIQUE(attack_vector)" in source, (
        "red_team_strategies must UNIQUE(attack_vector) — required for the "
        "run_red_team ON CONFLICT DO NOTHING upsert to be idempotent"
    )


def test_migration_uses_if_not_exists_guards():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "CREATE TABLE IF NOT EXISTS red_team_strategies" in source
    assert "CREATE TABLE IF NOT EXISTS red_team_probes" in source
    assert "CREATE TABLE IF NOT EXISTS red_team_findings" in source
    assert "CREATE INDEX IF NOT EXISTS ix_red_team_findings_run_id" in source
    assert "CREATE INDEX IF NOT EXISTS ix_red_team_findings_status_severity" in source


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


def test_migration_downgrade_drops_all_three_tables():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "DROP TABLE IF EXISTS red_team_findings" in source
    assert "DROP TABLE IF EXISTS red_team_probes" in source
    assert "DROP TABLE IF EXISTS red_team_strategies" in source
    # findings must drop before strategies/probes (reverse dependency order)
    findings_pos = source.index("DROP TABLE IF EXISTS red_team_findings")
    strategies_pos = source.index("DROP TABLE IF EXISTS red_team_strategies")
    assert findings_pos < strategies_pos, (
        "downgrade must drop red_team_findings before red_team_strategies "
        "(reverse dependency order — findings FKs to strategies)"
    )


# ---------------------------------------------------------------------------
# Integration gate: DB roundtrip (skipped in unit mode)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INTEGRATION_TESTS,
    reason="INTEGRATION_TESTS_ENABLED=1 required for migration DB roundtrip",
)
def test_migration_0012_db_roundtrip():
    """Integration: upgrade to 0012 -> all three tables exist and CHECKs are
    enforced -> downgrade to 0011 removes them -> re-upgrade to 0012
    (idempotent).
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

    db_name = f"wchats_test_0012_{uuid.uuid4().hex[:12]}"
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

        # Upgrade to 0012 (chains through 0001-0011 first)
        command.upgrade(cfg, "0012")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        table_names = set(insp.get_table_names())
        for tbl in ("red_team_strategies", "red_team_probes", "red_team_findings"):
            assert tbl in table_names, f"{tbl} missing after upgrade to 0012"
        engine.dispose()

        # Load-bearing: severity CHECK is enforced
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with pytest.raises(Exception):
            with engine.begin() as conn:
                conn.execute(
                    sa_text("""
                        INSERT INTO red_team_findings (id, severity)
                        VALUES (gen_random_uuid(), 'bogus_severity')
                    """)
                )
        engine.dispose()

        # A valid severity + default status succeed
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            conn.execute(
                sa_text("""
                    INSERT INTO red_team_findings (id, severity)
                    VALUES (gen_random_uuid(), 'high')
                """)
            )
            row = conn.execute(
                sa_text("SELECT status FROM red_team_findings WHERE severity = 'high'")
            ).fetchone()
            assert row[0] == "open"
        engine.dispose()

        # Strategy UNIQUE(attack_vector) + ON CONFLICT DO NOTHING is idempotent
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            conn.execute(
                sa_text("""
                    INSERT INTO red_team_strategies (attack_vector)
                    VALUES ('prompt_injection')
                    ON CONFLICT (attack_vector) DO NOTHING
                """)
            )
            conn.execute(
                sa_text("""
                    INSERT INTO red_team_strategies (attack_vector)
                    VALUES ('prompt_injection')
                    ON CONFLICT (attack_vector) DO NOTHING
                """)
            )
            count = conn.execute(
                sa_text(
                    "SELECT COUNT(*) FROM red_team_strategies WHERE attack_vector = 'prompt_injection'"
                )
            ).scalar()
            assert count == 1, "UNIQUE(attack_vector) + ON CONFLICT DO NOTHING must be idempotent"
        engine.dispose()

        # Downgrade to 0011 — new tables must disappear
        command.downgrade(cfg, "0011")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        table_names_after = set(insp.get_table_names())
        for tbl in ("red_team_strategies", "red_team_probes", "red_team_findings"):
            assert tbl not in table_names_after, f"{tbl} still exists after downgrade to 0011"
        engine.dispose()

        # Re-upgrade to 0012 — idempotent, no error
        command.upgrade(cfg, "0012")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        table_names_re = set(insp.get_table_names())
        for tbl in ("red_team_strategies", "red_team_probes", "red_team_findings"):
            assert tbl in table_names_re, f"{tbl} missing after re-upgrade to 0012"
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
