"""Tests for TENANT migration 0027, the eval_samples table (issue #58).

Named test_migration_tenant_0027 like its 0014 to 0026 siblings: the CONTROL-DB
tree numbers its revisions independently and a reader who assumes one tree looks
in the wrong directory.

What the migration is for, stated where its constraints are checked.
`eval_results` holds one score per (scenario, metric) and nothing about the text
the score is about. The calibration harness needs the agent's answer, the chunks
it retrieved and the reference in front of a human beside the Judge's verdict,
and until 0027 those four strings existed only in memory while Ragas ran.

The constraints this file holds the migration to:

  - one new table, `eval_samples`, and nothing else touched in either direction
  - the four scored strings are NOT NULL: a row missing one describes nothing
    Ragas scored
  - `eval_run_id` is indexed, because every reader starts from a run
  - downgrade drops exactly the table upgrade created, guarded

APPLIED AND VERIFIED 2026-09-07 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`). Observed:

    revision before                                      0026
    revision after upgrade head                          0027
    columns (name, type, nullable, default)
        id                  uuid                     NO   None
        eval_run_id         uuid                     NO   None
        scenario_id         text                     NO   None
        dataset             text                     YES  None
        user_input          text                     NO   None
        response            text                     NO   None
        retrieved_contexts  jsonb                    NO   None
        reference           text                     NO   None
        created_at          timestamp with time zone NO   now()
    indexes                                              eval_samples_pkey,
                                                         ix_eval_samples_eval_run_id
    table comment                                        The four strings Ragas scored for one scenario of one eval run, writte...
    revision after downgrade                             0026, columns 0
    revision after re-upgrade                            0027, columns 9

The statements are read by patching `alembic.op.execute`, never by reading the
migration as text; the revision graph comes from the 0023 sibling's helper.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

from tests.unit.test_migration_tenant_0023 import VERSIONS_DIR, _all_tenant_revisions

MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0027_eval_samples.py")

#: The columns 0027 creates, and the nullability each must arrive with.
COLUMNS = {
    "ID": "UUID PRIMARY KEY",
    "EVAL_RUN_ID": "UUID NOT NULL",
    "SCENARIO_ID": "TEXT NOT NULL",
    "DATASET": "TEXT",
    "USER_INPUT": "TEXT NOT NULL",
    "RESPONSE": "TEXT NOT NULL",
    "RETRIEVED_CONTEXTS": "JSONB NOT NULL",
    "REFERENCE": "TEXT NOT NULL",
    "CREATED_AT": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0027", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(direction: str) -> list[str]:
    """Every statement the named direction issues, whitespace-collapsed, upper."""
    module = _load_migration()
    statements: list[str] = []
    with patch("alembic.op.execute", side_effect=statements.append):
        getattr(module, direction)()
    assert statements, f"{direction}() issued no statement at all"
    return [" ".join(str(s).split()).upper() for s in statements]


# ---------------------------------------------------------------------------
# Identity and parentage
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), f"missing migration: {MIGRATION_FILE}"


def test_migration_revision():
    assert _load_migration().revision == "0027"


def test_migration_down_revision():
    assert _load_migration().down_revision == "0026"


def test_0027_is_the_sole_child_of_0026_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0026 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0027" in revisions, "0027 was not discovered in alembic_tenant/versions"
    children_of_0026 = [rev for rev, down in revisions.items() if down == "0026"]
    assert children_of_0026 == ["0027"], (
        f"0026 must have exactly one child, found {sorted(children_of_0026)}"
    )
    parents = [down for down in revisions.values() if down is not None]
    assert len(parents) == len(set(parents)), "two revisions share a parent"


def test_0027_is_no_longer_the_tenant_head_and_0028_took_the_assertion():
    """The head assertion left this file for test_migration_tenant_0028.py.

    It said "0028 moves this line and only this line", 0028 landed, and it moved.
    What stays here is the half that is still about 0027: this revision is no
    longer the end of the tree, and the assertion naming the new end lives with
    the revision that took it.
    """
    revisions = _all_tenant_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads != {"0027"}, (
        "0027 is the head again. Either 0028 was removed, in which case this "
        "assertion comes back here, or the tree forked"
    )
    assert "0027" in parents, "0027 lost its child; 0028 must descend from it"


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def test_upgrade_creates_eval_samples_with_every_column():
    joined = " ; ".join(_statements("upgrade"))
    assert "CREATE TABLE IF NOT EXISTS EVAL_SAMPLES" in joined
    for name, spec in COLUMNS.items():
        assert re.search(rf"\b{name}\s+{re.escape(spec)}", joined), (
            f"eval_samples.{name.lower()} must be declared as {spec}"
        )


def test_upgrade_indexes_the_run_id():
    joined = " ; ".join(_statements("upgrade"))
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS IX_EVAL_SAMPLES_EVAL_RUN_ID ON EVAL_SAMPLES \(EVAL_RUN_ID\)",
        joined,
    )


def test_upgrade_touches_no_other_table():
    for statement in _statements("upgrade"):
        # Comment bodies are prose; only the DDL outside quotes names tables.
        ddl = re.sub(r"'(?:[^']|'')*'", "''", statement)
        named = set(re.findall(r"\bTABLE\s+(?:IF NOT EXISTS\s+)?([A-Z_]+)", ddl))
        named |= set(re.findall(r"\bON\s+([A-Z_]+)\s*\(", ddl))
        named |= set(re.findall(r"\bCOLUMN\s+([A-Z_]+)\.", ddl))
        named.discard("EVAL_SAMPLES")
        assert not named, f"upgrade names a table other than eval_samples: {statement}"


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_the_table_upgrade_created():
    statements = _statements("downgrade")
    assert statements == ["DROP TABLE IF EXISTS EVAL_SAMPLES"], statements


def test_every_downgrade_statement_is_guarded():
    for statement in _statements("downgrade"):
        assert "IF EXISTS" in statement, statement
