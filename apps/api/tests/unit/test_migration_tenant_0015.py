"""
Tests for TENANT migration 0015 — red_team_runs.coverage (P2 review).

Named test_migration_tenant_0015 rather than test_migration_0015 for the same
reason as its 0014 sibling: the CONTROL-DB tree numbers its revisions
independently and a reader who assumes one tree will look in the wrong
directory.

Why the column exists, stated where the constraints are checked: P2 computed
`red_team_coverage()` in the red-team task and put it in a log line and the
Celery return value. Neither survives the request, so `GET
/agents/{id}/red-team-runs` still described a run in which four of seven
attackers could not probe (audit D4) exactly as it describes a clean
seven-vector run. Storing the coverage on the run is also what stops the deploy
gate re-labelling history: derive it at read time and every stored run silently
becomes seven-of-seven the day P4 flips SDK_ATTACKERS_CAN_PROBE.

Like 0013 and 0014 this cannot be verified against a live database on this
machine (no local PostgreSQL — every `-m integration` harness skips, and a skip
is unobserved, never a pass). The source assertions below are the only observed
evidence that exists for it, and they constrain what the migration is ALLOWED to
contain:

  - strictly additive     — no DROP/ALTER/RENAME of anything pre-existing
  - strictly nullable     — no NOT NULL, no DEFAULT, no CHECK, no backfill
  - rollback is a no-op   — downgrade drops only the column it added
  - the tree is not forked — 0015 is the sole child of 0014 and the sole head

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
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0015_red_team_run_coverage.py")
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "tenant_migration_0015", MIGRATION_FILE
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Identity and position in the tree
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), (
        f"Tenant migration 0015 not found at expected path: {MIGRATION_FILE}"
    )


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0015", f"Expected revision '0015', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0014", (
        f"Expected down_revision '0014', got {mod.down_revision!r}"
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
        down = re.search(r'^down_revision:[^=]*=\s*(?:"([^"]+)"|None)', src, re.M)
        if rev:
            revisions[rev.group(1)] = down.group(1) if down and down.group(1) else None
    return revisions


def test_0015_is_the_single_tenant_head():
    """0015 is the sole child of 0014 and the sole head of the tenant tree.

    A fork is invisible on this machine and fatal on a live tenant: `alembic
    upgrade head` refuses to run with two heads, so every subsequent tenant
    provision breaks. Read out of the versions directory rather than restated,
    so a second child of 0014 landing later fails here.
    """
    revisions = _all_tenant_revisions()
    assert "0015" in revisions, "0015 was not discovered in alembic_tenant/versions"

    children_of_0014 = [rev for rev, down in revisions.items() if down == "0014"]
    assert children_of_0014 == ["0015"], (
        f"0014 must have exactly one child, found {sorted(children_of_0014)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert heads == {"0015"}, (
        f"Expected 0015 to be the single tenant head, got heads={sorted(heads)}"
    )


# ---------------------------------------------------------------------------
# The one column
# ---------------------------------------------------------------------------


def test_upgrade_adds_the_nullable_coverage_column():
    mod = _load_migration()
    normalised = " ".join(inspect.getsource(mod.upgrade).split())
    assert (
        "ALTER TABLE red_team_runs ADD COLUMN IF NOT EXISTS coverage JSONB"
        in normalised
    ), "Migration 0015 upgrade must add red_team_runs.coverage as nullable JSONB"


def test_upgrade_touches_only_red_team_runs():
    mod = _load_migration()
    upgrade_src = inspect.getsource(mod.upgrade)
    tables = set(re.findall(r"ALTER TABLE (\w+)", upgrade_src))
    assert tables == {"red_team_runs"}, (
        f"Migration 0015 upgrade must touch red_team_runs only, found {sorted(tables)}"
    )


def test_upgrade_adds_exactly_one_column():
    """One column, not two. Scope is part of the safety argument for a
    migration that cannot be tested against a database."""
    mod = _load_migration()
    adds = re.findall(r"ADD COLUMN", inspect.getsource(mod.upgrade))
    assert len(adds) == 1, f"expected exactly one ADD COLUMN, found {len(adds)}"


# ---------------------------------------------------------------------------
# Strictly additive / strictly nullable
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

    DEFAULT is on this list for the reason that matters most here: a default
    coverage payload would make every run that never recorded one assert a
    coverage it never had, which is precisely the substitution the column exists
    to prevent. NULL means "this run did not say", and the readers report that.
    """
    mod = _load_migration()
    upgrade_src = inspect.getsource(mod.upgrade)
    sql_only = "\n".join(
        line for line in upgrade_src.splitlines() if not line.strip().startswith("#")
    )
    assert forbidden not in sql_only.upper(), (
        f"Migration 0015 upgrade must not contain {forbidden!r} — it is required "
        "to be strictly additive and strictly nullable (cannot be verified "
        "against a live DB on this machine)"
    )


