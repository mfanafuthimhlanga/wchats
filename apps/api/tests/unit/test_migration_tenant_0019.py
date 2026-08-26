"""Tests for TENANT migration 0019, the model_calls ledger (ticket #46, issue #22).

Named test_migration_tenant_0019 rather than test_migration_0019 for the same
reason as its 0014-0018 siblings. The CONTROL-DB tree numbers its revisions
independently, so a reader who assumes one tree will look in the wrong directory.

What the migration is for: ten call sites build an anthropic client, read the text
back, and throw `usage` and `model` away. `turn_metrics.cost_usd` then carried the
CLI's Anthropic-book figure for calls DeepSeek served, so the first Harness run
could not be priced at all. This table is where one call's token counts are
written down.

WHY THERE IS NO MONEY COLUMN:
    Money is derived at read time by `app.domain.pricing` against a versioned book.
    A stored cost freezes yesterday's price into a row that a corrected book can
    never reach, and correcting the book is the whole reason the book is versioned.

WHY `at` TAKES NO DEFAULT, where `created_at` on turn_metrics does:
    The writer always knows when the call happened, because the response hook
    stamps it. A DEFAULT now() would let a row that lost its instant read as though
    it happened at insert time, and pricing reads the peak window off that instant.
    A wrong window prices a call at a fifth or at five times what it cost.

WHY THE IDS ARE TEXT, where turn_metrics.agent_id is UUID:
    The ledger is fail open by design. A recording failure logs and lets the model
    call succeed, so a column type that can refuse a value `ModelCall` accepts turns
    a recordable call into a silently lost row. `ModelCall` guarantees a non-empty
    string and nothing narrower, and this column accepts exactly that.

HOW THE SQL IS READ HERE:
    By running upgrade() and downgrade() against a recording stand-in for
    `op.execute`, not by reading their source. That is the statement the migration
    actually issues, and it keeps this file off gates.py's
    SOURCE_ASSERTION_BASELINE, which exists to stop tests asserting on characters
    of source.

APPLIED AND VERIFIED 2026-08-25 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`): 0018 to 0019, the
table arrives with fourteen columns and two indexes, a seeded row written by
`record_model_call` reads back with its counts intact, downgrade drops the table,
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
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0019_model_calls.py")

#: Every column `app.core.model_client.record_model_call` names in its INSERT.
LEDGER_COLUMNS = (
    "PURPOSE",
    "PROVIDER",
    "REQUESTED_MODEL",
    "SERVED_MODEL",
    "MODEL_SOURCE",
    "INPUT_TOKENS",
    "OUTPUT_TOKENS",
    "CACHE_READ_TOKENS",
    "CACHE_CREATION_TOKENS",
    "AT",
    "TENANT_ID",
    "AGENT_ID",
    "JOB_ID",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0019", MIGRATION_FILE)
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
    mod = _load_migration()
    assert mod.revision == "0019", f"Expected revision '0019', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0018", (
        f"Expected down_revision '0018', got {mod.down_revision!r}"
    )


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


def test_0019_is_the_sole_child_of_0018_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0018 breaks every subsequent tenant provision.
    """
    revisions = _all_tenant_revisions()
    assert "0019" in revisions, "0019 was not discovered in alembic_tenant/versions"

    children_of_0018 = [rev for rev, down in revisions.items() if down == "0018"]
    assert children_of_0018 == ["0019"], (
        f"0018 must have exactly one child, found {sorted(children_of_0018)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked. Expected a single head, got {sorted(heads)}"
    )


def test_0019_is_the_tenant_head():
    """Head IDENTITY, moved here from test_migration_tenant_0018.py.

    That file carried this assertion with a docstring saying 0019 would move this
    line and only this line, and it caught 0019 landing. The battery went red on
    the 0018 head assertion the first time this migration existed. Moving it is
    the instruction the test itself gives, and it is not the same as deleting it.
    Relaxing it to `len(heads) == 1` would leave nothing asserting which revision
    the tree ends at, and an assertion getting weaker inside a test that still
    passes is invisible.

    0020 moves this line and only this line.
    """
    revisions = _all_tenant_revisions()
    parents = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - parents
    assert heads == {"0019"}, (
        f"the tenant head is {sorted(heads)}, not 0019. If a later revision "
        "landed, move this assertion to its test file rather than deleting it"
    )


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_upgrade_creates_model_calls_and_nothing_else():
    sql = _executed("upgrade")
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql))
    assert created == {"MODEL_CALLS"}, (
        f"0019 must create model_calls and nothing else, found {sorted(created)}"
    )


def test_the_create_is_guarded_so_a_re_run_is_a_no_op():
    assert "CREATE TABLE IF NOT EXISTS" in _executed("upgrade"), (
        "the create must be guarded, matching 0009 and every tenant table since"
    )


@pytest.mark.parametrize("column", LEDGER_COLUMNS)
def test_every_column_the_writer_names_exists(column):
    """record_model_call lists these in its INSERT. A missing one is a lost row."""
    assert column in _executed("upgrade"), (
        f"0019 must create model_calls.{column.lower()}, which record_model_call "
        "writes to on every model call"
    )


