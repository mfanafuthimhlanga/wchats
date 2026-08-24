"""Tests for TENANT migration 0018 — chunks.is_table (ticket #42, issue #7).

Named test_migration_tenant_0018 rather than test_migration_0018 for the same
reason as its 0014-0017 siblings: the CONTROL-DB tree numbers its revisions
independently and a reader who assumes one tree will look in the wrong
directory.

What the migration is for: chunking_service computes is_table on every chunk —
True for a table serialised as Markdown, False for HybridChunker text — and the
INSERT in app/worker/tasks/pipeline/chunk.py listed five columns, none of them
is_table. The flag reached the log line counting how many tables a document had
and nothing else, so retrieval could not tell a table from prose. 0018 is the
column it is written into.

WHY NOT NULL DEFAULT false, where 0017 forbids both:
    0017's column had to keep NULL and an empty array apart, because they are
    different observations about a retrieval. Here there is no third state. A
    chunk either came from the table path or it did not, every row already in a
    tenant DB was written by the text-or-table split that predates the column,
    and false is what a pre-0018 row means. A nullable column would invent an
    "unknown" that the writer cannot produce and every reader would then have
    to handle.

HOW THE SQL IS READ HERE:
    By running upgrade() and downgrade() against a recording stand-in for
    `op.execute`, not by reading their source. That is the statement the
    migration actually issues, and it keeps this file off gates.py's
    SOURCE_ASSERTION_BASELINE, which exists to stop tests asserting on
    characters of source.

APPLIED AND VERIFIED 2026-08-24 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`): 0017 to 0018,
the column arrives boolean NOT NULL DEFAULT false, downgrade drops it, and
re-upgrade restores it.

Note on encoding: all open() calls use encoding="utf-8" to avoid Windows cp1252
UnicodeDecodeError.
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
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0018_chunk_is_table.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0018", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executed(direction: str) -> str:
    """Every statement the named direction issues, whitespace-collapsed, upper.

    `op.execute` is patched on the alembic.op module the migration looks the
    name up on at call time, so this records the real statements rather than a
    reading of the file.
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
    assert mod.revision == "0018", f"Expected revision '0018', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0017", (
        f"Expected down_revision '0017', got {mod.down_revision!r}"
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


def test_0018_is_the_sole_child_of_0017_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0017 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0018" in revisions, "0018 was not discovered in alembic_tenant/versions"

    children_of_0017 = [rev for rev, down in revisions.items() if down == "0017"]
    assert children_of_0017 == ["0018"], (
        f"0017 must have exactly one child, found {sorted(children_of_0017)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked — expected a single head, got {sorted(heads)}"
    )


def test_0018_is_the_tenant_head():
    """Head IDENTITY, moved here from test_migration_tenant_0017.py.

    That file carried this assertion with a docstring saying 0018 would move
    this line and only this line, and it caught 0018 landing: the battery went
    red on the 0017 head assertion the first time this migration existed.
    Moving it is the instruction the test itself gives, and it is not the same
    as deleting it — relaxing it to `len(heads) == 1` would leave nothing
    asserting which revision the tree ends at, and an assertion getting weaker
    inside a test that still passes is invisible.

    0019 moves this line and only this line.
    """
    revisions = _all_tenant_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0018"}, (
        f"the tenant head is {sorted(heads)}, not 0018 — if a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------


def test_upgrade_adds_the_column_as_boolean_not_null_default_false():
    sql = _executed("upgrade")
    assert "IS_TABLE BOOLEAN NOT NULL DEFAULT FALSE" in sql, (
        "0018 must add is_table as boolean NOT NULL DEFAULT false: " + sql
    )
    assert "ADD COLUMN IF NOT EXISTS" in sql, (
        "the add must be guarded so a re-run is a no-op, matching 0017"
    )


def test_the_default_is_false_and_not_true():
    """false is what every pre-0018 row means, and true would relabel them.

    A chunk written before this column existed came from whichever path the
    chunker took, and the overwhelming majority is text. Defaulting to true
    would tell every reader that the whole existing corpus is tables.
    """
    sql = _executed("upgrade")
    assert "DEFAULT TRUE" not in sql
    assert "DEFAULT FALSE" in sql


def test_upgrade_touches_only_chunks():
    sql = _executed("upgrade")
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"CHUNKS"}, (
        f"0018 must touch chunks and nothing else, found {sorted(tables)}"
    )


def test_upgrade_adds_exactly_one_column():
    sql = _executed("upgrade")
    assert sql.count("ADD COLUMN") == 1, (
        "0018 adds one column; a second one landing here without its own "
        "rationale is the kind of drift this file exists to catch"
    )


@pytest.mark.parametrize(
    "forbidden",
    ["UPDATE ", "DROP COLUMN", "DROP TABLE", "RENAME", "DELETE "],
)
def test_upgrade_is_strictly_additive(forbidden):
    """No backfill, no destructive statement. The DEFAULT does the filling."""
    assert forbidden not in _executed("upgrade"), (
        f"0018 upgrade must not contain {forbidden!r} — it adds a column and "
        "nothing else"
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_added():
    sql = _executed("downgrade")
    dropped = set(re.findall(r"DROP COLUMN IF EXISTS\s+(\w+)", sql))
    assert dropped == {"IS_TABLE"}, (
        f"downgrade must drop exactly the column upgrade added, found {sorted(dropped)}"
    )


def test_every_downgrade_statement_is_guarded():
    """A downgrade that raises leaves a tenant half-migrated."""
    sql = _executed("downgrade")
    for statement in [s for s in sql.split(";") if "DROP" in s]:
        assert "IF EXISTS" in statement, (
            f"unguarded DROP in downgrade: {statement.strip()!r}"
        )


def test_downgrade_touches_only_chunks():
    sql = _executed("downgrade")
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"CHUNKS"}, (
        f"downgrade must touch chunks and nothing else, found {sorted(tables)}"
    )
