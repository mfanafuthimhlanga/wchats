"""Tests for CONTROL migration 0020, tenant_usage_daily (ticket #46, issue #22).

Named test_migration_control_0020 rather than test_migration_0020 because the two
alembic trees number their revisions independently. The tenant tree already has a
0019 and will have its own 0020, so a reader who assumes one tree looks in the
wrong directory. `alembic/versions` is the CONTROL tree. `alembic_tenant/versions`
is the tenant one.

WHAT THE TABLE IS FOR
    The tenant ledger (tenant migration 0019) holds one row per model call and no
    money at all. `rollup_model_calls` reads a day of those rows, prices each one
    through `app.domain.pricing`, and writes one row per (tenant, purpose, day)
    here. This table is the DERIVED figure, and the tenant ledger stays the fact it
    was derived from.

WHY MONEY LIVES HERE WHEN IT IS BANNED FROM THE LEDGER
    Because every figure here carries the version of the book that produced it.
    `price_version` and `fx_version` say which tariff and which rand rate priced
    the row, so a corrected book re-derives the day and OVERWRITES it, and a reader
    can always tell which book a figure came from. A cost stored on the ledger row
    could say neither of those things.

WHY THE PRIMARY KEY IS (tenant_id, purpose, day)
    It is the upsert key, and the upsert is the task's idempotency. Running the
    rollup twice for the same day writes the same three values and lands on the
    same row, so the second run leaves the table exactly as the first did. A
    surrogate id would let a second run append a duplicate day beside the first.

WHY THE MONEY COLUMNS ARE NULLABLE AND THE COUNTS ARE NOT
    A purpose group whose model the book does not price is written with its tokens
    and its call count and NULL money. The gap is then visible in the table as a
    row that spent tokens for no recorded cost, rather than invisible in a task
    that crashed. A NULL token count would report a busy tenant as cheaper, so the
    counts take NOT NULL.

WHY THE MONEY COLUMNS ARE NUMERIC WITH NO PRECISION
    One judge call costs a small fraction of a cent. Rounding to cents anywhere
    before the report reads the row would report a busy tenant as free, which is
    the failure this ticket exists to end.

HOW THE SQL IS READ HERE
    By running upgrade() and downgrade() against a recording stand-in for
    `op.execute`, not by reading their source. That is the statement the migration
    actually issues, and it keeps this file off gates.py's
    SOURCE_ASSERTION_BASELINE, which exists to stop tests asserting on characters
    of source.

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
VERSIONS_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic/versions"))
MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0020_tenant_usage_daily.py")

#: Every column the rollup upsert names. A missing one is a lost figure.
ROLLUP_COLUMNS = (
    "TENANT_ID",
    "PURPOSE",
    "DAY",
    "INPUT_TOKENS",
    "OUTPUT_TOKENS",
    "CACHE_READ_TOKENS",
    "CACHE_CREATION_TOKENS",
    "CALL_COUNT",
    "COST_USD",
    "COST_ZAR",
    "PRICE_VERSION",
    "FX_VERSION",
)

COUNT_COLUMNS = (
    "INPUT_TOKENS",
    "OUTPUT_TOKENS",
    "CACHE_READ_TOKENS",
    "CACHE_CREATION_TOKENS",
    "CALL_COUNT",
)

DERIVED_COLUMNS = ("COST_USD", "COST_ZAR", "PRICE_VERSION", "FX_VERSION")


def _load_migration():
    spec = importlib.util.spec_from_file_location("control_0020", MIGRATION_FILE)
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
    assert mod.revision == "0020", f"Expected revision '0020', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0019", (
        f"Expected down_revision '0019', got {mod.down_revision!r}"
    )


def _all_control_revisions() -> dict[str, str | None]:
    """revision -> down_revision for every file in alembic/versions."""
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
    """A fork is invisible here and fatal on the live control database.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0019 stops every later control migration.
    """
    revisions = _all_control_revisions()
    assert "0020" in revisions, "0020 was not discovered in alembic/versions"

    children_of_0019 = [rev for rev, down in revisions.items() if down == "0019"]
    assert children_of_0019 == ["0020"], (
        f"0019 must have exactly one child, found {sorted(children_of_0019)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the control tree is forked. Expected a single head, got {sorted(heads)}"
    )


# Head IDENTITY moved to test_migration_control_0021.py when 0021 landed, on the
# instruction this comment replaces: the assertion travels to the new head's test
# file rather than being deleted, so it never gets weaker inside a test that still
# passes. `len(heads) == 1` above stays here and still catches a fork.


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_upgrade_creates_tenant_usage_daily_and_nothing_else():
    sql = _executed("upgrade")
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql))
    assert created == {"TENANT_USAGE_DAILY"}, (
        f"0020 must create tenant_usage_daily and nothing else, found {sorted(created)}"
    )


def test_the_create_is_guarded_so_a_re_run_is_a_no_op():
    assert "CREATE TABLE IF NOT EXISTS" in _executed("upgrade"), (
        "the create must be guarded, matching 0011 and every control table since"
    )


@pytest.mark.parametrize("column", ROLLUP_COLUMNS)
def test_every_column_the_upsert_names_exists(column):
    """rollup_model_calls writes each of these on every row it derives."""
    assert column in _executed("upgrade"), (
        f"0020 must create tenant_usage_daily.{column.lower()}, which the rollup "
        "upsert names"
    )


def test_the_primary_key_is_the_upsert_key():
    """The task's idempotency IS this key. A second run must land on the same row."""
    sql = _executed("upgrade")
    assert re.search(r"PRIMARY KEY \(\s*TENANT_ID,\s*PURPOSE,\s*DAY\s*\)", sql), (
        "tenant_usage_daily takes PRIMARY KEY (tenant_id, purpose, day), the ON "
        "CONFLICT target that makes a re-run overwrite rather than append: " + sql
    )


