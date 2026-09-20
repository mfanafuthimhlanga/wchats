"""Tests for TENANT migration 0032, claim_reviews (#290 step 3).

Named test_migration_tenant_0032 like its 0014 to 0031 siblings: the CONTROL-DB
tree numbers its revisions independently and a reader who assumes one tree looks
in the wrong directory.

The Tenant's yes or no on a claim the faithfulness judge flagged. The constraints
this file holds the migration to:

  - one table and one index, nothing else touched either way
  - a unique key on (run, scenario, position), because a second answer on the
    same claim replaces the first and the writer is an upsert on that key
  - `supported` NOT NULL: an answer that says nothing is not stored
  - no foreign key, matching eval_samples (0027)
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

MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0032_claim_reviews.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0032", MIGRATION_FILE)
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
    assert module.revision == "0032"
    assert module.down_revision == "0031"


def test_0032_is_the_sole_child_of_0031_and_the_tree_is_unforked():
    revisions = _all_tenant_revisions()
    assert "0032" in revisions
    children_of_0031 = [rev for rev, down in revisions.items() if down == "0031"]
    assert children_of_0031 == ["0032"], (
        f"0031 must have exactly one child, found {sorted(children_of_0031)}"
    )
    parents = [down for down in revisions.values() if down is not None]
    assert len(parents) == len(set(parents)), "two revisions share a parent"


def test_0032_is_the_tenant_head():
    """Head IDENTITY, moved here from test_migration_tenant_0031.py.

    0033 moves this line and only this line.
    """
    revisions = _all_tenant_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0032"}, (
        f"the tenant head is {sorted(heads)}, not 0032. If a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


def test_upgrade_creates_one_table_with_the_answer_columns_and_the_replace_key():
    statements = _statements("upgrade")
    [create] = [s for s in statements if "CREATE TABLE" in s]
    assert "IF NOT EXISTS CLAIM_REVIEWS" in create
    for column in ("EVAL_RUN_ID UUID NOT NULL", "SCENARIO_ID TEXT NOT NULL", "POSITION INTEGER NOT NULL",
                   "STATEMENT TEXT NOT NULL", "SUPPORTED BOOLEAN NOT NULL", "REVIEWED_AT TIMESTAMPTZ NOT NULL"):
        assert column in create, column
    assert "UNIQUE (EVAL_RUN_ID, SCENARIO_ID, POSITION)" in create


def test_upgrade_adds_no_foreign_key():
    for statement in _statements("upgrade"):
        assert "REFERENCES" not in _ddl_only(statement)
        assert "FOREIGN KEY" not in _ddl_only(statement)


def test_upgrade_indexes_the_run():
    [index] = [s for s in _statements("upgrade") if "CREATE INDEX" in s]
    assert "IX_CLAIM_REVIEWS_EVAL_RUN_ID" in index
    assert "ON CLAIM_REVIEWS (EVAL_RUN_ID)" in index


def test_upgrade_comments_the_table():
    [comment] = [s for s in _statements("upgrade") if s.startswith("COMMENT ON TABLE")]
    assert "CLAIM_REVIEWS" in comment


def test_upgrade_touches_only_claim_reviews():
    for statement in _statements("upgrade"):
        ddl = _ddl_only(statement)
        for other in ("EVAL_RUNS", "EVAL_RESULTS", "EVAL_SAMPLES", "EVAL_SCENARIOS"):
            assert other not in ddl, other


def test_downgrade_drops_exactly_what_upgrade_created_and_is_guarded():
    assert _statements("downgrade") == [
        "DROP INDEX IF EXISTS IX_CLAIM_REVIEWS_EVAL_RUN_ID",
        "DROP TABLE IF EXISTS CLAIM_REVIEWS",
    ]