def test_the_primary_key_is_a_uuid():
    sql = _executed("upgrade")
    assert "ID UUID PRIMARY KEY" in sql, (
        "every tenant table since 0004 takes a uuid primary key: " + sql
    )


def test_at_is_timestamptz():
    """Pricing reads the CAT peak window off this column, so the offset is load bearing."""
    assert re.search(r"\bAT\s+TIMESTAMPTZ\b", _executed("upgrade")), (
        "model_calls.at must be TIMESTAMPTZ. A naive timestamp names no instant, "
        "so a call cannot be placed in a price window"
    )


def test_at_carries_no_default():
    """A defaulted instant would price a lost row at whenever it was inserted."""
    sql = _executed("upgrade")
    assert not re.search(r"\bAT\s+TIMESTAMPTZ[^,]*DEFAULT", sql), (
        "model_calls.at must take no DEFAULT: " + sql
    )


@pytest.mark.parametrize(
    "column",
    ["PURPOSE", "PROVIDER", "REQUESTED_MODEL", "SERVED_MODEL", "MODEL_SOURCE", "TENANT_ID"],
)
def test_the_fields_modelcall_refuses_to_leave_empty_are_not_null(column):
    """ModelCall refuses each of these at construction. The column says the same."""
    sql = _executed("upgrade")
    assert re.search(rf"\b{column}\s+TEXT NOT NULL", sql), (
        f"model_calls.{column.lower()} must be TEXT NOT NULL: " + sql
    )


@pytest.mark.parametrize(
    "column",
    ["INPUT_TOKENS", "OUTPUT_TOKENS", "CACHE_READ_TOKENS", "CACHE_CREATION_TOKENS"],
)
def test_the_token_counts_are_not_null(column):
    """A NULL count sums as nothing, which reports a busy tenant as cheaper."""
    sql = _executed("upgrade")
    assert re.search(rf"\b{column}\s+INT NOT NULL", sql), (
        f"model_calls.{column.lower()} must be INT NOT NULL: " + sql
    )


@pytest.mark.parametrize("column", ["AGENT_ID", "JOB_ID"])
def test_the_optional_ids_stay_nullable(column):
    """A platform call names no agent and a rollup names no job."""
    sql = _executed("upgrade")
    assert not re.search(rf"\b{column}\s+TEXT NOT NULL", sql), (
        f"model_calls.{column.lower()} must stay nullable: " + sql
    )


def test_no_money_column_lands_on_the_row():
    """Money is derived from these counts at read time, against a versioned book."""
    sql = _executed("upgrade")
    for forbidden in ("COST_USD", "COST_ZAR", "PRICE_VERSION", "FX_VERSION"):
        assert forbidden not in sql, (
            f"0019 must not store {forbidden.lower()}. A stored cost freezes "
            "yesterday's price into a row a corrected book can never reach"
        )


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


def test_both_read_paths_are_indexed():
    sql = _executed("upgrade")
    indexed = set(re.findall(r"CREATE INDEX IF NOT EXISTS\s+(\w+)", sql))
    assert indexed == {"IX_MODEL_CALLS_AT", "IX_MODEL_CALLS_JOB_ID"}, (
        f"0019 declares an index on at and one on job_id, found {sorted(indexed)}"
    )


def test_the_indexes_are_guarded():
    sql = _executed("upgrade")
    for statement in [s for s in sql.split(";") if "CREATE INDEX" in s]:
        assert "IF NOT EXISTS" in statement, (
            f"unguarded CREATE INDEX: {statement.strip()!r}"
        )


# ---------------------------------------------------------------------------
# Additive only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden", ["UPDATE ", "DROP TABLE", "ALTER TABLE", "DELETE "])
def test_upgrade_is_strictly_additive(forbidden):
    """0019 creates a table. It touches no table that already holds tenant data."""
    assert forbidden not in _executed("upgrade"), (
        f"0019 upgrade must not contain {forbidden!r}"
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_created():
    sql = _executed("downgrade")
    dropped = set(re.findall(r"DROP TABLE IF EXISTS\s+(\w+)", sql))
    assert dropped == {"MODEL_CALLS"}, (
        f"downgrade must drop exactly the table upgrade created, found {sorted(dropped)}"
    )


def test_every_downgrade_statement_is_guarded():
    """A downgrade that raises leaves a tenant half-migrated."""
    sql = _executed("downgrade")
    for statement in [s for s in sql.split(";") if "DROP" in s]:
        assert "IF EXISTS" in statement, (
            f"unguarded DROP in downgrade: {statement.strip()!r}"
        )


def test_downgrade_touches_no_other_table():
    sql = _executed("downgrade")
    named = set(re.findall(r"DROP TABLE IF EXISTS\s+(\w+)", sql))
    named |= set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert named == {"MODEL_CALLS"}, (
        f"downgrade must touch model_calls and nothing else, found {sorted(named)}"
    )
