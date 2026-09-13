"""Tests for TENANT migration 0029, the ambiguous row's verdict (issue #226).

Named test_migration_tenant_0029 like its 0014 to 0028 siblings: the CONTROL-DB
tree numbers its revisions independently and a reader who assumes one tree looks
in the wrong directory.

0028 gave a scenario an `ambiguous` flag and nothing read it. An ambiguous
scenario is not scored by the four Ragas metrics, because a correct clarifying
question retrieves nothing; its verdict is a deterministic rule on the response,
and this migration gives that verdict a home on the sample row (ADR 0012).

The constraints this file holds the migration to:

  - one column, one table, nothing else touched in either direction
  - the column is NULLABLE with no default, because NULL is a real state: every
    row of a scenario that is not ambiguous, and every row written before 0029
  - downgrade drops exactly the column upgrade created, guarded

APPLIED AND VERIFIED 2026-09-12 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`), with
`command.downgrade(cfg, "0028")` for the way down. Observed:

    before                   0028, column absent
    after upgrade head       0029, clarifying_check boolean YES None, comment present
    after downgrade 0028     0028, column absent
    after re-upgrade         0029, clarifying_check boolean YES None

`eval_samples` was empty in that run, so it did not carry a populated row up; the
column arrives nullable with no default, which is the only shape that cannot fail
on rows that exist.

The statements are read by patching `alembic.op.execute`, never by reading the
migration as text; the revision graph comes from the 0023 sibling's helper.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

from tests.unit.test_migration_tenant_0023 import VERSIONS_DIR, _all_tenant_revisions

MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0029_eval_sample_clarifying_check.py")

COLUMN = ("EVAL_SAMPLES", "CLARIFYING_CHECK")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0029", MIGRATION_FILE)
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
    assert module.revision == "0029"
    assert module.down_revision == "0028"


def test_0029_is_the_sole_child_of_0028_and_the_tree_is_unforked():
    revisions = _all_tenant_revisions()
    assert "0029" in revisions
    children_of_0028 = [rev for rev, down in revisions.items() if down == "0028"]
    assert children_of_0028 == ["0029"], (
        f"0028 must have exactly one child, found {sorted(children_of_0028)}"
    )
    parents = [down for down in revisions.values() if down is not None]
    assert len(parents) == len(set(parents)), "two revisions share a parent"


def test_upgrade_adds_one_nullable_boolean_with_no_default():
    statements = _statements("upgrade")
    adds = [s for s in statements if "ADD COLUMN" in s]
    assert len(adds) == 1, adds
    assert "ALTER TABLE EVAL_SAMPLES" in adds[0]
    assert "IF NOT EXISTS CLARIFYING_CHECK BOOLEAN" in adds[0]
    assert "NOT NULL" not in adds[0], "NULL is the state of every non-ambiguous row"
    assert "DEFAULT" not in adds[0], "a default would make an unchecked row look checked"


def test_upgrade_comments_the_column():
    statements = _statements("upgrade")
    comments = [s for s in statements if s.startswith("COMMENT ON COLUMN")]
    assert len(comments) == 1
    assert "EVAL_SAMPLES.CLARIFYING_CHECK" in comments[0]


def test_upgrade_touches_only_eval_samples():
    for statement in _statements("upgrade"):
        ddl = _ddl_only(statement)
        assert "EVAL_SCENARIOS" not in ddl
        assert "EVAL_RESULTS" not in ddl


def test_downgrade_drops_exactly_the_column_and_is_guarded():
    statements = _statements("downgrade")
    assert statements == ["ALTER TABLE EVAL_SAMPLES DROP COLUMN IF EXISTS CLARIFYING_CHECK"]
