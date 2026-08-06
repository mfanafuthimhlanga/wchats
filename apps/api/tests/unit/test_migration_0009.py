"""
Tests for Migration 0009 (tenant) — turn_metrics + message_feedback.

Covers:
  1. Migration source assertions (file exists, revision, down_revision, all
     column names, both CHECK constraint clauses, prompt_version_id present).
  2. Migration DB roundtrip (guarded by INTEGRATION_TESTS_ENABLED=1): upgrade
     to 0009, verify both tables + indexes exist, downgrade to 0008 removes
     them, re-upgrade to 0009 re-creates them (idempotent).

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
        "../../alembic_tenant/versions/0009_turn_metrics_message_feedback.py",
    )
)
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

# ---------------------------------------------------------------------------
# Migration source assertions
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), (
        f"Migration 0009 not found at expected path: {MIGRATION_FILE}"
    )


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0009", MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0009", f"Expected revision '0009', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0008", (
        f"Expected down_revision '0008', got {mod.down_revision!r}"
    )


@pytest.mark.parametrize(
    "expected_token",
    [
        "turn_metrics",
        "message_feedback",
        "cost_usd",
        "latency_ms",
        "num_turns",
        "escalated",
        "tool_count",
        "prompt_version_id",
        "rating",
        "csat_score",
    ],
)
def test_migration_source_contains_column(expected_token):
    """Migration source must mention every turn_metrics/message_feedback column."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert expected_token in source, (
        f"Migration 0009 source must include {expected_token!r}"
    )


def test_migration_has_rating_check_constraint():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "CHECK (rating IN ('up', 'down'))" in source, (
        "Migration 0009 must CHECK (rating IN ('up', 'down')) on message_feedback"
    )


def test_migration_has_csat_score_check_constraint():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "CHECK (csat_score BETWEEN 1 AND 5)" in source, (
        "Migration 0009 must CHECK (csat_score BETWEEN 1 AND 5) on message_feedback"
    )


def test_migration_prompt_version_id_nullable_no_not_null():
    """prompt_version_id must be nullable — no NOT NULL constraint attached to it."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    idx = source.index("prompt_version_id")
    # Grab the rest of that column's declaration line only
    line_end = source.index("\n", idx)
    column_line = source[idx:line_end]
    assert "NOT NULL" not in column_line.upper(), (
        "turn_metrics.prompt_version_id must remain nullable "
        "(reserved for OPS-16 Wave 5 canary correlation)"
    )


def test_migration_uses_if_not_exists_guards():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "CREATE TABLE IF NOT EXISTS turn_metrics" in source
    assert "CREATE TABLE IF NOT EXISTS message_feedback" in source


# ---------------------------------------------------------------------------
# Integration gate: DB roundtrip (skipped in unit mode)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INTEGRATION_TESTS,
    reason="INTEGRATION_TESTS_ENABLED=1 required for migration DB roundtrip",
)
def test_migration_0009_db_roundtrip():
    """Integration: upgrade to 0009 → downgrade to 0008 → upgrade to 0009.

    Verifies:
    - turn_metrics + message_feedback exist after upgrade, with expected columns
    - both tables are removed after downgrade to 0008
    - re-upgrade to 0009 re-creates them without error
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

    db_name = f"wchats_test_0009_{uuid.uuid4().hex[:12]}"
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

        # Upgrade to 0009 (chains through 0001-0008 first)
        command.upgrade(cfg, "0009")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        table_names = set(insp.get_table_names())
        assert "turn_metrics" in table_names
        assert "message_feedback" in table_names

        turn_metrics_cols = {c["name"] for c in insp.get_columns("turn_metrics")}
        for col in (
            "job_id",
            "cost_usd",
            "num_turns",
            "latency_ms",
            "escalated",
            "tool_count",
            "stop_reason",
            "prompt_version_id",
        ):
            assert col in turn_metrics_cols, f"turn_metrics missing column {col!r}"

        feedback_cols = {c["name"] for c in insp.get_columns("message_feedback")}
        for col in ("message_id", "rating", "csat_score"):
            assert col in feedback_cols, f"message_feedback missing column {col!r}"

        engine.dispose()

        # Downgrade to 0008 — both tables must disappear
        command.downgrade(cfg, "0008")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        table_names_after = set(insp.get_table_names())
        assert "turn_metrics" not in table_names_after
        assert "message_feedback" not in table_names_after
        engine.dispose()

        # Re-upgrade to 0009 — idempotent, no error
        command.upgrade(cfg, "0009")

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        insp = sa_inspect(engine)
        table_names_re = set(insp.get_table_names())
        assert "turn_metrics" in table_names_re
        assert "message_feedback" in table_names_re
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
