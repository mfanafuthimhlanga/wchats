"""Tests for TENANT migration 0022, eval_runs.result (ticket 14, issue #51).

Named test_migration_tenant_0022 rather than test_migration_0022 for the same
reason as its 0014 to 0021 siblings: the CONTROL-DB tree numbers its revisions
independently and a reader who assumes one tree looks in the wrong directory.

What the migration is for, stated where its constraints are checked: a completed
eval run left its scores in `eval_results` and its configuration in
`eval_runs.config`, and neither holds the run's numbers. Three readers each
derived them again and were free to disagree. `result` holds
`EvalResult.payload`, which is the one derivation.

The constraints this file holds the migration to:

  - additive column only:  no DROP or RENAME of anything pre-existing
  - nullable only:         no NOT NULL, no DEFAULT, no backfill. NULL means the
                           run did not record its result, which is every row
                           written before this revision, and that is a
                           different claim from "the run measured nothing"
  - one table touched:     eval_runs, nothing else
  - `config` is left alone: the configuration a run asserts and the measurement
                           it produced stay two columns
  - downgrade drops only what upgrade added, every statement IF EXISTS

APPLIED AND VERIFIED 2026-08-30 against the local `wchats_tenant_probe` cluster
through the production path (`app.services.migrations.run_tenant_migrations`):
0021 to 0022, the column arrives `jsonb` and nullable with no DEFAULT, downgrade
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
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0022_eval_run_result.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0022", MIGRATION_FILE)
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
    assert _load_migration().revision == "0022"


def test_migration_down_revision():
    assert _load_migration().down_revision == "0021"


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


def test_0022_is_the_sole_child_of_0021_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0021 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0022" in revisions, "0022 was not discovered in alembic_tenant/versions"

    children_of_0021 = [rev for rev, down in revisions.items() if down == "0021"]
    assert children_of_0021 == ["0022"], (
        f"0021 must have exactly one child, found {sorted(children_of_0021)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked. Expected a single head, got {sorted(heads)}"
    )


# The head IDENTITY assertion that stood here has MOVED to
# tests/unit/test_migration_tenant_0023.py, which is the instruction it carried:
# "0023 moves this line and only this line". 0023 landed and it caught it.
# Moving it is not the same as deleting it, and the tree still has exactly one
# test naming which revision it ends at.
#
# What stays here is `test_0022_is_the_sole_child_of_0021_and_the_tree_is_unforked`
# above. That one is about 0022's own parentage and it does not weaken when a
# later revision lands.


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------


def test_upgrade_adds_the_column_as_jsonb():
    sql = _executed("upgrade")
    assert "RESULT JSONB" in sql, "0022 must add result as jsonb: " + sql
    assert "ADD COLUMN IF NOT EXISTS" in sql, (
        "the add must be guarded so a re-run is a no-op, matching 0017 to 0021"
    )


def test_the_column_is_nullable_and_takes_no_default():
    """Every pre-0022 run recorded no result, and NULL is how it says so.

    NOT NULL would refuse those rows outright. A default would assert a set of
    per-dataset measurements about runs that never took any, which is the reverse
    of what the column exists for.
    """
    sql = _executed("upgrade")
    assert "NOT NULL" not in sql
    assert "DEFAULT" not in sql


def test_upgrade_touches_only_eval_runs():
    sql = _executed("upgrade")
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"EVAL_RUNS"}, (
        f"0022 must touch eval_runs and nothing else, found {sorted(tables)}"
    )


def test_upgrade_adds_exactly_one_column():
    sql = _executed("upgrade")
    assert sql.count("ADD COLUMN") == 1, (
        "0022 adds one column; a second one landing here without its own "
        "rationale is the kind of drift this file exists to catch"
    )


def test_the_column_carries_its_comment():
    """A reader of the catalogue learns what NULL means without opening the repo."""
    assert "COMMENT ON COLUMN EVAL_RUNS.RESULT" in _executed("upgrade")


def test_the_config_column_is_left_alone():
    """0013's column keeps its meaning and its writer.

    `config` is the configuration tuple a run is an assertion ABOUT, written at
    INSERT and patched once by `update_eval_run_config`'s shallow `||` merge. The
    measurement goes beside it in its own column rather than inside it, so that
    merge can never half-overwrite a dataset.
    """
    assert "CONFIG" not in _executed("upgrade")


def test_eval_results_is_left_alone():
    """The per-scenario rows are slice 2's, and they are a different grain.

    This migration adds the RUN's record. `eval_results.binary_verdict`,
    `threshold` and `judge_identity` belong to the same ticket and arrive on
    their own revision, so a rollback of either does not take the other with it.
    """
    assert "EVAL_RESULTS" not in _executed("upgrade")


@pytest.mark.parametrize(
    "forbidden",
    ["UPDATE ", "DROP COLUMN", "DROP TABLE", "RENAME", "DELETE "],
)
def test_upgrade_is_strictly_additive(forbidden):
    """No backfill. There is no measurement to attribute to a historical run."""
    assert forbidden not in _executed("upgrade"), (
        f"0022 upgrade must not contain {forbidden!r}. It adds a column and "
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
    """eval_runs predates this migration by twenty-one revisions."""
    assert "DROP TABLE" not in _executed("downgrade")
