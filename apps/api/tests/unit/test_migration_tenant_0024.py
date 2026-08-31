"""Tests for TENANT migration 0024, the authored source (ticket 19, #56).

Named test_migration_tenant_0024 like its 0014 to 0023 siblings: the CONTROL-DB
tree numbers its revisions independently and a reader who assumes one tree looks
in the wrong directory.

What the migration is for, stated where its constraints are checked. The golden
registration route inserts rows the owner wrote by hand, and 0011's CHECK
(eval_scenarios_source_check_v2) admitted only the four machine origins, so the
INSERT died on CheckViolation. 0024 replaces that CHECK with a v3 that adds
'authored' and changes nothing else.

The constraints this file holds the migration to:

  - the v3 CHECK admits 'authored' beside the four machine origins
  - downgrade restores v2, which never names 'authored'
  - eval_scenarios is the only table touched, in both directions
  - no column is added, dropped or renamed, in either direction

APPLIED AND VERIFIED 2026-08-31 against the local `wchats_tenant_probe` cluster:
upgrade to head inserted source='authored' and refused 'bogus'
(eval_scenarios_source_check_v3); downgrade to 0023 refused 'authored'
(eval_scenarios_source_check_v2); re-upgrade admitted it again. The assertions
below keep a different job: they constrain what the migration is allowed to
CONTAIN, which a successful apply does not.

The statements are read by patching `alembic.op.execute`, never by reading the
migration as text; the revision graph comes from the 0023 sibling's helper.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

from tests.unit.test_migration_tenant_0023 import VERSIONS_DIR, _all_tenant_revisions

MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0024_eval_scenarios_authored_source.py")

#: What v2 admitted; v3 is these plus 'authored'.
MACHINE_SOURCES = ("'GENERATED'", "'MINED'", "'PRODUCTION'", "'RED_TEAM'")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0024", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executed(direction: str) -> str:
    """Every statement the named direction issues, whitespace-collapsed, upper."""
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
    assert _load_migration().revision == "0024"


def test_migration_down_revision():
    assert _load_migration().down_revision == "0023"


def test_0024_is_the_sole_child_of_0023_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0023 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0024" in revisions, "0024 was not discovered in alembic_tenant/versions"

    children_of_0023 = [rev for rev, down in revisions.items() if down == "0023"]
    assert children_of_0023 == ["0024"], (
        f"0023 must have exactly one child, found {sorted(children_of_0023)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked. Expected a single head, got {sorted(heads)}"
    )


def test_0024_is_the_tenant_head():
    """Head IDENTITY, moved here from test_migration_tenant_0023.py.

    That file carried this assertion with a docstring saying 0024 would move this
    line and only this line, and it caught 0024 landing. Moving it is the
    instruction the test itself gives, and it is not the same as deleting it.
    Relaxing it to `len(heads) == 1` would leave nothing asserting which revision
    the tree ends at, and an assertion getting weaker inside a test that still
    passes is invisible.

    0025 moves this line and only this line.
    """
    revisions = _all_tenant_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0024"}, (
        f"the tenant head is {sorted(heads)}, not 0024. If a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


# ---------------------------------------------------------------------------
# The CHECK
# ---------------------------------------------------------------------------


def test_upgrade_admits_authored_beside_the_machine_origins():
    """'authored' joins the list; nothing already admitted leaves it."""
    sql = _executed("upgrade")
    assert "'AUTHORED'" in sql, f"v3 must admit 'authored': {sql}"
    for source in MACHINE_SOURCES:
        assert source in sql, f"v3 must keep {source}: {sql}"
    assert "EVAL_SCENARIOS_SOURCE_CHECK_V3" in sql


def test_downgrade_restores_v2_which_never_names_authored():
    """After a rollback an 'authored' INSERT must be refused again."""
    sql = _executed("downgrade")
    assert "'AUTHORED'" not in sql, (
        f"v2 admits only the machine origins; 'authored' in downgrade: {sql}"
    )
    for source in MACHINE_SOURCES:
        assert source in sql, f"the restored v2 must keep {source}: {sql}"
    assert "EVAL_SCENARIOS_SOURCE_CHECK_V2" in sql


def test_only_eval_scenarios_is_touched():
    for direction in ("upgrade", "downgrade"):
        sql = _executed(direction)
        tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
        assert tables == {"EVAL_SCENARIOS"}, (
            f"{direction} must touch eval_scenarios and nothing else, "
            f"found {sorted(tables)}"
        )


def test_no_column_or_table_changes_in_either_direction():
    """0024 is a constraint swap. A column change riding it has no rationale."""
    for direction in ("upgrade", "downgrade"):
        sql = _executed(direction)
        for forbidden in ("ADD COLUMN", "DROP COLUMN", "DROP TABLE", "RENAME"):
            assert forbidden not in sql, (
                f"{direction} must not contain {forbidden!r}: {sql}"
            )
