"""Tests for CONTROL migration 0022, checklist_runs.pass_no (#124).

Named test_migration_control_0022 rather than test_migration_0022 because the two
alembic trees number their revisions independently. `alembic/versions` is the
CONTROL tree.

WHAT THE COLUMN IS FOR
    The checklist waits by re-queueing itself rather than by sleeping in the
    worker slot, and the task is acks_late=True. A worker that dies between
    `apply_async` and the ack hands its message back, so one wait forks into two
    chains carrying the same run_id and the same wait_state: two orchestrator
    turns billed to the tenant, two ledger rows, and two completing writes
    landing last-writer-wins on one row. Every existing guard is sequential-only
    and none of them can see it, because a continuation skips the guard entirely.
    Each pass names the number it expects the row to hold and advances it in the
    same fenced UPDATE that stamps its beat, so exactly one fork writes.

WHY THIS ONE IS NOT NULL WITH A DEFAULT, WHERE 0021'S BEAT WAS NEITHER
    A beat is a moment, and a default would hand a historical row a moment it
    never had. This is a count, and zero passes is the true count for every row
    written before the column existed. The default is also what a fresh INSERT
    gets, which is what the first pass then expects to find.

HOW THE SQL IS READ HERE
    By running upgrade() and downgrade() against a recording stand-in for
    `op.execute`, not by reading their source. That is the statement the
    migration actually issues, and it keeps this file off gates.py's
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
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0022_checklist_run_pass_no.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("control_0022", MIGRATION_FILE)
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
    assert mod.revision == "0022", f"Expected revision '0022', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0021", (
        f"Expected down_revision '0021', got {mod.down_revision!r}"
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


def test_0022_is_the_sole_child_of_0021_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on the live control database.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0021 stops every later control migration.
    """
    revisions = _all_control_revisions()
    assert "0022" in revisions, "0022 was not discovered in alembic/versions"

    children_of_0021 = [rev for rev, down in revisions.items() if down == "0021"]
    assert children_of_0021 == ["0022"], (
        f"0021 must have exactly one child, found {sorted(children_of_0021)}"
    )


def test_0022_is_the_control_head():
    """Head IDENTITY, which `len(heads) == 1` does not pin.

    Moved here from test_migration_control_0021.py when 0022 landed, on that
    file's own instruction. 0023 moves this line and only this line, into its own
    test file, rather than deleting it. An assertion getting weaker inside a test
    that still passes is invisible.
    """
    revisions = _all_control_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0022"}, (
        f"the control head is {sorted(heads)}, not 0022. If a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------


def test_upgrade_adds_pass_no_to_checklist_runs():
    sql = _executed("upgrade")
    assert "ADD COLUMN IF NOT EXISTS PASS_NO INTEGER" in sql, (
        f"0022 must add checklist_runs.pass_no as integer, issued: {sql}"
    )


def test_the_add_is_guarded_so_a_re_run_is_a_no_op():
    assert "ADD COLUMN IF NOT EXISTS" in _executed("upgrade"), (
        "the add must be guarded, matching 0019 and every control column since"
    )


def test_the_counter_is_not_null_so_the_fence_never_compares_against_null():
    """`pass_no = NULL` is never true, so a nullable column would stop every
    continuation of every run written before this migration."""
    sql = _executed("upgrade")
    assert "NOT NULL" in sql, f"0022 must add a NOT NULL column, issued: {sql}"


def test_the_default_is_zero_so_a_historical_row_reads_as_having_taken_no_pass():
    """NOT NULL with no default fails on a table that already has rows, and any
    other default would claim passes that were never taken."""
    sql = _executed("upgrade")
    assert "DEFAULT 0" in sql, f"0022 must default the counter to 0, issued: {sql}"


def test_upgrade_touches_checklist_runs_and_nothing_else():
    sql = _executed("upgrade")
    named = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    named |= set(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)", sql))
    named |= set(re.findall(r"UPDATE\s+(\w+)\s+SET", sql))
    assert named == {"CHECKLIST_RUNS"}, (
        f"0022 must touch checklist_runs alone, found {sorted(named)}"
    )


def test_upgrade_changes_no_schema_it_did_not_add():
    sql = _executed("upgrade")
    for forbidden in ("DROP COLUMN", "DROP TABLE", "RENAME", "ALTER COLUMN"):
        assert forbidden not in sql, (
            f"0022's upgrade must add and nothing else, found {forbidden}: {sql}"
        )


def test_upgrade_rewrites_no_row():
    """Every existing row takes the default in place. A backfill here would be
    writing a count nobody measured."""
    sql = _executed("upgrade")
    assert "UPDATE" not in sql, (
        f"0022's upgrade must write no checklist_runs row: {sql}"
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_added():
    sql = _executed("downgrade")
    dropped = set(re.findall(r"DROP COLUMN IF EXISTS\s+(\w+)", sql))
    assert dropped == {"PASS_NO"}, (
        f"downgrade must drop exactly the column upgrade added, found {sorted(dropped)}"
    )


def test_downgrade_rewrites_no_row():
    """0021's index and every row's status and beat survive in both directions."""
    sql = _executed("downgrade")
    assert "UPDATE" not in sql, (
        f"downgrade must not rewrite any checklist_runs row: {sql}"
    )
    assert "DROP INDEX" not in sql, (
        f"the one-live-run index belongs to 0021, not to this downgrade: {sql}"
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
