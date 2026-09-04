"""Tests for TENANT migration 0025, messages.seq (issue #79).

Named test_migration_tenant_0025 like its 0014 to 0024 siblings: the CONTROL-DB
tree numbers its revisions independently and a reader who assumes one tree looks
in the wrong directory.

What the migration is for, stated where its constraints are checked. `messages`
has carried `created_at TIMESTAMPTZ DEFAULT now()` and no tiebreaker since 0001.
`_persist_messages` writes a turn's user row and its assistant row in ONE
transaction, so `transaction_timestamp()` gives both the same `created_at`, and
`id` is a v4 uuid that sorts arbitrarily. Four readers ordered by `created_at`
alone and relied on user-before-assistant inside a turn. 0025 is the column that
makes that order a fact rather than a hope.

The constraints this file holds the migration to:

  - `seq` arrives NOT NULL with a sequence DEFAULT, so no row can be unplaceable
  - the backfill orders user before assistant inside one `created_at`
  - the backfill touches only rows with no `seq`, so a resumed run renumbers none
  - `messages` is the only table touched, in both directions
  - nothing pre-existing is dropped or renamed by upgrade
  - every downgrade statement is guarded

APPLIED AND VERIFIED 2026-09-04 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`). Observed:

    revision before                                0024
    revision after upgrade head                    0025
    messages.seq (type, nullable, default)         ('bigint', 'NO', "nextval('messages_seq_seq'::regclass)")
    index                                          CREATE UNIQUE INDEX messages_conversation_seq_idx ON public.messages USING btree (conversation_id, seq)
    revision after downgrade                       0024
    messages.seq after downgrade                   None
    index after downgrade                          None
    distinct created_at over 4 seeded rows         2
    revision after re-upgrade                      0025
    messages.seq (type, nullable, default)         ('bigint', 'NO', "nextval('messages_seq_seq'::regclass)")
    backfilled row                                 (5, 'user', 'first user')
    backfilled row                                 (6, 'assistant', 'first assistant')
    backfilled row                                 (7, 'user', 'second user')
    backfilled row                                 (8, 'assistant', 'second assistant')
    after a post-migration turn                    (9, 'user', 'third user')
    after a post-migration turn                    (10, 'assistant', 'third assistant')

The four rows were seeded WHILE THE DATABASE SAT AT 0024, a turn's pair per
transaction, and `distinct created_at over 4 seeded rows` reading 2 is the tie
this migration exists for, measured rather than assumed. The backfill starts at
5 because the probe database already held four older message rows, which is the
other thing that line shows: the backfill covers the table, not one conversation.

The assertions below keep a different job from that apply. They constrain what
the migration is allowed to CONTAIN, which a successful apply does not: a
migration can apply cleanly and still order its backfill by something that
freezes an arbitrary answer into a column that now looks authoritative.

The statements are read by patching `alembic.op.execute`, never by reading the
migration as text; the revision graph comes from the 0023 sibling's helper.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

import pytest

from tests.unit.test_migration_tenant_0023 import VERSIONS_DIR, _all_tenant_revisions

MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0025_messages_seq.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0025", MIGRATION_FILE)
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
    assert _load_migration().revision == "0025"


def test_migration_down_revision():
    assert _load_migration().down_revision == "0024"


def test_0025_is_the_sole_child_of_0024_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0024 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0025" in revisions, "0025 was not discovered in alembic_tenant/versions"

    children_of_0024 = [rev for rev, down in revisions.items() if down == "0024"]
    assert children_of_0024 == ["0025"], (
        f"0024 must have exactly one child, found {sorted(children_of_0024)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked. Expected a single head, got {sorted(heads)}"
    )


def test_0025_is_the_tenant_head():
    """Head IDENTITY, moved here from test_migration_tenant_0024.py.

    That file carried this assertion with a docstring saying 0025 would move this
    line and only this line, and it caught 0025 landing. Moving it is the
    instruction the test itself gives, and it is not the same as deleting it.

    0026 moves this line and only this line.
    """
    revisions = _all_tenant_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0025"}, (
        f"the tenant head is {sorted(heads)}, not 0025. If a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------


def test_upgrade_adds_seq_as_a_guarded_bigint():
    sql = _executed("upgrade")
    assert "ADD COLUMN IF NOT EXISTS SEQ BIGINT" in sql, (
        f"0025 must add seq as a guarded bigint: {sql}"
    )


def test_seq_is_not_null_with_a_sequence_default():
    """A nullable ordering column is the defect wearing a new name.

    Rows with a NULL `seq` land together at one end of every ORDER BY with no
    order among them, which is exactly what `created_at` ties were doing.
    """
    sql = _executed("upgrade")
    assert "ALTER COLUMN SEQ SET NOT NULL" in sql, f"seq must end up NOT NULL: {sql}"
    assert "SET DEFAULT NEXTVAL('MESSAGES_SEQ_SEQ')" in sql, (
        f"seq must take its value from the sequence, so no writer names it: {sql}"
    )


def test_the_sequence_starts_past_the_backfill():
    """setval before the DEFAULT is attached, or the first live INSERT collides
    with a backfilled row and the unique index rejects a customer's turn."""
    sql = _executed("upgrade")
    assert "SETVAL(" in sql, f"the sequence must be advanced past the backfill: {sql}"
    assert sql.index("SETVAL(") < sql.index("SET DEFAULT NEXTVAL"), (
        "setval must run BEFORE the DEFAULT is attached"
    )
    assert "MAX(SEQ)" in sql, "setval must read the highest backfilled value"


