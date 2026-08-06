"""
Tests for Migration 0013 (tenant) — the eval configuration tuple (P1).

This is the first tenant migration since 0012 and it CANNOT be verified against
a live database on this machine (no local PostgreSQL — every `-m integration`
harness skips, and a skip is unobserved, never a pass). The source assertions
below are therefore the only observed evidence that exists for it, so they are
written as constraints on what the migration is ALLOWED to contain, not merely
as checks that it contains the two ADD COLUMNs:

  - strictly additive     — no DROP/ALTER/RENAME of anything pre-existing
  - strictly nullable     — no NOT NULL, no DEFAULT, no CHECK, no backfill
  - rollback is a no-op   — downgrade only drops the two columns it added
  - the tree is not forked — 0013 is the sole child of 0012 and the sole head

The last one exists because a forked Alembic tree fails at `upgrade head` on a
live tenant DB, which is precisely the failure this machine cannot observe.

Note on encoding:
  All open() calls use encoding="utf-8" to avoid Windows cp1252
  UnicodeDecodeError (cf. 14-04-SUMMARY deviations).
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re
import uuid

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(__file__)
VERSIONS_DIR = os.path.normpath(
    os.path.join(_TESTS_DIR, "../../alembic_tenant/versions")
)
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0013_eval_run_config.py")
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0013", MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _migration_source() -> str:
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Identity and position in the tree
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), (
        f"Migration 0013 not found at expected path: {MIGRATION_FILE}"
    )


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0013", f"Expected revision '0013', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0012", (
        f"Expected down_revision '0012', got {mod.down_revision!r}"
    )


def _all_tenant_revisions() -> dict[str, str | None]:
    """revision -> down_revision for every file in alembic_tenant/versions."""
    revisions: dict[str, str | None] = {}
    for name in os.listdir(VERSIONS_DIR):
        if not name.endswith(".py") or name.startswith("__"):
            continue
        with open(os.path.join(VERSIONS_DIR, name), encoding="utf-8") as fh:
            src = fh.read()
        rev = re.search(r'^revision:\s*str\s*=\s*"([^"]+)"', src, re.M)
        down = re.search(
            r'^down_revision:[^=]*=\s*(?:"([^"]+)"|None)', src, re.M
        )
        if rev:
            revisions[rev.group(1)] = down.group(1) if down and down.group(1) else None
    return revisions


def test_tenant_migration_tree_is_not_forked():
    """Exactly one child per revision, and exactly one head.

    A fork is invisible here (no live DB to run `upgrade head` against) and
    fatal there. Two revisions sharing a down_revision, or a second head,
    means every tenant provision breaks on the next `alembic upgrade head`.

    The head is no longer asserted to BE 0013 — P2's 0014 is the head now, and
    each migration's own test owns that claim (see
    test_migration_tenant_0014.py). What this test owns is the shape of the
    whole tree, which every later revision has to keep true.
    """
    revisions = _all_tenant_revisions()
    assert "0013" in revisions, "0013 was not discovered in alembic_tenant/versions"

    parents = [down for down in revisions.values() if down is not None]
    duplicated = {p for p in parents if parents.count(p) > 1}
    assert not duplicated, (
        f"Forked tenant migration tree — revisions share a down_revision: {duplicated}"
    )

    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"Expected exactly one tenant head, got heads={sorted(heads)}"
    )


# ---------------------------------------------------------------------------
# The two columns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expected_ddl",
    [
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS prompt_version_id UUID",
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS config JSONB",
    ],
)
def test_upgrade_adds_the_two_nullable_columns(expected_ddl):
    mod = _load_migration()
    upgrade_src = inspect.getsource(mod.upgrade)
    normalised = " ".join(upgrade_src.split())
    assert expected_ddl in normalised, (
        f"Migration 0013 upgrade must contain: {expected_ddl}"
    )


def test_upgrade_touches_only_eval_runs():
    """The only table named in the upgrade is eval_runs."""
    mod = _load_migration()
    upgrade_src = inspect.getsource(mod.upgrade)
    tables = set(re.findall(r"ALTER TABLE (\w+)", upgrade_src))
    assert tables == {"eval_runs"}, (
        f"Migration 0013 upgrade must touch eval_runs only, found {sorted(tables)}"
    )


# ---------------------------------------------------------------------------
# Strictly additive / strictly nullable (the P1 risk register)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        "NOT NULL",
        "DEFAULT",
        "CHECK",
        "DROP COLUMN",
        "DROP TABLE",
        "DROP CONSTRAINT",
        "RENAME",
        "CREATE TABLE",
        "CREATE INDEX",
        "UPDATE ",
        "DELETE ",
        "REFERENCES",
    ],
)
def test_upgrade_is_strictly_additive_and_nullable(forbidden):
    """No constraint, no default, no backfill, no destructive statement.

    A nullable bare UUID with no FK is deliberate and mirrors
    turn_metrics.prompt_version_id (0009:86): prompt_versions lives in the
    CONTROL DB while every tenant has its own Neon project, so a cross-database
    foreign key is not expressible.
    """
    mod = _load_migration()
    upgrade_src = inspect.getsource(mod.upgrade)
    # Strip comment lines — the prose explains why these are absent.
    sql_only = "\n".join(
        line for line in upgrade_src.splitlines() if not line.strip().startswith("#")
    )
    assert forbidden not in sql_only.upper(), (
        f"Migration 0013 upgrade must not contain {forbidden!r} — it is required "
        "to be strictly additive and strictly nullable (cannot be verified "
        "against a live DB on this machine)"
    )


def test_downgrade_only_drops_what_upgrade_added():
    mod = _load_migration()
    downgrade_src = inspect.getsource(mod.downgrade)
    normalised = " ".join(downgrade_src.split())

    assert "ALTER TABLE eval_runs DROP COLUMN IF EXISTS config" in normalised
    assert (
        "ALTER TABLE eval_runs DROP COLUMN IF EXISTS prompt_version_id" in normalised
    )

    # Reverse order of the adds, and nothing else touched.
    config_pos = normalised.index("DROP COLUMN IF EXISTS config")
    pv_pos = normalised.index("DROP COLUMN IF EXISTS prompt_version_id")
    assert config_pos < pv_pos, "downgrade must drop in reverse order of the adds"

    tables = set(re.findall(r"ALTER TABLE (\w+)", downgrade_src))
    assert tables == {"eval_runs"}, (
        f"Migration 0013 downgrade must touch eval_runs only, found {sorted(tables)}"
    )
    assert "DROP TABLE" not in downgrade_src.upper()


def test_downgrade_is_idempotent_via_if_exists():
    """A downgrade against a DB that never received 0013 must be a no-op."""
    mod = _load_migration()
    downgrade_src = inspect.getsource(mod.downgrade)
    drops = re.findall(r"DROP COLUMN(?: IF EXISTS)?", downgrade_src)
    assert drops, "downgrade contains no DROP COLUMN at all"
    assert all(d == "DROP COLUMN IF EXISTS" for d in drops), (
        f"every downgrade DROP COLUMN must be IF EXISTS, found {drops}"
    )


def test_migration_no_pg_search_ddl():
    """No pg_search/pgbm25 extension/index DDL (CLAUDE.md rule 8)."""
    mod = _load_migration()
    combined = (
        inspect.getsource(mod.upgrade) + inspect.getsource(mod.downgrade)
    ).lower()
    assert "pg_search" not in combined
    assert "pgbm25" not in combined


# ---------------------------------------------------------------------------
# Integration gate: DB roundtrip (skipped in unit mode — UNOBSERVED, not passing)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INTEGRATION_TESTS,
    reason="INTEGRATION_TESTS_ENABLED=1 required for migration DB roundtrip",
)
def test_migration_0013_db_roundtrip():
    """Integration: upgrade to 0013 -> both columns exist and accept NULL ->
    downgrade to 0012 removes them -> re-upgrade to 0013 (idempotent).
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

    db_name = f"wchats_test_0013_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(
        admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
    )
    with admin_engine.connect() as conn:
        conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    conn_url = f"{local_base}/{db_name}"
    script_location = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic_tenant"))

    def _eval_run_columns() -> set[str]:
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        try:
            return {c["name"] for c in sa_inspect(engine).get_columns("eval_runs")}
        finally:
            engine.dispose()

    try:
        cfg = Config()
        cfg.set_main_option("script_location", script_location)
        cfg.set_main_option("sqlalchemy.url", conn_url)

        command.upgrade(cfg, "0013")
        cols = _eval_run_columns()
        assert "prompt_version_id" in cols, "prompt_version_id missing after 0013"
        assert "config" in cols, "config missing after 0013"

        # Both columns are nullable — a pre-0013-shaped INSERT still works.
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO eval_runs (id, kind, status) "
                    "VALUES (gen_random_uuid(), 'm6:test', 'running')"
                )
            )
            row = conn.execute(
                sa_text(
                    "SELECT prompt_version_id, config FROM eval_runs "
                    "WHERE kind = 'm6:test'"
                )
            ).fetchone()
            assert row[0] is None and row[1] is None, (
                "0013's columns must be nullable with no DEFAULT"
            )
        engine.dispose()

        # Downgrade removes them and leaves the pre-existing row intact.
        command.downgrade(cfg, "0012")
        cols_after = _eval_run_columns()
        assert "prompt_version_id" not in cols_after
        assert "config" not in cols_after

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            surviving = conn.execute(
                sa_text("SELECT COUNT(*) FROM eval_runs WHERE kind = 'm6:test'")
            ).scalar()
            assert surviving == 1, "rollback must not destroy existing eval_runs rows"
        engine.dispose()

        # Re-upgrade — idempotent, no error.
        command.upgrade(cfg, "0013")
        cols_re = _eval_run_columns()
        assert "prompt_version_id" in cols_re and "config" in cols_re

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
