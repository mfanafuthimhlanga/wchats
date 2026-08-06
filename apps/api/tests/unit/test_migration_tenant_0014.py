"""
Tests for TENANT migration 0014 — eval_scenarios.dataset (measurement layer P2).

Named test_migration_tenant_0014 rather than test_migration_0014 because the
CONTROL-DB tree has its own 0014 (alembic/versions/0014_transactional_substrate.py)
with its own test module. The two trees are independent and share revision
numbers; a reader who assumes otherwise will look in the wrong directory.

Like 0013 this cannot be verified against a live database on this machine (no
local PostgreSQL — every `-m integration` harness skips, and a skip is
unobserved, never a pass). The source assertions below are therefore the only
observed evidence that exists for it, and they are written as constraints on
what the migration is ALLOWED to contain rather than as checks that it contains
one ADD COLUMN:

  - strictly additive     — no DROP/ALTER/RENAME of anything pre-existing
  - strictly nullable     — no NOT NULL, no DEFAULT, no CHECK, no backfill
  - rollback is a no-op   — downgrade drops only the column it added
  - the tree is not forked — 0014 is the sole child of 0013 and the sole head

The no-CHECK constraint is not pedantry. 0011 exists largely because 0005 put an
inline CHECK on eval_scenarios.source, and that constraint became the thing
standing between a shipped feature and a working INSERT. The dataset domain
lives in eval_service instead, where dataset_of() resolves anything unrecognised
to 'exploratory' rather than raising.

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
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0014_eval_scenario_dataset.py")
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_migration_0014", MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Identity and position in the tree
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), (
        f"Tenant migration 0014 not found at expected path: {MIGRATION_FILE}"
    )


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0014", f"Expected revision '0014', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0013", (
        f"Expected down_revision '0013', got {mod.down_revision!r}"
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


def test_0014_is_the_sole_child_of_0013_and_the_tree_is_not_forked():
    """0014 is the sole child of 0013, and the tenant tree has ONE head.

    A fork is invisible on this machine and fatal on a live tenant: `alembic
    upgrade head` refuses to run with two heads, so every subsequent tenant
    provision breaks. Read out of the versions directory rather than restated,
    so a second child of 0013 landing later fails here.

    The head itself is asserted as "exactly one", not as "0014": 0015
    (red_team_runs.coverage) is the head now, and pinning the head's NAME here
    would make every later revision edit this test rather than the tree's shape.
    test_migration_tenant_0015 pins the current head by name.
    """
    revisions = _all_tenant_revisions()
    assert "0014" in revisions, "0014 was not discovered in alembic_tenant/versions"

    children_of_0013 = [rev for rev, down in revisions.items() if down == "0013"]
    assert children_of_0013 == ["0014"], (
        f"0013 must have exactly one child, found {sorted(children_of_0013)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked — expected a single head, got {sorted(heads)}"
    )


# ---------------------------------------------------------------------------
# The one column
# ---------------------------------------------------------------------------


def test_upgrade_adds_the_nullable_dataset_column():
    mod = _load_migration()
    normalised = " ".join(inspect.getsource(mod.upgrade).split())
    assert (
        "ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS dataset TEXT" in normalised
    ), "Migration 0014 upgrade must add eval_scenarios.dataset as nullable TEXT"


def test_upgrade_touches_only_eval_scenarios():
    mod = _load_migration()
    upgrade_src = inspect.getsource(mod.upgrade)
    tables = set(re.findall(r"ALTER TABLE (\w+)", upgrade_src))
    assert tables == {"eval_scenarios"}, (
        f"Migration 0014 upgrade must touch eval_scenarios only, found {sorted(tables)}"
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

    CHECK is on this list deliberately: eval_scenarios already carries the scar
    of an inline CHECK (0005's source constraint, rewritten by 0011 under a
    generated name it had to discover at runtime). The dataset domain is
    enforced in eval_service.dataset_of(), which resolves an unrecognised value
    to 'exploratory' instead of failing an INSERT on a live tenant.

    DEFAULT is on it for a different reason: a DEFAULT would make every future
    row assert a membership nobody chose.
    """
    mod = _load_migration()
    upgrade_src = inspect.getsource(mod.upgrade)
    sql_only = "\n".join(
        line for line in upgrade_src.splitlines() if not line.strip().startswith("#")
    )
    assert forbidden not in sql_only.upper(), (
        f"Migration 0014 upgrade must not contain {forbidden!r} — it is required "
        "to be strictly additive and strictly nullable (cannot be verified "
        "against a live DB on this machine)"
    )