# ---------------------------------------------------------------------------
# The backfill
# ---------------------------------------------------------------------------


def test_the_backfill_puts_the_user_row_before_the_assistant_row():
    """The information the tie destroyed is gone, so the backfill reconstructs
    the order `_persist_messages` writes rather than freezing a plan's answer."""
    sql = _executed("upgrade")
    window = re.search(r"ROW_NUMBER\(\) OVER \( ORDER BY (.+?)\) AS POSITION", sql)
    assert window, f"the backfill must number rows with an explicit ORDER BY: {sql}"
    ordering = window.group(1)
    assert ordering.index("CREATED_AT") < ordering.index("CASE ROLE"), (
        f"created_at must lead; the role rank is the TIEBREAK: {ordering}"
    )
    assert "WHEN 'USER' THEN 0" in ordering and "WHEN 'ASSISTANT' THEN 1" in ordering, (
        f"the tiebreak must rank user before assistant: {ordering}"
    )
    assert ordering.rstrip().endswith("ID"), (
        f"id must be the last term so the backfill is repeatable: {ordering}"
    )


def test_the_backfill_only_touches_rows_with_no_seq():
    """A resumed or re-run upgrade must renumber nothing it already placed."""
    sql = _executed("upgrade")
    assert "WHERE SEQ IS NULL" in sql, (
        f"the backfill must be scoped to unplaced rows: {sql}"
    )


def test_the_read_path_index_is_unique_on_conversation_and_seq():
    """Every reader filters by conversation_id and orders by seq, and a duplicate
    seq inside a conversation is the original defect coming back."""
    sql = _executed("upgrade")
    assert "CREATE UNIQUE INDEX IF NOT EXISTS MESSAGES_CONVERSATION_SEQ_IDX" in sql
    assert "ON MESSAGES (CONVERSATION_ID, SEQ)" in sql


# ---------------------------------------------------------------------------
# What upgrade must never do
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden", ["DROP COLUMN", "DROP TABLE", "RENAME", "DELETE "])
def test_upgrade_destroys_nothing(forbidden):
    assert forbidden not in _executed("upgrade"), (
        f"0025 upgrade contains {forbidden!r}; it adds one column and fills it"
    )


def test_upgrade_touches_only_messages():
    sql = _executed("upgrade")
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql)) | set(
        re.findall(r"UPDATE\s+(\w+)", sql)
    )
    assert tables == {"MESSAGES"}, (
        f"0025 must touch messages and nothing else, found {sorted(tables)}"
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_removes_exactly_what_upgrade_added():
    sql = _executed("downgrade")
    assert "DROP COLUMN IF EXISTS SEQ" in sql
    assert "DROP INDEX IF EXISTS MESSAGES_CONVERSATION_SEQ_IDX" in sql
    assert "DROP SEQUENCE IF EXISTS MESSAGES_SEQ_SEQ" in sql


def test_every_downgrade_statement_is_guarded():
    """A downgrade that raises leaves a tenant half-migrated."""
    sql = _executed("downgrade")
    for statement in [s for s in sql.split(";") if "DROP" in s]:
        assert "IF EXISTS" in statement, (
            f"unguarded DROP in downgrade: {statement.strip()!r}"
        )


def test_downgrade_touches_only_messages():
    sql = _executed("downgrade")
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"MESSAGES"}, (
        f"downgrade must touch messages and nothing else, found {sorted(tables)}"
    )
