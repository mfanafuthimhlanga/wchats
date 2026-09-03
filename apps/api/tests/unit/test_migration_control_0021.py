"""Tests for CONTROL migration 0021, checklist_runs.heartbeat_at (#129).

Named test_migration_control_0021 rather than test_migration_0021 because the two
alembic trees number their revisions independently. The tenant tree already has
its own 0021, so a reader who assumes one tree looks in the wrong directory.
`alembic/versions` is the CONTROL tree.

WHAT THE COLUMN IS FOR
    The checklist's idempotency guard asked whether a 'running' checklist_runs row
    had been CREATED within the last sixty minutes. A congested chain outlives
    that window while still working, so a second trigger found no live row and
    started a second checklist for the same agent. Age since creation cannot
    separate a chain that is still going from one nothing will ever finish; a beat
    stamped by each pass can, and this column holds it.

WHY IT IS NULLABLE WITH NO DEFAULT AND NO BACKFILL
    Every row written before this migration has no beat, and so does a row
    inserted seconds ago whose first pass has not polled yet. The guard reads
    COALESCE(heartbeat_at, created_at) precisely so a NULL costs nothing, and a
    DEFAULT here would hand a historical run a beat it never had.

HOW THE SQL IS READ HERE
    By running upgrade() and downgrade() against a recording stand-in for
    `op.execute`, not by reading their source. That is the statement the migration
    actually issues, and it keeps this file off gates.py's
    SOURCE_ASSERTION_BASELINE, which exists to stop tests asserting on characters
    of source.

Note on encoding: all open() calls use encoding="utf-8" to avoid Windows cp1252
UnicodeDecodeError.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(__file__)
VERSIONS_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic/versions"))
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0021_checklist_run_heartbeat.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("control_0021", MIGRATION_FILE)
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
    mod = _load_migration()
    assert mod.revision == "0021", f"Expected revision '0021', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0020", (
        f"Expected down_revision '0020', got {mod.down_revision!r}"
    )


def _all_control_revisions() -> dict[str, str | None]:
    """revision -> down_revision for every file in alembic/versions."""
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
    """A fork is invisible here and fatal on the live control database.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0020 stops every later control migration.
    """
    revisions = _all_control_revisions()
    assert "0021" in revisions, "0021 was not discovered in alembic/versions"

    children_of_0020 = [rev for rev, down in revisions.items() if down == "0020"]
    assert children_of_0020 == ["0021"], (
        f"0020 must have exactly one child, found {sorted(children_of_0020)}"
    )


def test_0021_is_the_control_head():
    """Head IDENTITY, which `len(heads) == 1` in the 0020 file does not pin.

    Moved here from test_migration_control_0020.py when 0021 landed, on that
    file's own instruction. 0022 moves this line and only this line, into its own
    test file, rather than deleting it. An assertion getting weaker inside a test
    that still passes is invisible.
    """
    revisions = _all_control_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0021"}, (
        f"the control head is {sorted(heads)}, not 0021. If a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------


def test_upgrade_adds_heartbeat_at_to_checklist_runs():
    sql = _executed("upgrade")
    assert "ADD COLUMN IF NOT EXISTS HEARTBEAT_AT TIMESTAMPTZ" in sql, (
        f"0021 must add checklist_runs.heartbeat_at as timestamptz, issued: {sql}"
    )


def test_the_add_is_guarded_so_a_re_run_is_a_no_op():
    assert "ADD COLUMN IF NOT EXISTS" in _executed("upgrade"), (
        "the add must be guarded, matching 0019 and every control column since"
    )


def test_the_column_is_nullable_so_a_historical_run_stays_unbeaten():
    """A NOT NULL here needs a backfill, and a backfilled beat is a lie.

    The guard reads COALESCE(heartbeat_at, created_at). A row that never beat has
    to arrive as NULL for that fallback to mean anything.
    """
    sql = _executed("upgrade")
    assert "NOT NULL" not in sql, f"0021 must add a nullable column, issued: {sql}"


def test_no_default_invents_a_beat_for_an_existing_row():
    """A DEFAULT would stamp every historical run as beating at migration time."""
    sql = _executed("upgrade")
    assert "DEFAULT" not in sql, f"0021 must add no default, issued: {sql}"


def test_upgrade_touches_checklist_runs_and_nothing_else():
    sql = _executed("upgrade")
    named = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    named |= set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)", sql))
    assert named == {"CHECKLIST_RUNS"}, (
        f"0021 must touch checklist_runs alone, found {sorted(named)}"
    )


# ---------------------------------------------------------------------------
# Additive only
# ---------------------------------------------------------------------------


def test_upgrade_is_strictly_additive():
    sql = _executed("upgrade")
    for forbidden in ("DROP COLUMN", "DROP TABLE", "RENAME", "ALTER COLUMN"):
        assert forbidden not in sql, (
            f"0021's upgrade must add and nothing else, found {forbidden}: {sql}"
        )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_added():
    sql = _executed("downgrade")
    dropped = set(re.findall(r"DROP COLUMN IF EXISTS\s+(\w+)", sql))
    assert dropped == {"HEARTBEAT_AT"}, (
        f"downgrade must drop exactly the column upgrade added, found {sorted(dropped)}"
    )


def test_every_downgrade_statement_is_guarded():
    """A downgrade that raises leaves the control database half-migrated."""
    sql = _executed("downgrade")
    for statement in [s for s in sql.split(";") if "DROP" in s]:
        assert "IF EXISTS" in statement, (
            f"unguarded DROP in downgrade: {statement.strip()!r}"
        )


def test_downgrade_touches_no_other_table():
    sql = _executed("downgrade")
    named = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    named |= set(re.findall(r"DROP TABLE IF EXISTS\s+(\w+)", sql))
    assert named == {"CHECKLIST_RUNS"}, (
        f"downgrade must touch checklist_runs and nothing else, found {sorted(named)}"
    )
