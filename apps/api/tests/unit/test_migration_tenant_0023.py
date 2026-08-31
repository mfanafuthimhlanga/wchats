"""Tests for TENANT migration 0023, the eval_results judge row (ticket 14, #51).

Named test_migration_tenant_0023 rather than test_migration_0023 for the same
reason as its 0014 to 0022 siblings: the CONTROL-DB tree numbers its revisions
independently and a reader who assumes one tree looks in the wrong directory.

What the migration is for, stated where its constraints are checked. A judge row
carried a score and a `detail` blob that repeated all four of the scenario's
scores, and nothing on the row said whether the score cleared its gate, which
gate, which Judge, or what the verdict cost. `api/v1/evals.py` rebuilt the
comparison from `settings` at read time, so raising a threshold restated every
historical verdict. These four columns put the decision on the row that made it.

The constraints this file holds the migration to:

  - additive columns only: no DROP or RENAME of anything pre-existing
  - nullable only:         no NOT NULL, no DEFAULT, no backfill. NULL is what
                           every pre-0023 row honestly says, and for
                           `binary_verdict` NULL is specifically not False
  - one table touched:     eval_results, nothing else
  - `eval_runs` is left alone: 0022's run record is a different grain and a
                           rollback of either must not take the other with it
  - downgrade drops only what upgrade added, every statement IF EXISTS

APPLIED AND VERIFIED 2026-08-30 against the local `wchats_tenant_probe` cluster
through the production path (`app.services.migrations.run_tenant_migrations`):
0022 to 0023, the four columns arrive `boolean` / `numeric` / `jsonb` / `text`,
all nullable with no DEFAULT, downgrade drops them, re-upgrade restores them. So
the assertions below are not the only evidence, and they keep a different job:
they constrain what the migration is allowed to CONTAIN, which a successful apply
does not.

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
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0023_eval_result_judge_row.py")

#: The four columns 0023 adds, and the type each one must arrive as.
ADDED_COLUMNS = {
    "BINARY_VERDICT": "BOOLEAN",
    "THRESHOLD": "NUMERIC",
    "JUDGE_IDENTITY": "JSONB",
    "LEDGER_PURPOSE": "TEXT",
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0023", MIGRATION_FILE)
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


def _ddl(direction: str) -> str:
    """The ALTER statements alone, with the COMMENT prose dropped.

    A `COMMENT ON COLUMN` body is English, and English contains the words DEFAULT
    and DETAIL. Every check about what the migration DOES runs against this
    rather than against `_executed`, so a comment that explains the absence of a
    default cannot be read as the presence of one.
    """
    return " ; ".join(
        s for s in _executed(direction).split(" ; ") if not s.startswith("COMMENT")
    )


# ---------------------------------------------------------------------------
# Identity and parentage
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), f"missing migration: {MIGRATION_FILE}"


def test_migration_revision():
    assert _load_migration().revision == "0023"


def test_migration_down_revision():
    assert _load_migration().down_revision == "0022"


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


def test_0023_is_the_sole_child_of_0022_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0022 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0023" in revisions, "0023 was not discovered in alembic_tenant/versions"

    children_of_0022 = [rev for rev, down in revisions.items() if down == "0022"]
    assert children_of_0022 == ["0023"], (
        f"0022 must have exactly one child, found {sorted(children_of_0022)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked. Expected a single head, got {sorted(heads)}"
    )


# ---------------------------------------------------------------------------
# The columns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column,sql_type", sorted(ADDED_COLUMNS.items()))
def test_upgrade_adds_each_column_with_its_type(column, sql_type):
    """The type is the claim. A verdict stored as text sorts and groups wrong."""
    sql = _ddl("upgrade")
    assert f"{column} {sql_type}" in sql, (
        f"0023 must add {column} as {sql_type}: {sql}"
    )


def test_every_add_is_guarded():
    sql = _ddl("upgrade")
    assert sql.count("ADD COLUMN IF NOT EXISTS") == len(ADDED_COLUMNS), (
        "each add must be guarded so a re-run is a no-op, matching 0017 to 0022"
    )


def test_upgrade_adds_exactly_these_four_columns():
    sql = _ddl("upgrade")
    added = {
        m.upper() for m in re.findall(r"ADD COLUMN IF NOT EXISTS\s+(\w+)", sql)
    }
    assert added == set(ADDED_COLUMNS), (
        f"0023 adds {sorted(ADDED_COLUMNS)}; found {sorted(added)}. A fifth "
        "column landing here without its own rationale is the drift this file "
        "exists to catch"
    )


def test_the_columns_are_nullable_and_take_no_default():
    """NULL is what every pre-0023 row honestly says, on all four.

    For `binary_verdict` the rule is sharper than nullability. A DEFAULT FALSE
    would say every historical row FAILED its gate, and a run that was never
    gated would read as a run that was gated and lost. NOT NULL would refuse the
    two metrics that have no threshold at all.
    """
    sql = _ddl("upgrade")
    assert "NOT NULL" not in sql
    assert "DEFAULT" not in sql


def test_upgrade_touches_only_eval_results():
    sql = _ddl("upgrade")
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"EVAL_RESULTS"}, (
        f"0023 must touch eval_results and nothing else, found {sorted(tables)}"
    )


def test_eval_runs_is_left_alone():
    """0022's run record is a different grain and rolls back separately.

    `eval_runs.result` is what the RUN measured; these four are what one
    (scenario, metric) row decided. Putting both on one revision would make a
    rollback of either take the other with it.
    """
    assert "EVAL_RUNS" not in _ddl("upgrade")


@pytest.mark.parametrize("column", sorted(ADDED_COLUMNS))
def test_each_column_carries_its_comment(column):
    """A reader of the catalogue learns what NULL means without opening the repo.

    `ledger_purpose` is the one that most needs it. Its grain is the metric
    within the run and not the scenario, and a reader who assumes otherwise
    attributes one scenario's judge spend to forty.
    """
    assert f"COMMENT ON COLUMN EVAL_RESULTS.{column}" in _executed("upgrade")


def test_the_detail_column_is_not_dropped_by_this_migration():
    """The blob stops being WRITTEN in this ticket; the column stays.

    `write_eval_results` stops filling `detail` with the four-score copy, which
    is an application change and reversible by deploying the previous build.
    Dropping the column here would destroy every historical row's blob on a
    forward migration and give the rollback nothing to restore.
    """
    assert "DETAIL" not in _ddl("upgrade")


@pytest.mark.parametrize(
    "forbidden",
    ["UPDATE ", "DROP COLUMN", "DROP TABLE", "RENAME", "DELETE "],
)
def test_upgrade_is_strictly_additive(forbidden):
    """No backfill. A historical row's verdict cannot be recovered.

    Recomputing it from today's `settings` is exactly the read-time comparison
    these columns exist to replace, and doing it in a migration would write the
    wrong answer down permanently.
    """
    assert forbidden not in _ddl("upgrade"), (
        f"0023 upgrade must not contain {forbidden!r}. It adds four columns and "
        "comments them, and nothing else"
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_added():
    sql = _ddl("downgrade")
    dropped = {m.upper() for m in re.findall(r"DROP COLUMN IF EXISTS\s+(\w+)", sql)}
    assert dropped == set(ADDED_COLUMNS), (
        f"downgrade must drop {sorted(ADDED_COLUMNS)}, found {sorted(dropped)}"
    )


def test_every_downgrade_statement_is_guarded():
    """A downgrade that raises leaves a tenant half-migrated."""
    sql = _executed("downgrade")
    for statement in [s for s in sql.split(";") if "DROP" in s]:
        assert "IF EXISTS" in statement, (
            f"unguarded DROP in downgrade: {statement.strip()!r}"
        )


def test_downgrade_drops_no_table():
    """eval_results predates this migration by twenty-two revisions."""
    assert "DROP TABLE" not in _ddl("downgrade")


def test_downgrade_leaves_score_and_metric_alone():
    """The scores survive a rollback; only the decision about them goes."""
    sql = _ddl("downgrade")
    dropped = {m.upper() for m in re.findall(r"DROP COLUMN IF EXISTS\s+(\w+)", sql)}
    assert not dropped & {"SCORE", "METRIC", "SCENARIO_ID", "DETAIL", "EVAL_RUN_ID"}