def test_no_backfill_invents_coverage_for_historical_runs():
    """The absence pin that matters most for this column.

    Every red_team_runs row written before this revision belongs to a run whose
    coverage nobody recorded. A backfill would write today's numbers onto
    yesterday's runs — the exact re-labelling the column exists to prevent,
    performed once, permanently, and invisibly.
    """
    mod = _load_migration()
    source = inspect.getsource(mod.upgrade) + inspect.getsource(mod.downgrade)
    sql_only = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    ).lower()
    assert "update" not in sql_only
    assert "vectors_attempted" not in sql_only, (
        "the migration must never write a coverage value — a run's coverage is "
        "recorded by the run, never invented for it"
    )


def test_downgrade_only_drops_what_upgrade_added():
    mod = _load_migration()
    downgrade_src = inspect.getsource(mod.downgrade)
    normalised = " ".join(downgrade_src.split())

    assert (
        "ALTER TABLE red_team_runs DROP COLUMN IF EXISTS coverage" in normalised
    )
    tables = set(re.findall(r"ALTER TABLE (\w+)", downgrade_src))
    assert tables == {"red_team_runs"}, (
        f"Migration 0015 downgrade must touch red_team_runs only, found "
        f"{sorted(tables)}"
    )
    assert "DROP TABLE" not in downgrade_src.upper()


def test_downgrade_is_idempotent_via_if_exists():
    """A downgrade against a DB that never received 0015 must be a no-op."""
    mod = _load_migration()
    drops = re.findall(r"DROP COLUMN(?: IF EXISTS)?", inspect.getsource(mod.downgrade))
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
def test_migration_tenant_0015_db_roundtrip():
    """Integration: upgrade to 0015 -> coverage exists and accepts NULL ->
    downgrade to 0014 removes it -> re-upgrade to 0015 (idempotent).
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

    db_name = f"wchats_test_t0015_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(
        admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
    )
    with admin_engine.connect() as conn:
        conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    conn_url = f"{local_base}/{db_name}"
    script_location = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic_tenant"))

    def _run_columns() -> set[str]:
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        try:
            return {c["name"] for c in sa_inspect(engine).get_columns("red_team_runs")}
        finally:
            engine.dispose()

    try:
        cfg = Config()
        cfg.set_main_option("script_location", script_location)
        cfg.set_main_option("sqlalchemy.url", conn_url)

        command.upgrade(cfg, "0015")
        assert "coverage" in _run_columns(), "coverage missing after 0015"

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO red_team_runs (id, kind, status) "
                    "VALUES (gen_random_uuid(), 'm7:test', 'running')"
                )
            )
            row = conn.execute(
                sa_text("SELECT coverage FROM red_team_runs WHERE kind = 'm7:test'")
            ).fetchone()
            assert row[0] is None, "0015's column must be nullable with no DEFAULT"
        engine.dispose()

        command.downgrade(cfg, "0014")
        assert "coverage" not in _run_columns()

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            surviving = conn.execute(
                sa_text("SELECT COUNT(*) FROM red_team_runs WHERE kind = 'm7:test'")
            ).scalar()
            assert surviving == 1, "rollback must not destroy red_team_runs rows"
        engine.dispose()

        command.upgrade(cfg, "0015")
        assert "coverage" in _run_columns()

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
