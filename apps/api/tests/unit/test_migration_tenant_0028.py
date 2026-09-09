"""Tests for TENANT migration 0028, a scenario's conversation (issue #227).

Named test_migration_tenant_0028 like its 0014 to 0027 siblings: the CONTROL-DB
tree numbers its revisions independently and a reader who assumes one tree looks
in the wrong directory.

What the migration is for, stated where its constraints are checked. An eval
scenario was a question and a reference answer, and the eval turn ran with an
empty history, so the only customer message the agent ever saw was the first one
of a conversation. Nothing held the earlier turns, so a question bound by an
earlier message ("how do I start the dev server", after the customer named the
project) could not be sent, tested or scored.

The constraints this file holds the migration to:

  - four columns, two tables, and nothing else touched in either direction
  - every NOT NULL column arrives with a DEFAULT, because a NOT NULL column added
    without one fails on a table that already holds rows
  - `resolved_question` is NULLABLE, and null is a real state: the resolution
    step is a model call, and a failed one scores the raw question rather than
    inventing a reference
  - downgrade drops exactly the four columns upgrade created, guarded

APPLIED AND VERIFIED 2026-09-09 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`). Observed:

    revision before                                      0027, columns 0
    revision after upgrade head                          0028, columns 4
    columns (table.name, type, nullable, default)
        eval_samples.resolved_question   text    YES  None
        eval_samples.turns               jsonb   NO   '[]'::jsonb
        eval_scenarios.ambiguous         boolean NO   false
        eval_scenarios.turns             jsonb   NO   '[]'::jsonb
    column comments                                      all four present
    revision after downgrade                             0027, columns 0
    revision after re-upgrade                            0028, columns 4

Both tables were EMPTY in that run, so it did not exercise the case every tenant
presents. A second run seeded one `eval_scenarios` row and one `eval_samples` row
at 0027 and carried them up. Observed: the upgrade returned with no error, and
the pre-existing rows read `turns = []`, `ambiguous = false`,
`resolved_question = NULL`. Both runs are written up in
`.dev/reference/260909-tenant-0028-observed.md`.

The statements are read by patching `alembic.op.execute`, never by reading the
migration as text; the revision graph comes from the 0023 sibling's helper.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

from tests.unit.test_migration_tenant_0023 import VERSIONS_DIR, _all_tenant_revisions

MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0028_eval_scenario_turns.py")

#: The columns 0028 adds, and the declaration each must arrive with.
COLUMNS = {
    ("EVAL_SCENARIOS", "TURNS"): "JSONB NOT NULL DEFAULT '[]'::JSONB",
    ("EVAL_SCENARIOS", "AMBIGUOUS"): "BOOLEAN NOT NULL DEFAULT FALSE",
    ("EVAL_SAMPLES", "TURNS"): "JSONB NOT NULL DEFAULT '[]'::JSONB",
    ("EVAL_SAMPLES", "RESOLVED_QUESTION"): "TEXT",
}

#: The two tables 0028 is allowed to name.
TABLES = {"EVAL_SCENARIOS", "EVAL_SAMPLES"}


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0028", MIGRATION_FILE)
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


def _ddl_only(statement: str) -> str:
    """The statement with quoted prose blanked, so a comment body names no table."""
    return re.sub(r"'(?:[^']|'')*'", "''", statement)


# ---------------------------------------------------------------------------
# Identity and parentage
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), f"missing migration: {MIGRATION_FILE}"


def test_migration_revision():
    assert _load_migration().revision == "0028"


def test_migration_down_revision():
    assert _load_migration().down_revision == "0027"


def test_0028_is_the_sole_child_of_0027_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0027 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0028" in revisions, "0028 was not discovered in alembic_tenant/versions"
    children_of_0027 = [rev for rev, down in revisions.items() if down == "0027"]
    assert children_of_0027 == ["0028"], (
        f"0027 must have exactly one child, found {sorted(children_of_0027)}"
    )
    parents = [down for down in revisions.values() if down is not None]
    assert len(parents) == len(set(parents)), "two revisions share a parent"


def test_0028_is_the_tenant_head():
    """Head IDENTITY, moved here from test_migration_tenant_0027.py.

    That file carried this assertion with a docstring saying 0028 would move this
    line and only this line, and it caught 0028 landing. Moving it is the
    instruction the test itself gives, and it is not the same as deleting it.

    0029 moves this line and only this line.
    """
    revisions = _all_tenant_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0028"}, (
        f"the tenant head is {sorted(heads)}, not 0028. If a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def test_upgrade_adds_every_column_to_its_own_table():
    joined = " ; ".join(_statements("upgrade"))
    for (table, column), spec in COLUMNS.items():
        assert re.search(
            rf"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}\s+{re.escape(spec)}",
            joined,
        ), f"{table.lower()}.{column.lower()} must be added as {spec}"


def test_every_not_null_column_arrives_with_a_default():
    """A NOT NULL column added without a default fails on a table holding rows.

    `eval_scenarios` holds the corpus and `eval_samples` holds every run's scored
    text, so both are populated on every tenant this migration reaches. The
    defaults also describe the existing rows correctly, which is why there is no
    backfill: a scenario written before 0028 has no prior turns and is not
    ambiguous, because the eval that wrote it could express neither.
    """
    checked = [
        statement
        for statement in _statements("upgrade")
        if "ADD COLUMN" in statement and "NOT NULL" in statement
    ]
    # THE FLOOR, because a loop over nothing passes. Three of the four columns
    # are NOT NULL; a rename that stopped this filter matching would otherwise
    # leave the assertion below with no subject and the test green (FM-025).
    assert len(checked) == 3, (
        f"expected 3 NOT NULL columns to check, found {len(checked)}. Either the "
        "migration changed or this filter stopped matching its statements"
    )
    for statement in checked:
        assert "DEFAULT" in statement, (
            f"a NOT NULL column is added with no default: {statement}"
        )


def test_resolved_question_is_nullable():
    """Null is a real state on this column, not an unset one.

    The resolution step is a model call, and a failure leaves the row scored on
    its raw question and marked. NOT NULL with a default would make "never
    resolved" and "resolved to the empty string" the same row.
    """
    joined = " ; ".join(_statements("upgrade"))
    match = re.search(r"ADD COLUMN IF NOT EXISTS RESOLVED_QUESTION\s+([^;]*)", joined)
    assert match, "resolved_question is not added at all"
    assert "NOT NULL" not in match.group(1), match.group(1)


def test_upgrade_comments_every_column_it_adds():
    """A column nobody can read the meaning of is a column somebody guesses at.

    `turns` on two tables is the case that needs it: on `eval_scenarios` it is
    what the author wrote, and on `eval_samples` it is what the agent was given.
    """
    joined = " ; ".join(_statements("upgrade"))
    for table, column in COLUMNS:
        assert f"COMMENT ON COLUMN {table}.{column} IS" in joined, (
            f"{table.lower()}.{column.lower()} was added without a comment"
        )


def test_upgrade_touches_no_other_table():
    for statement in _statements("upgrade"):
        ddl = _ddl_only(statement)
        named = set(re.findall(r"\bTABLE\s+(?:IF NOT EXISTS\s+)?([A-Z_]+)", ddl))
        named |= set(re.findall(r"\bCOLUMN\s+([A-Z_]+)\.", ddl))
        named -= TABLES
        assert not named, f"upgrade names a table outside {sorted(TABLES)}: {statement}"


def test_upgrade_creates_and_drops_nothing():
    """Four columns is the whole migration.

    A CREATE or a DROP here would be a second change riding along with the one
    the revision is named for, and the downgrade below only reverses columns.
    """
    for statement in _statements("upgrade"):
        ddl = _ddl_only(statement)
        assert not re.search(r"\b(CREATE|DROP|TRUNCATE|DELETE|UPDATE)\b", ddl), statement


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_the_columns_upgrade_created():
    statements = _statements("downgrade")
    assert statements == [
        "ALTER TABLE EVAL_SCENARIOS DROP COLUMN IF EXISTS TURNS",
        "ALTER TABLE EVAL_SCENARIOS DROP COLUMN IF EXISTS AMBIGUOUS",
        "ALTER TABLE EVAL_SAMPLES DROP COLUMN IF EXISTS TURNS",
        "ALTER TABLE EVAL_SAMPLES DROP COLUMN IF EXISTS RESOLVED_QUESTION",
    ], statements


def test_every_downgrade_statement_is_guarded():
    for statement in _statements("downgrade"):
        assert "IF EXISTS" in statement, statement


def test_downgrade_reverses_every_column_upgrade_added():
    """The pair, matched by name, so a fifth column cannot arrive one-way.

    A column added in upgrade and forgotten in downgrade leaves a tenant that
    rolled back carrying a column no revision admits to, which the next upgrade
    then meets with ADD COLUMN.
    """
    added = {
        (m.group(1), m.group(2))
        for statement in _statements("upgrade")
        for m in [re.search(r"ALTER TABLE ([A-Z_]+) ADD COLUMN IF NOT EXISTS ([A-Z_]+)", statement)]
        if m
    }
    dropped = {
        (m.group(1), m.group(2))
        for statement in _statements("downgrade")
        for m in [re.search(r"ALTER TABLE ([A-Z_]+) DROP COLUMN IF EXISTS ([A-Z_]+)", statement)]
        if m
    }
    # Two empty sets are equal, so the comparison alone passes when neither
    # regex matches anything. The count is what makes it an observation (FM-004).
    assert len(added) == 4, f"read {sorted(added)} added columns, expected 4"
    assert added == dropped, f"added {sorted(added)}, dropped {sorted(dropped)}"
