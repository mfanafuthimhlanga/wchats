"""Tests for TENANT migration 0026, retrieval_metrics.context_source (issue #120).

Named test_migration_tenant_0026 like its 0014 to 0025 siblings: the CONTROL-DB
tree numbers its revisions independently and a reader who assumes one tree looks
in the wrong directory.

What the migration is for, stated where its constraints are checked.
`retrieval_metrics.faithfulness` holds scores from three instruments — the SDK-era
repr proxy, the post-#48 `wire_text` proxy, and the retrieved chunks themselves
from 280ff05 — and nothing on the row said which. `judge_identity` (0020) names
the MODEL, not the shape of the text the model was shown, and it is NULL for
every row written before it, so it separates the eras only by accident. 0026 is
the column that says what a score was computed over.

The constraints this file holds the migration to:

  - the column is nullable with no DEFAULT and NO backfill: NULL is the honest
    reading of the history, and a guessed label looks authoritative
  - `retrieval_metrics` is the only table touched, in both directions
  - nothing pre-existing is dropped or renamed
  - downgrade drops exactly the column upgrade added, guarded

THE ROW COUNT #120 ASKS FOR FIRST. Counted 2026-09-04 on the local
`wchats_tenant_probe` cluster:

    retrieval_metrics rows total                         0
    rows carrying a faithfulness score                   0

The probe cluster is disposable and holds no tenant traffic, so that zero is a
fact about this machine and NOT the "close it as a no-op" the issue describes.
Live rows live in per-tenant Neon projects, which are unreachable from here
without the production control DB. The column lands because the history it marks
cannot be counted from this machine, not because it was counted and found.

APPLIED AND VERIFIED 2026-09-04 against the same cluster through the production
path (`migrations.run_tenant_migrations`). Observed:

    revision before                                      0025
    revision after upgrade head                          0026
    context_source (type, nullable, default)             ('text', 'YES', None)
    column comment                                       Issue #120. The shape of the text this row's faithfulness sc...
    revision after downgrade                             0025
    context_source after downgrade                       None
    revision after re-upgrade                            0026
    context_source (type, nullable, default)             ('text', 'YES', None)
    the pre-0026 scored row                              (Decimal('0.42'), None)
    after a post-0026 score                              (Decimal('0.91'), 'agent_retrieve_chunks/1')

The scored row was written WHILE THE DATABASE SAT AT 0025, which is the
unlabelled history this column exists to mark, and it stayed NULL through the
upgrade. The line under it is `_update_retrieval_metrics` writing a new score
against the same row.

The statements are read by patching `alembic.op.execute`, never by reading the
migration as text; the revision graph comes from the 0023 sibling's helper.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

import pytest

from app.domain.eval_result import CONTEXT_PROXY_VERSION
from tests.unit.test_migration_tenant_0023 import VERSIONS_DIR, _all_tenant_revisions

MIGRATION_FILE = os.path.join(
    VERSIONS_DIR, "0026_retrieval_metrics_context_source.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0026", MIGRATION_FILE)
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
    assert _load_migration().revision == "0026"


def test_migration_down_revision():
    assert _load_migration().down_revision == "0025"


def test_0026_is_the_sole_child_of_0025_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0025 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0026" in revisions, "0026 was not discovered in alembic_tenant/versions"

    children_of_0025 = [rev for rev, down in revisions.items() if down == "0025"]
    assert children_of_0025 == ["0026"], (
        f"0025 must have exactly one child, found {sorted(children_of_0025)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked. Expected a single head, got {sorted(heads)}"
    )


def test_0026_is_the_tenant_head():
    """Head IDENTITY, moved here from test_migration_tenant_0025.py.

    That file carried this assertion with a docstring saying 0026 would move this
    line and only this line, and it caught 0026 landing. Moving it is the
    instruction the test itself gives, and it is not the same as deleting it.

    0027 moves this line and only this line.
    """
    revisions = _all_tenant_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0026"}, (
        f"the tenant head is {sorted(heads)}, not 0026. If a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------


def test_upgrade_adds_the_column_as_guarded_text():
    sql = _executed("upgrade")
    assert "ADD COLUMN IF NOT EXISTS CONTEXT_SOURCE TEXT" in sql, (
        f"0026 must add context_source as a guarded text column: {sql}"
    )


def test_upgrade_adds_exactly_one_column():
    sql = _executed("upgrade")
    assert sql.count("ADD COLUMN") == 1, (
        "0026 adds one column; a second landing here without its own rationale "
        "is the drift this file exists to catch"
    )


def test_the_column_is_nullable_with_no_default_and_no_backfill():
    """NULL reads as "one of the two proxies, shape unknown", which is what the
    history is. The information needed to tell those two apart is not on the row,
    so any backfill would guess, and a guessed label looks authoritative.
    """
    sql = _executed("upgrade")
    for forbidden in ("NOT NULL", "DEFAULT", "UPDATE ", "SET CONTEXT_SOURCE"):
        assert forbidden not in sql, (
            f"0026 upgrade contains {forbidden!r}; the column arrives NULL and "
            f"stays NULL for every row written before it: {sql}"
        )


def test_the_comment_says_what_null_means():
    """A reader who finds NULL must not read it as "no context"."""
    sql = _executed("upgrade")
    assert "COMMENT ON COLUMN RETRIEVAL_METRICS.CONTEXT_SOURCE" in sql
    assert "NULL MEANS" in sql, f"the comment must say what NULL is: {sql}"


def test_the_write_path_constant_is_the_one_the_offline_record_uses():
    """One shape, one string. The live Judge's row and the offline eval record
    name it from the same constant, or a reader comparing them compares two
    vocabularies."""
    from app.worker.tasks.runtime import retrieval_eval

    assert retrieval_eval.CONTEXT_PROXY_VERSION is CONTEXT_PROXY_VERSION


# ---------------------------------------------------------------------------
# What upgrade must never do
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden", ["DROP COLUMN", "DROP TABLE", "RENAME", "DELETE "])
def test_upgrade_destroys_nothing(forbidden):
    assert forbidden not in _executed("upgrade"), (
        f"0026 upgrade contains {forbidden!r}; it adds one nullable column"
    )


def test_upgrade_touches_only_retrieval_metrics():
    sql = _executed("upgrade")
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"RETRIEVAL_METRICS"}, (
        f"0026 must touch retrieval_metrics and nothing else, found {sorted(tables)}"
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_added():
    sql = _executed("downgrade")
    dropped = set(re.findall(r"DROP COLUMN IF EXISTS\s+(\w+)", sql))
    assert dropped == {"CONTEXT_SOURCE"}, (
        f"downgrade must drop exactly the column upgrade added, found {sorted(dropped)}"
    )


def test_every_downgrade_statement_is_guarded():
    """A downgrade that raises leaves a tenant half-migrated."""
    sql = _executed("downgrade")
    for statement in [s for s in sql.split(";") if "DROP" in s]:
        assert "IF EXISTS" in statement, (
            f"unguarded DROP in downgrade: {statement.strip()!r}"
        )


def test_downgrade_touches_only_retrieval_metrics():
    sql = _executed("downgrade")
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"RETRIEVAL_METRICS"}, (
        f"downgrade must touch retrieval_metrics and nothing else, found {sorted(tables)}"
    )