def test_no_backfill_promotes_existing_rows_into_the_golden_set():
    """The absence pin that matters most for this column.

    A backfill setting dataset='golden' on existing rows would turn a
    randomly-accumulated pile of Haiku-written scenarios into the fixed
    instrument every future comparison rests on, and it would do it silently:
    nothing downstream can tell a curated golden set from a backfilled one.
    """
    mod = _load_migration()
    source = inspect.getsource(mod.upgrade) + inspect.getsource(mod.downgrade)
    # Comment lines explain WHY there is no backfill and necessarily name the
    # value; only the executable half is scanned.
    sql_only = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    ).lower()
    assert "golden" not in sql_only, (
        "the migration must never write a dataset value — membership of the "
        "golden set is asserted by a human, never backfilled"
    )
    assert "update" not in sql_only


def test_downgrade_only_drops_what_upgrade_added():
    mod = _load_migration()
    downgrade_src = inspect.getsource(mod.downgrade)
    normalised = " ".join(downgrade_src.split())

    assert "ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS dataset" in normalised
    tables = set(re.findall(r"ALTER TABLE (\w+)", downgrade_src))
    assert tables == {"eval_scenarios"}, (
        f"Migration 0014 downgrade must touch eval_scenarios only, found {sorted(tables)}"
    )
    assert "DROP TABLE" not in downgrade_src.upper()


def test_downgrade_is_idempotent_via_if_exists():
    """A downgrade against a DB that never received 0014 must be a no-op."""
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
def test_migration_tenant_0014_db_roundtrip():
    """Integration: upgrade to 0014 -> dataset exists and accepts NULL ->
    downgrade to 0013 removes it -> re-upgrade to 0014 (idempotent).
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

    db_name = f"wchats_test_t0014_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(
        admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
    )
    with admin_engine.connect() as conn:
        conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    conn_url = f"{local_base}/{db_name}"
    script_location = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic_tenant"))

    def _scenario_columns() -> set[str]:
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        try:
            return {c["name"] for c in sa_inspect(engine).get_columns("eval_scenarios")}
        finally:
            engine.dispose()

    try:
        cfg = Config()
        cfg.set_main_option("script_location", script_location)
        cfg.set_main_option("sqlalchemy.url", conn_url)

        command.upgrade(cfg, "0014")
        assert "dataset" in _scenario_columns(), "dataset missing after 0014"

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO eval_scenarios (id, source, question, reference_answer) "
                    "VALUES (gen_random_uuid(), 'generated', 'q', 'a')"
                )
            )
            row = conn.execute(
                sa_text("SELECT dataset FROM eval_scenarios WHERE question = 'q'")
            ).fetchone()
            assert row[0] is None, "0014's column must be nullable with no DEFAULT"
        engine.dispose()

        command.downgrade(cfg, "0013")
        assert "dataset" not in _scenario_columns()

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            surviving = conn.execute(
                sa_text("SELECT COUNT(*) FROM eval_scenarios WHERE question = 'q'")
            ).scalar()
            assert surviving == 1, "rollback must not destroy eval_scenarios rows"
        engine.dispose()

        command.upgrade(cfg, "0014")
        assert "dataset" in _scenario_columns()

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
