"""Tests for TENANT migration 0021, red_team_runs.result (ticket 15, issue #52).

Named test_migration_tenant_0021 rather than test_migration_0021 for the same
reason as its 0014 to 0020 siblings: the CONTROL-DB tree numbers its revisions
independently and a reader who assumes one tree looks in the wrong directory.

What the migration is for, stated where its constraints are checked: a completed
run wrote `findings`, `max_severity`, `deployment_blocked` and `coverage`, and
between them they cannot say how many independent attempts a vector made. A
vector attacked once and a vector attacked three times read identically off the
row. `result` holds `RedTeamResult.payload`, which says.

The constraints this file holds the migration to:

  - additive column only   — no DROP or RENAME of anything pre-existing
  - nullable only          — no NOT NULL, no DEFAULT, no backfill. NULL means the
                             run did not record its result, which is every row
                             written before this revision, and that is a
                             different claim from "the run measured nothing"
  - one table touched      — red_team_runs, nothing else
  - downgrade drops only what upgrade added, every statement IF EXISTS

APPLIED AND VERIFIED 2026-08-29 against the local `wchats_tenant_probe` cluster
through the production path (`app.services.migrations.run_tenant_migrations`):
0020 to 0021, the column arrives `jsonb` and nullable with no DEFAULT, downgrade
drops it, re-upgrade restores it. So the assertions below are not the only
evidence, and they keep a different job: they constrain what the migration is
allowed to CONTAIN, which a successful apply does not.

The statements are read by patching `alembic.op.execute`, never by reading the
file as text. A test that matches the characters of a migration pins a shape that
is free to change while the behaviour stays correct.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(__file__)
VERSIONS_DIR = os.path.normpath(
    os.path.join(_TESTS_DIR, "../../alembic_tenant/versions")
)
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0021_red_team_run_result.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0021", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executed(direction: str) -> str:
    """Every statement the named direction issues, whitespace-collapsed, upper.

    `op.execute` is patched on the alembic.op module the migration looks the name
    up on at call time, so this records the real statements rather than a reading
    of the file.
    """
    module = _load_migration()
    statements: list[str] = []
    with patch("alembic.op.execute", side_effect=statements.append):
        getattr(module, direction)()
    assert statements, f"{direction}() issued no statement at all"
    return " ; ".join(" ".join(str(s).split()) for s in statements).upper()


# ---------------------------------------------------------------------------
# Identity and parentage
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), f"missing migration: {MIGRATION_FILE}"


def test_migration_revision():
    assert _load_migration().revision == "0021"


def test_migration_down_revision():
    assert _load_migration().down_revision == "0020"


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


def test_0021_is_the_sole_child_of_0020_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0020 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0021" in revisions, "0021 was not discovered in alembic_tenant/versions"

    children_of_0020 = [rev for rev, down in revisions.items() if down == "0020"]
    assert children_of_0020 == ["0021"], (
        f"0020 must have exactly one child, found {sorted(children_of_0020)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked. Expected a single head, got {sorted(heads)}"
    )


def test_0021_is_the_tenant_head():
    """Head IDENTITY, moved here from test_migration_tenant_0020.py.

    That file carried this assertion with a docstring saying 0021 would move this
    line and only this line, and it caught 0021 landing. Moving it is the
    instruction the test itself gives, and it is not the same as deleting it.
    Relaxing it to `len(heads) == 1` would leave nothing asserting which revision
    the tree ends at, and an assertion getting weaker inside a test that still
    passes is invisible.

    0022 moves this line and only this line.
    """
    revisions = _all_tenant_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0021"}, (
        f"the tenant head is {sorted(heads)}, not 0021. If a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------


def test_upgrade_adds_the_column_as_jsonb():
    sql = _executed("upgrade")
    assert "RESULT JSONB" in sql, "0021 must add result as jsonb: " + sql
    assert "ADD COLUMN IF NOT EXISTS" in sql, (
        "the add must be guarded so a re-run is a no-op, matching 0017 to 0020"
    )


def test_the_column_is_nullable_and_takes_no_default():
    """Every pre-0021 row recorded no result, and NULL is how it says so.

    NOT NULL would refuse those rows outright. A default would assert a k and a
    set of per-vector attempt counts about runs that never measured any, which is
    the reverse of what the column exists for.
    """
    sql = _executed("upgrade")
    assert "NOT NULL" not in sql
    assert "DEFAULT" not in sql


def test_upgrade_touches_only_red_team_runs():
    sql = _executed("upgrade")
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"RED_TEAM_RUNS"}, (
        f"0021 must touch red_team_runs and nothing else, found {sorted(tables)}"
    )


def test_upgrade_adds_exactly_one_column():
    sql = _executed("upgrade")
    assert sql.count("ADD COLUMN") == 1, (
        "0021 adds one column; a second one landing here without its own "
        "rationale is the kind of drift this file exists to catch"
    )


def test_the_column_carries_its_comment():
    """A reader of the catalogue learns what NULL means without opening the repo."""
    assert "COMMENT ON COLUMN RED_TEAM_RUNS.RESULT" in _executed("upgrade")


def test_the_coverage_column_is_left_alone():
    """0015's column keeps its meaning and its readers.

    `deployment_service._coverage_from_run` and both red-team routes read four
    keys out of `coverage`. This migration adds a place for the record beside it
    rather than repurposing it, so nothing downstream has to change to keep
    working.
    """
    assert "COVERAGE" not in _executed("upgrade")


@pytest.mark.parametrize(
    "forbidden",
    ["UPDATE ", "DROP COLUMN", "DROP TABLE", "RENAME", "DELETE "],
)
def test_upgrade_is_strictly_additive(forbidden):
    """No backfill. There is no measurement to attribute to a historical run."""
    assert forbidden not in _executed("upgrade"), (
        f"0021 upgrade must not contain {forbidden!r}. It adds a column and "
        "comments it, and nothing else"
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_added():
    sql = _executed("downgrade")
    dropped = set(re.findall(r"DROP COLUMN IF EXISTS\s+(\w+)", sql))
    assert dropped == {"RESULT"}, (
        f"downgrade must drop result alone, found {sorted(dropped)}"
    )


def test_every_downgrade_statement_is_guarded():
    """A downgrade that raises leaves a tenant half-migrated."""
    sql = _executed("downgrade")
    for statement in [s for s in sql.split(";") if "DROP" in s]:
        assert "IF EXISTS" in statement, (
            f"unguarded DROP in downgrade: {statement.strip()!r}"
        )


def test_downgrade_drops_no_table():
    """red_team_runs predates this migration by twenty revisions."""
    assert "DROP TABLE" not in _executed("downgrade")
