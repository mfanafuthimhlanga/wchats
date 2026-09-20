"""Tests for TENANT migration 0031, a faithfulness row carries its claims (#290).

Named test_migration_tenant_0031 like its 0014 to 0030 siblings: the CONTROL-DB
tree numbers its revisions independently and a reader who assumes one tree looks
in the wrong directory.

A faithfulness score is the share of an answer's statements the judge found in
the retrieved text, and the share cannot say which one it did not find. 0031
gives the row a `claims` column for the statements and their verdicts.

The constraints this file holds the migration to:

  - one column, one table, nothing else touched either way
  - the column is NULLABLE with no default, because NULL is a real state: every
    row before 0031, every metric that decides no claims, and a judge call that
    failed. An empty-list default would read as "the judge found no claims",
    which is a claim about the answer nobody made
  - downgrade drops exactly what upgrade created, guarded

The statements are read by patching `alembic.op.execute`, never by reading the
migration as text; the revision graph comes from the 0023 sibling's helper.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

from tests.unit.test_migration_tenant_0023 import VERSIONS_DIR, _all_tenant_revisions

MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0031_eval_result_claims.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0031", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(direction: str) -> list[str]:
    module = _load_migration()
    statements: list[str] = []
    with patch("alembic.op.execute", side_effect=statements.append):
        getattr(module, direction)()
    assert statements, f"{direction}() issued no statement at all"
    return [" ".join(str(s).split()).upper() for s in statements]


def _ddl_only(statement: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", statement)


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), f"missing migration: {MIGRATION_FILE}"


def test_migration_revision_and_parent():
    module = _load_migration()
    assert module.revision == "0031"
    assert module.down_revision == "0030"


def test_0031_is_the_sole_child_of_0030_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0030 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0031" in revisions
    children_of_0030 = [rev for rev, down in revisions.items() if down == "0030"]
    assert children_of_0030 == ["0031"], (
        f"0030 must have exactly one child, found {sorted(children_of_0030)}"
    )
    parents = [down for down in revisions.values() if down is not None]
    assert len(parents) == len(set(parents)), "two revisions share a parent"


def test_upgrade_adds_one_nullable_jsonb_with_no_default():
    statements = _statements("upgrade")
    adds = [s for s in statements if "ADD COLUMN" in s]
    assert len(adds) == 1, adds
    assert "ALTER TABLE EVAL_RESULTS" in adds[0]
    assert "IF NOT EXISTS CLAIMS JSONB" in adds[0]
    assert "NOT NULL" not in adds[0], (
        "NULL is the state of every row before 0031 and of every metric that "
        "decides no claims"
    )
    assert "DEFAULT" not in adds[0], (
        "an empty-list default would read as a judge that found no claims"
    )


def test_upgrade_comments_the_column_and_says_null_is_not_a_pass():
    statements = _statements("upgrade")
    comments = [s for s in statements if s.startswith("COMMENT ON COLUMN")]
    assert len(comments) == 1
    assert "EVAL_RESULTS.CLAIMS" in comments[0]
    assert "NEVER A PASSING ANSWER" in comments[0]


def test_upgrade_touches_only_eval_results():
    for statement in _statements("upgrade"):
        ddl = _ddl_only(statement)
        assert "EVAL_RUNS" not in ddl
        assert "EVAL_SCENARIOS" not in ddl
        assert "EVAL_SAMPLES" not in ddl
        assert "CREATE INDEX" not in ddl


def test_downgrade_drops_exactly_what_upgrade_created_and_is_guarded():
    assert _statements("downgrade") == [
        "ALTER TABLE EVAL_RESULTS DROP COLUMN IF EXISTS CLAIMS",
    ]