def test_the_day_is_a_date():
    """One row per calendar day, not per instant."""
    assert re.search(r"\bDAY\s+DATE NOT NULL\b", _executed("upgrade")), (
        "tenant_usage_daily.day must be DATE NOT NULL"
    )


@pytest.mark.parametrize("column", COUNT_COLUMNS)
def test_the_counts_are_not_null(column):
    """A NULL count sums as nothing, which reports a busy tenant as cheaper."""
    sql = _executed("upgrade")
    assert re.search(rf"\b{column}\s+BIGINT NOT NULL", sql), (
        f"tenant_usage_daily.{column.lower()} must be BIGINT NOT NULL. A day's sum "
        "across a tenant outgrows INT: " + sql
    )


@pytest.mark.parametrize("column", DERIVED_COLUMNS)
def test_the_derived_columns_stay_nullable(column):
    """A purpose the book cannot price is written with tokens and NULL money."""
    sql = _executed("upgrade")
    assert not re.search(rf"\b{column}\s+\w+\s+NOT NULL", sql), (
        f"tenant_usage_daily.{column.lower()} must stay nullable, so an unpriceable "
        "purpose lands as a visible gap rather than crashing the rollup: " + sql
    )


@pytest.mark.parametrize("column", ["COST_USD", "COST_ZAR"])
def test_the_money_columns_carry_no_precision(column):
    """One judge call costs a fraction of a cent. Rounding here reports it as free."""
    sql = _executed("upgrade")
    assert re.search(rf"\b{column}\s+NUMERIC(?!\s*\()", sql), (
        f"tenant_usage_daily.{column.lower()} must be NUMERIC with no precision. "
        "A scale would round a day of judge calls to zero: " + sql
    )


@pytest.mark.parametrize("column", ["PRICE_VERSION", "FX_VERSION"])
def test_every_figure_can_name_the_book_that_produced_it(column):
    sql = _executed("upgrade")
    assert re.search(rf"\b{column}\s+TEXT", sql), (
        f"tenant_usage_daily.{column.lower()} must be TEXT. A money figure that "
        "cannot name its book cannot be checked against an invoice: " + sql
    )


def test_the_day_read_is_indexed():
    """A platform report reads one day across every tenant, which the key misses."""
    sql = _executed("upgrade")
    indexed = set(re.findall(r"CREATE INDEX IF NOT EXISTS\s+(\w+)", sql))
    assert indexed == {"IX_TENANT_USAGE_DAILY_DAY"}, (
        f"0020 declares one index, on day, found {sorted(indexed)}"
    )


def test_the_index_is_guarded():
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
    """0020 creates a table. It touches nothing that already holds control data."""
    assert forbidden not in _executed("upgrade"), (
        f"0020 upgrade must not contain {forbidden!r}"
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_created():
    sql = _executed("downgrade")
    dropped = set(re.findall(r"DROP TABLE IF EXISTS\s+(\w+)", sql))
    assert dropped == {"TENANT_USAGE_DAILY"}, (
        f"downgrade must drop exactly the table upgrade created, found {sorted(dropped)}"
    )


def test_every_downgrade_statement_is_guarded():
    """A downgrade that raises leaves the control database half-migrated."""
    sql = _executed("downgrade")
    for statement in [s for s in sql.split(";") if "DROP" in s]:
        assert "IF EXISTS" in statement, (
            f"unguarded DROP in downgrade: {statement.strip()!r}"
        )


def test_downgrade_touches_no_other_table():
    sql = _executed("downgrade")
    named = set(re.findall(r"DROP TABLE IF EXISTS\s+(\w+)", sql))
    named |= set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert named == {"TENANT_USAGE_DAILY"}, (
        f"downgrade must touch tenant_usage_daily and nothing else, found {sorted(named)}"
    )
