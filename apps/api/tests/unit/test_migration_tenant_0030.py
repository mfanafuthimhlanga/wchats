"""Tests for TENANT migration 0030, a rejudge run names its source (#274).

Named test_migration_tenant_0030 like its 0014 to 0029 siblings: the CONTROL-DB
tree numbers its revisions independently and a reader who assumes one tree looks
in the wrong directory.

#274 replaced the instrument behind the gated relevancy metric, which strands
every verdict measured against the old one. `rejudge_eval_run` rescores a
finished run's stored samples into a NEW `eval_runs` row, and this column is what
says which run it rescored. Without it a second opinion is indistinguishable from
a second measurement, and the idempotency check that stops a rejudge paying twice
has nothing to key on.

The constraints this file holds the migration to:

  - one column and one partial index, one table, nothing else touched either way
  - the column is NULLABLE with no default, because NULL is a real state: every
    run that measured an agent, which is every row written before 0030
  - downgrade drops exactly what upgrade created, guarded

APPLIED AND VERIFIED against the local `wchats_tenant_probe` cluster through the
production path (`migrations.run_tenant_migrations`), with
`command.downgrade(cfg, "0029")` for the way down. The observed output is in
`.dev/plans/260915-relevance-judge.md`.

The statements are read by patching `alembic.op.execute`, never by reading the
migration as text; the revision graph comes from the 0023 sibling's helper.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

from tests.unit.test_migration_tenant_0023 import VERSIONS_DIR, _all_tenant_revisions

MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0030_eval_run_source_run.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0030", MIGRATION_FILE)
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
    assert module.revision == "0030"
    assert module.down_revision == "0029"


def test_0030_is_the_sole_child_of_0029_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0029 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0030" in revisions
    children_of_0029 = [rev for rev, down in revisions.items() if down == "0029"]
    assert children_of_0029 == ["0030"], (
        f"0029 must have exactly one child, found {sorted(children_of_0029)}"
    )
    parents = [down for down in revisions.values() if down is not None]
    assert len(parents) == len(set(parents)), "two revisions share a parent"


def test_0030_is_the_tenant_head():
    """Head IDENTITY, moved here from test_migration_tenant_0028.py.

    That file carried this assertion with a docstring saying 0030 would move this
    line and only this line, and it caught 0030 landing. Moving it is the
    instruction the test itself gives, and it is not the same as deleting it.

    0031 moves this line and only this line.
    """
    revisions = _all_tenant_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0030"}, (
        f"the tenant head is {sorted(heads)}, not 0030. If a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


def test_upgrade_adds_one_nullable_uuid_with_no_default():
    statements = _statements("upgrade")
    adds = [s for s in statements if "ADD COLUMN" in s]
    assert len(adds) == 1, adds
    assert "ALTER TABLE EVAL_RUNS" in adds[0]
    assert "IF NOT EXISTS SOURCE_RUN_ID UUID" in adds[0]
    assert "NOT NULL" not in adds[0], (
        "NULL is the state of every run that measured an agent, which is every "
        "row written before 0030"
    )
    assert "DEFAULT" not in adds[0]


def test_upgrade_adds_no_foreign_key():
    """Matching `eval_samples.eval_run_id`, which is the table that lacks one.

    `eval_results.eval_run_id` DOES carry a foreign key (0001:167), so it is not
    the precedent: `eval_samples` is. A run row is inserted before any work
    starts and never deleted, so the reference holds by construction, and a
    constraint would make the writer's failure mode depend on write order.
    """
    for statement in _statements("upgrade"):
        assert "REFERENCES" not in _ddl_only(statement)
        assert "FOREIGN KEY" not in _ddl_only(statement)


def test_upgrade_indexes_only_the_rows_that_have_a_source():
    """A partial index, because every pre-0030 row and every agent run is NULL."""
    statements = _statements("upgrade")
    [index] = [s for s in statements if "CREATE INDEX" in s]
    assert "IX_EVAL_RUNS_SOURCE_RUN_ID" in index
    assert "WHERE SOURCE_RUN_ID IS NOT NULL" in index


def test_upgrade_comments_the_column():
    statements = _statements("upgrade")
    comments = [s for s in statements if s.startswith("COMMENT ON COLUMN")]
    assert len(comments) == 1
    assert "EVAL_RUNS.SOURCE_RUN_ID" in comments[0]


def test_upgrade_touches_only_eval_runs():
    for statement in _statements("upgrade"):
        ddl = _ddl_only(statement)
        assert "EVAL_SCENARIOS" not in ddl
        assert "EVAL_RESULTS" not in ddl
        assert "EVAL_SAMPLES" not in ddl


def test_downgrade_drops_exactly_what_upgrade_created_and_is_guarded():
    assert _statements("downgrade") == [
        "DROP INDEX IF EXISTS IX_EVAL_RUNS_SOURCE_RUN_ID",
        "ALTER TABLE EVAL_RUNS DROP COLUMN IF EXISTS SOURCE_RUN_ID",
    ]
