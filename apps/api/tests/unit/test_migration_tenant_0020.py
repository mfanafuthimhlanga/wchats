"""Tests for TENANT migration 0020, retrieval_metrics.judge_identity (#47, AC3).

Named test_migration_tenant_0020 rather than test_migration_0020 for the same
reason as its 0014-0019 siblings. The CONTROL-DB tree numbers its revisions
independently, so a reader who assumes one tree looks in the wrong directory.

What the migration is for: a calibration figure is measured against a named
Judge. `eval_results.detail` has carried that name for the four offline metrics
since #47's slice C, and the fifth Judge, the one that scores live traffic from
`retrieval_eval`, left its verdict in `retrieval_metrics.faithfulness` with
nothing saying which model, which effort or which prompt produced it. Two runs on
different Judges then read as one population.

WHY JSONB AND NOT THREE COLUMNS:
    `JudgeIdentity` is one value with three fields that are only ever read
    together, which is the grain #53's CalibrationStatus groups on. Three columns
    would let a row hold one field and not the others, and that partial key is
    exactly what the domain type refuses at construction.

WHY IT IS NULLABLE:
    Every row written before this migration has no Judge recorded, and so does
    every row whose faithfulness is NULL, because citation_coverage is arithmetic
    the task does itself. A verdict whose Judge is unknown is unknown.

HOW THE SQL IS READ HERE:
    By running upgrade() and downgrade() against a recording stand-in for
    `op.execute`, not by reading their source. That is the statement the
    migration actually issues, and it keeps this file off gates.py's
    SOURCE_ASSERTION_BASELINE.

APPLIED AND VERIFIED 2026-08-25 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`): 0019 to 0020
adds the column as jsonb, a row written through `_update_retrieval_metrics` reads
back as the three fields of the Judge that scored it, downgrade drops the column
and re-upgrade restores it.

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
MIGRATION_FILE = os.path.join(
    VERSIONS_DIR, "0020_retrieval_metrics_judge_identity.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0020", MIGRATION_FILE)
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
    assert _load_migration().revision == "0020"


def test_migration_down_revision():
    assert _load_migration().down_revision == "0019"


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


def test_0020_is_the_sole_child_of_0019_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0019 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0020" in revisions, "0020 was not discovered in alembic_tenant/versions"

    children_of_0019 = [rev for rev, down in revisions.items() if down == "0019"]
    assert children_of_0019 == ["0020"], (
        f"0019 must have exactly one child, found {sorted(children_of_0019)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked. Expected a single head, got {sorted(heads)}"
    )


# Head IDENTITY moved on to test_migration_tenant_0021.py when 0021 landed, which
# is what `test_0020_is_the_tenant_head` said would happen to it and the reason
# it went red the first time 0021 existed. The single-head and sole-child
# assertions above stay here; they are about a different failure.


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------


def test_upgrade_adds_the_column_as_jsonb():
    sql = _executed("upgrade")
    assert "JUDGE_IDENTITY JSONB" in sql, (
        "0020 must add judge_identity as jsonb: " + sql
    )
    assert "ADD COLUMN IF NOT EXISTS" in sql, (
        "the add must be guarded so a re-run is a no-op, matching 0017 and 0018"
    )


def test_the_column_is_nullable_and_takes_no_default():
    """Every pre-0020 row has no Judge, and so does every row with no verdict.

    NOT NULL would refuse both, and a default filling them would name a Judge
    that never ran.
    """
    sql = _executed("upgrade")
    assert "NOT NULL" not in sql
    assert "DEFAULT" not in sql


def test_upgrade_touches_only_retrieval_metrics():
    sql = _executed("upgrade")
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"RETRIEVAL_METRICS"}, (
        f"0020 must touch retrieval_metrics and nothing else, found {sorted(tables)}"
    )


def test_upgrade_adds_exactly_one_column():
    sql = _executed("upgrade")
    assert sql.count("ADD COLUMN") == 1, (
        "0020 adds one column; a second one landing here without its own "
        "rationale is the kind of drift this file exists to catch"
    )


def test_the_column_carries_its_comment():
    """A reader of the catalogue learns what NULL means without opening the repo."""
    sql = _executed("upgrade")
    assert "COMMENT ON COLUMN RETRIEVAL_METRICS.JUDGE_IDENTITY" in sql


@pytest.mark.parametrize(
    "forbidden",
    ["UPDATE ", "DROP COLUMN", "DROP TABLE", "RENAME", "DELETE "],
)
def test_upgrade_is_strictly_additive(forbidden):
    """No backfill. There is no Judge to attribute a historical row to."""
    assert forbidden not in _executed("upgrade"), (
        f"0020 upgrade must not contain {forbidden!r}. It adds a column and "
        "nothing else"
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_added():
    sql = _executed("downgrade")
    dropped = set(re.findall(r"DROP COLUMN IF EXISTS\s+(\w+)", sql))
    assert dropped == {"JUDGE_IDENTITY"}, (
        f"0020 downgrade must drop judge_identity alone, found {sorted(dropped)}"
    )


def test_downgrade_drops_no_table():
    """retrieval_metrics predates this migration by ten revisions."""
    assert "DROP TABLE" not in _executed("downgrade")


# ---------------------------------------------------------------------------
# The writer and the column agree
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, statements):
        self.statements = statements

    def execute(self, sql, params):
        self.statements.append(sql)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, statements):
        self.statements = statements

    def cursor(self):
        return _Cursor(self.statements)

    def commit(self):
        return None

    def close(self):
        return None


def test_the_task_writes_the_column_this_migration_adds():
    """A column nothing writes is decoration, and a write with no column raises.

    The task's UPDATE is read off the statement it issues, through a recording
    stand-in for the connection, the same way the migration is read above.
    """
    from app.worker.tasks.runtime import retrieval_eval

    statements: list[str] = []
    with patch.object(
        retrieval_eval.psycopg2, "connect", return_value=_Conn(statements)
    ):
        retrieval_eval._update_retrieval_metrics(
            "postgresql://probe", "job-1", 0.5, 0.9, {"model": "gpt-5.6-luna"}
        )

    assert "judge_identity" in statements[0], statements[0]
