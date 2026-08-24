"""
Tests for TENANT migration 0016 — eval_scenarios label provenance (D6 P1).

Named test_migration_tenant_0016 rather than test_migration_0016 for the same
reason as its 0014/0015 siblings: the CONTROL-DB tree numbers its revisions
independently and a reader who assumes one tree will look in the wrong
directory.

What the migration is for, stated where its constraints are checked:
`eval_service.LABEL_TRUST_TIERS` has declared `human_verified` and
`human_authored` since D5 and nothing in the system could produce either,
because the only tier resolver read `eval_scenarios.source` — which says where
the QUESTION came from. 0016 adds the column that lets the LABEL carry its own
tier, so a mined production failure the owner then answers by hand can be
`customer_negative` in origin and `human_authored` in label at the same time
without either statement being overwritten by the other.

THIS MIGRATION HAS NOT BEEN APPLIED. There is no PostgreSQL server on this
machine — every `-m integration` harness skips, and a skip is UNOBSERVED, never
a pass. No ALTER TABLE in 0016 has executed against any database, here or
anywhere. The source-level assertions below are the only observed evidence that
exists for it, so they are written as constraints on what the migration is
ALLOWED to contain:

  - additive columns only  — no DROP/RENAME of anything pre-existing
  - nullable only          — no NOT NULL, no DEFAULT, no backfill
  - one CHECK, and only on the brand-new column (see
    test_the_only_check_is_on_the_new_column for why a CHECK is permitted here
    when 0014 and 0015 forbid one outright)
  - 0011's constraint-name discipline — the name is discovered from
    pg_constraint/pg_attribute at apply time, never assumed
  - the `source` CHECK is NOT touched
  - downgrade drops only what upgrade added, every statement IF EXISTS

Note on encoding:
  All open() calls use encoding="utf-8" to avoid Windows cp1252
  UnicodeDecodeError (cf. 14-04-SUMMARY deviations).
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import re
import uuid

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(__file__)
VERSIONS_DIR = os.path.normpath(
    os.path.join(_TESTS_DIR, "../../alembic_tenant/versions")
)
MIGRATION_FILE = os.path.join(
    VERSIONS_DIR, "0016_eval_scenario_label_provenance.py"
)
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

LABEL_COLUMNS = ("label_trust_tier", "labelled_by", "labelled_at")


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "tenant_migration_0016", MIGRATION_FILE
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _sql_only(fn) -> str:
    """Source of *fn* with comment lines stripped, so prose about `DEFAULT`
    in an explanatory comment does not fail a test about DDL."""
    return "\n".join(
        line
        for line in inspect.getsource(fn).splitlines()
        if not line.strip().startswith("#")
    )


# ---------------------------------------------------------------------------
# Identity and position in the tree
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), (
        f"Tenant migration 0016 not found at expected path: {MIGRATION_FILE}"
    )


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0016", f"Expected revision '0016', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0015", (
        f"Expected down_revision '0015', got {mod.down_revision!r}"
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


def test_0016_is_the_sole_child_of_0015_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0015 breaks every subsequent tenant provision — and nothing on this machine
    would notice, because no migration is ever applied here. Read out of the
    versions directory rather than restated.
    """
    revisions = _all_tenant_revisions()
    assert "0016" in revisions, "0016 was not discovered in alembic_tenant/versions"

    children_of_0015 = [rev for rev, down in revisions.items() if down == "0015"]
    assert children_of_0015 == ["0016"], (
        f"0015 must have exactly one child, found {sorted(children_of_0015)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked — expected a single head, got {sorted(heads)}"
    )


# HEAD IDENTITY MOVED to `test_migration_tenant_0017.py` on 2026-08-18.
#
# `test_0016_is_the_tenant_head` asserted `heads == {"0016"}` and said in its own
# docstring that 0017 would move this line and only this line. 0017 landed
# (BACKLOG 7.34, tool_calls.retrieved_chunks), the battery went red on exactly
# that assertion, and the instruction was followed rather than the assertion
# weakened. It is a move and not a deletion: relaxing it to `len(heads) == 1`
# here would leave nothing naming the tip, and `test_0016_is_the_sole_child_of_
# 0015_and_the_tree_is_unforked` above already covers the fork case.


# ---------------------------------------------------------------------------
# The three columns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "column,ddl_type",
    [
        ("label_trust_tier", "TEXT"),
        ("labelled_by", "TEXT"),
        ("labelled_at", "TIMESTAMPTZ"),
    ],
)
def test_upgrade_adds_each_label_column_nullably(column, ddl_type):
    mod = _load_migration()
    normalised = " ".join(_sql_only(mod.upgrade).split())
    assert (
        f"ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS {column} {ddl_type}"
        in normalised
    ), f"0016 must add eval_scenarios.{column} as nullable {ddl_type}"


def test_upgrade_adds_exactly_three_columns():
    """Three, not four. Scope is part of the safety argument for a migration
    that cannot be tested against a database."""
    mod = _load_migration()
    adds = re.findall(r"ADD COLUMN", _sql_only(mod.upgrade))
    assert len(adds) == 3, f"expected exactly three ADD COLUMN, found {len(adds)}"


def test_upgrade_touches_only_eval_scenarios():
    mod = _load_migration()
    tables = set(re.findall(r"ALTER TABLE (\w+)", _sql_only(mod.upgrade)))
    assert tables == {"eval_scenarios"}, (
        f"0016 upgrade must touch eval_scenarios only, found {sorted(tables)}"
    )


# ---------------------------------------------------------------------------
# Strictly additive / strictly nullable
# ---------------------------------------------------------------------------


def _add_column_statements() -> list[str]:
    """Each ADD COLUMN statement in the migration, whitespace-collapsed.

    Read out of the SQL string literals via ast rather than by line-matching the
    Python source. The first version of this helper used
    `re.findall(r"ADD COLUMN IF NOT EXISTS \\w+ ([^\\n]*)")`, which captures only
    the remainder of the SAME LINE — so a `NOT NULL DEFAULT 'human_authored'`
    wrapped onto the next line sailed straight through it. Found by mutation.
    """
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    statements: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            collapsed = " ".join(node.value.split())
            if "ADD COLUMN IF NOT EXISTS" in collapsed.upper():
                statements.append(collapsed)
    return statements


def test_every_added_column_is_the_bare_alter_and_nothing_else():
    """Nullability asserted as an exact statement shape, not a banned substring.

    0015's version of this test banned "NOT NULL" anywhere in the upgrade, which
    cannot be reused here: this migration's idempotency block legitimately
    contains `IF con_name IS NOT NULL THEN` and `IF NOT EXISTS`, and a test that
    fired on those would be about SQL keywords rather than about columns. So the
    claim is made as an equality on the whole statement — the ALTER, the column,
    the type, and nothing after it. An equality has no blind spot for a clause
    somebody adds on the next line.

    It matters because these three columns land on a table that already has rows
    on every live tenant. NOT NULL with no DEFAULT fails the ALTER outright and
    the tenant's migration stops there; NOT NULL with a DEFAULT succeeds and
    writes a human label onto every row a model produced, which is worse,
    because it is indistinguishable afterwards from the real thing.
    """
    statements = _add_column_statements()
    expected = [
        "ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS label_trust_tier TEXT",
        "ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS labelled_by TEXT",
        "ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS labelled_at TIMESTAMPTZ",
    ]
    assert sorted(statements) == sorted(expected), (
        "an ADD COLUMN statement in 0016 carries something beyond its column "
        f"and type:\n  found:    {sorted(statements)}\n  expected: "
        f"{sorted(expected)}"
    )


@pytest.mark.parametrize(
    "forbidden",
    [
        "DEFAULT",
        "DROP COLUMN",
        "DROP TABLE",
        "RENAME",
        "CREATE TABLE",
        "CREATE INDEX",
        "UPDATE ",
        "DELETE ",
        "REFERENCES",
    ],
)
def test_upgrade_is_strictly_additive_and_nullable(forbidden):
    """No default, no backfill, no destructive statement.

    DEFAULT is on this list for the reason that matters most here. A DEFAULT on
    `labelled_at` would stamp a labelling time onto every row that was never
    labelled, and a DEFAULT on `label_trust_tier` would assert a human on every
    row a model wrote. The honest value for "did a human label this?" on a row
    that predates human labelling is NULL, and a reader takes a NULL there as
    "no claim about who wrote this answer" rather than as a claim that a human
    did.

    "CHECK" and "DROP CONSTRAINT" are NOT on this list, unlike 0015's version of
    this test — see test_the_only_check_is_on_the_new_column and
    test_the_constraint_name_is_discovered_not_assumed for what constrains them
    instead.
    """
    mod = _load_migration()
    assert forbidden not in _sql_only(mod.upgrade).upper(), (
        f"0016 upgrade must not contain {forbidden!r} — it is required to be "
        "strictly additive and strictly nullable (it cannot be verified "
        "against a live DB on this machine)"
    )


def test_no_backfill_invents_a_human_label_for_an_existing_row():
    """The absence pin that matters most for these columns.

    Every eval_scenarios row that exists today was written by a model-driven
    producer — a generated suite, a mined failure, a promoted trace, a contained
    red-team finding. A backfill would write a human trust tier onto rows no
    human ever read, and `human_authored` would then mean "old" rather than "a
    person wrote this". Once done it is indistinguishable from the real thing.
    """
    mod = _load_migration()
    combined = (_sql_only(mod.upgrade) + _sql_only(mod.downgrade)).upper()
    assert "UPDATE" not in combined
    assert "INSERT" not in combined


# ---------------------------------------------------------------------------
# The CHECK: what it constrains, and how it is named
# ---------------------------------------------------------------------------


def test_the_only_check_is_on_the_new_column():
    """A CHECK is permitted here where 0014 and 0015 banned one, and the
    difference is which column it is GOVERNED BY.

    0005's `source` CHECK was inline, unnamed, and on a column live INSERTs
    already wrote — so 0011 had to introspect Postgres' auto-generated name just
    to widen it, and 0014 drew the lesson as "do not repeat that". This CHECK is
    gated on a column that did not exist a moment ago and is NULL on every row,
    so its second arm is never evaluated for any existing row: it cannot break a
    live tenant on apply.

    WHAT IT BUYS, stated as narrowly as it is true: no NON-HUMAN tier can be
    stored, so the column has no vocabulary meaning "a model wrote this". It
    does NOT authenticate the writer — 'human_authored' is accepted from anyone
    holding a tenant connection, and WHO may write it is enforced in Python by
    `label_service`'s R1-R4.

    The test is that no OTHER column's value set is constrained. `reference_answer`
    appears in the second arm as an emptiness test, not as a value list — that is
    a constraint on the PAIR (a human tier beside an empty answer), which no
    existing row can be in.
    """
    mod = _load_migration()
    upgrade_sql = _sql_only(mod.upgrade)
    checks = re.findall(r"CHECK\s*\(", upgrade_sql)
    assert len(checks) == 1, f"expected exactly one CHECK, found {len(checks)}"

    body = upgrade_sql[upgrade_sql.index("CHECK") :]
    assert "label_trust_tier" in body
    for other in ("source", "dataset", "reference_answer", "question"):
        assert not re.search(rf"\b{other}\b\s+IN\s*\(", body), (
            f"0016's CHECK constrains {other!r}, which already has rows in it"
        )


def test_the_check_admits_null_and_only_the_human_tiers():
    """NULL passes (an unlabelled row makes no claim) and nothing but the two
    human tiers may occupy the column.

    There is no value of this column that means "a model wrote it" — a model's
    label records no claim at all, which is what NULL says. Note what that does
    and does not give you: it bounds the VOCABULARY, so the column can never
    carry a model's provenance; it does not bound the WRITER, so it is not the
    guard that stops a bypassing caller writing 'human_authored'.
    """
    mod = _load_migration()
    normalised = " ".join(_sql_only(mod.upgrade).split())
    assert re.search(
        r"label_trust_tier IS NULL\s+OR\s+\(?\s*label_trust_tier IN \(", normalised
    ), (
        "0016's CHECK must admit NULL explicitly — without the IS NULL arm "
        "every model-written INSERT into eval_scenarios starts failing"
    )


def test_the_check_refuses_a_human_tier_on_an_empty_answer():
    """A tier is a claim about a string. There must be a string.

    `label_trust_tier = 'human_authored'` beside `reference_answer = ''` asserts
    that a person authored nothing — and the eval selector's `WHERE
    reference_answer != ''` then never scores that row, so the claim hangs on
    something nothing measures. `record_human_label` refuses to create it; this
    arm stops a direct write, a partial restore, or a downgrade-and-re-upgrade
    from leaving one behind.

    Free to add: the column is brand new and NULL everywhere, so no existing row
    can violate it, and the migration has never been applied anywhere.
    """
    mod = _load_migration()
    normalised = " ".join(_sql_only(mod.upgrade).split())
    assert re.search(
        r"COALESCE\(\s*reference_answer\s*,\s*''\s*\)\s*<>\s*''", normalised
    ), (
        "0016's CHECK must require a non-empty reference_answer whenever a "
        "human tier is present"
    )
    # And it must be ANDed inside the human-tier arm, never a top-level
    # condition — a top-level `reference_answer <> ''` would constrain every
    # existing unlabelled row and fail the ALTER on any live tenant.
    assert re.search(
        r"label_trust_tier IS NULL\s+OR\s+\(", normalised
    ), "the emptiness test must sit inside the human-tier arm, not beside it"


def test_the_catalog_lookup_and_the_drop_are_schema_qualified():
    """Discovery and DROP must refer to the SAME table.

    0011's DO block filters on `rel.relname = 'eval_scenarios'` with no
    pg_namespace join and then EXECUTEs `ALTER TABLE eval_scenarios DROP
    CONSTRAINT %I` against an unqualified name. If a tenant DB ever carried
    `eval_scenarios` in more than one schema, the constraint name would be
    discovered from one table and the DROP applied to whichever the search_path
    resolves — dropping a constraint governing a different table's column.

    0016 inherited that shape and no longer has it. This is unobservable here:
    there is no PostgreSQL on this machine, no migration has been applied, and
    the roundtrip below skips. It is a source-level constraint like every other
    assertion in this file.
    """
    mod = _load_migration()
    upgrade_sql = _sql_only(mod.upgrade)

    assert "pg_namespace" in upgrade_sql, (
        "0016's catalog lookup must join pg_namespace so it discovers a "
        "constraint on the table it is about to alter"
    )
    assert "current_schema()" in upgrade_sql, (
        "0016's catalog lookup must filter to the schema alembic is targeting"
    )
    assert re.search(r"ALTER TABLE %I\.%I DROP CONSTRAINT %I", upgrade_sql), (
        "the DROP must be executed against the schema the name was discovered "
        "in, not against whatever the search_path resolves"
    )
    # The existence guard must be qualified too: a same-named constraint on a
    # different table in a different schema must not make this migration skip
    # its own ADD.
    guard = upgrade_sql[upgrade_sql.index("IF NOT EXISTS") :]
    assert "pg_namespace" in guard and "current_schema()" in guard, (
        "the 'already present' guard must be scoped to this table in this "
        "schema, or a same-named constraint elsewhere suppresses the ADD"
    )


def test_the_human_tiers_match_eval_services_declared_tiers():
    """The migration's tier list and eval_service.HUMAN_LABEL_TIERS are parsed
    against each other, never restated.

    Drift here is silent and one-directional: a tier eval_service believes it
    can stamp but the CHECK refuses shows up as a CheckViolation inside a live
    request's transaction, taking the rest of that request's writes with it.
    Same discipline as test_eval_service's SCHEMA_ALLOWED_SOURCES, which parses
    migration 0011 rather than hardcoding the source list.
    """
    from app.services.eval_service import HUMAN_LABEL_TIERS, LABEL_TRUST_TIERS

    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        src = fh.read()
    clause = re.search(r"label_trust_tier IN \(([^)]*)\)", src)
    assert clause, (
        "Could not find the label_trust_tier CHECK list in migration 0016 — "
        "this test's whole point is to read the schema rather than restate it, "
        "so a parse failure is a real failure."
    )
    # Quoted literals only — the same extraction test_eval_service uses on
    # 0011, for the same reason: a clause regex matches prose as happily as SQL.
    in_migration = set(re.findall(r"'([^']+)'", clause.group(1)))

    assert in_migration == set(HUMAN_LABEL_TIERS), (
        f"migration 0016 admits {sorted(in_migration)} but "
        f"eval_service.HUMAN_LABEL_TIERS is {sorted(HUMAN_LABEL_TIERS)}"
    )
    # And every one of them is a tier the hierarchy actually ranks.
    assert in_migration <= set(LABEL_TRUST_TIERS)


def test_the_constraint_name_is_discovered_not_assumed():
    """0011's technique, applied here: never hardcode a name Postgres chose.

    0011 could not simply `DROP CONSTRAINT eval_scenarios_source_check` because
    that name was auto-generated for an inline unnamed CHECK and there is no
    guarantee a given live tenant carries it. It queried
    pg_constraint/pg_attribute at apply time and dropped whatever it found. This
    migration keeps that discipline even though it chose its own name, because
    the case it defends against is a constraint someone else added to the same
    column — an assumption about a name is exactly the thing that cannot be
    checked from here.
    """
    mod = _load_migration()
    upgrade_sql = _sql_only(mod.upgrade)

    assert "pg_constraint" in upgrade_sql and "pg_attribute" in upgrade_sql, (
        "0016 must discover the constraint governing label_trust_tier via "
        "pg_constraint/pg_attribute, the way 0011 did"
    )
    assert "EXECUTE format(" in upgrade_sql, (
        "the DROP must be executed against the discovered name, not a literal"
    )
    # The only literal constraint name in the upgrade is the one this migration
    # chose itself, and it is used for the ADD and for the "don't drop my own"
    # exclusion — never as the target of a bare DROP.
    assert "DROP CONSTRAINT eval_scenarios" not in upgrade_sql, (
        "0016 upgrade drops a hardcoded constraint name — the name must be "
        "discovered from the catalog at apply time"
    )


def test_the_source_check_is_not_touched():
    """0016 must not widen or drop 0011's `source` CHECK.

    Two separate reasons, either sufficient. First: `source` says where the
    QUESTION came from, and a human-flavoured source value would fuse origin
    into label — the exact collapse the label column exists to prevent, and the
    failure the label hierarchy exists to prevent.
    Second, and customer-facing. `retrieval_service.verified_qa_lookup` serves
    `verified_qa` rows to real customers AHEAD of retrieval. ADR 0003 deleted the
    promotion writer and the resolver that read `source` to tier a row, so
    nothing widens that path today, and a schema-allowed source carrying a human
    flavour is what would reopen it the day a promotion writer returns. The owner
    settled that eval-only on 2026-08-08.
    """
    mod = _load_migration()
    combined = _sql_only(mod.upgrade) + _sql_only(mod.downgrade)
    assert "eval_scenarios_source_check" not in combined
    assert not re.search(r"CHECK\s*\(\s*source\b", combined)
    assert "'production'" not in combined and "'red_team'" not in combined, (
        "0016 restates 0011's source list, which means it is rewriting a "
        "constraint it has no business touching"
    )


def test_the_new_source_values_are_not_snuck_in_as_scenario_sources():
    """No source value is added anywhere in this migration.

    Stated as its own test because the tempting shortcut — `source =
    'owner_authored'` — is one line, reads as obviously correct, and is the
    single change that would carry an owner's answer into verified_qa without
    anybody deciding to.
    """
    mod = _load_migration()
    combined = (_sql_only(mod.upgrade) + _sql_only(mod.downgrade)).lower()
    assert "owner_authored" not in combined
    assert "source in (" not in " ".join(combined.split())


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_added():
    mod = _load_migration()
    downgrade_sql = _sql_only(mod.downgrade)
    normalised = " ".join(downgrade_sql.split())

    for column in LABEL_COLUMNS:
        assert (
            f"ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS {column}" in normalised
        ), f"0016 downgrade must drop {column}"

    tables = set(re.findall(r"ALTER TABLE (\w+)", downgrade_sql))
    assert tables == {"eval_scenarios"}, (
        f"0016 downgrade must touch eval_scenarios only, found {sorted(tables)}"
    )
    assert "DROP TABLE" not in downgrade_sql.upper()
    assert "ADD COLUMN" not in downgrade_sql.upper()


def test_downgrade_is_idempotent_via_if_exists():
    """A downgrade against a DB that never received 0016 must be a no-op."""
    mod = _load_migration()
    downgrade_sql = _sql_only(mod.downgrade)

    drops = re.findall(r"DROP COLUMN(?: IF EXISTS)?", downgrade_sql)
    assert len(drops) == 3, f"expected three DROP COLUMN, found {drops}"
    assert all(d == "DROP COLUMN IF EXISTS" for d in drops), (
        f"every downgrade DROP COLUMN must be IF EXISTS, found {drops}"
    )

    con_drops = re.findall(r"DROP CONSTRAINT(?: IF EXISTS)?", downgrade_sql)
    assert con_drops == ["DROP CONSTRAINT IF EXISTS"], (
        f"the downgrade's constraint drop must be IF EXISTS, found {con_drops}"
    )


def test_downgrade_drops_the_constraint_before_its_columns():
    """Order matters for readability, not for Postgres (dropping a column drops
    its constraints), and stating it here keeps a later edit from reordering the
    file into something that reads as if it relied on cascade behaviour."""
    mod = _load_migration()
    downgrade_sql = _sql_only(mod.downgrade)
    assert downgrade_sql.index("DROP CONSTRAINT") < downgrade_sql.index("DROP COLUMN")


def test_migration_no_pg_search_ddl():
    """No pg_search/pgbm25 extension/index DDL (CLAUDE.md project rule 5)."""
    mod = _load_migration()
    combined = (
        inspect.getsource(mod.upgrade) + inspect.getsource(mod.downgrade)
    ).lower()
    assert "pg_search" not in combined
    assert "pgbm25" not in combined


# ---------------------------------------------------------------------------
# Integration gate: DB roundtrip (skipped in unit mode — UNOBSERVED, not passing)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INTEGRATION_TESTS,
    reason="INTEGRATION_TESTS_ENABLED=1 required for migration DB roundtrip",
)
def test_migration_tenant_0016_db_roundtrip():
    """Integration: upgrade to 0016 -> the three columns exist and accept NULL,
    the CHECK refuses a non-human tier and accepts a human one -> downgrade to
    0015 removes them without losing rows -> re-upgrade (idempotent).

    THIS HAS NEVER RUN. There is no PostgreSQL on the development machine, so
    this test skips, and a skip is unobserved rather than passing. It is written
    out in full so that the first machine that does have a database gets the
    real evidence rather than a stub.
    """
    import psycopg2
    from alembic.config import Config
    from sqlalchemy import create_engine, pool
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text

    from alembic import command

    admin_url = os.environ.get(
        "TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres"
    )
    local_base = os.environ.get(
        "TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432"
    )

    db_name = f"wchats_test_t0016_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(
        admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
    )
    with admin_engine.connect() as conn:
        conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    conn_url = f"{local_base}/{db_name}"
    script_location = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic_tenant"))

    def _scenario_columns() -> set[str]:
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        try:
            return {
                c["name"] for c in sa_inspect(engine).get_columns("eval_scenarios")
            }
        finally:
            engine.dispose()

    try:
        cfg = Config()
        cfg.set_main_option("script_location", script_location)
        cfg.set_main_option("sqlalchemy.url", conn_url)

        command.upgrade(cfg, "0016")
        cols = _scenario_columns()
        for column in LABEL_COLUMNS:
            assert column in cols, f"{column} missing after 0016"

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO eval_scenarios (id, source, question, "
                    "reference_answer) VALUES (gen_random_uuid(), 'mined', "
                    "'t0016 q', '')"
                )
            )
            row = conn.execute(
                sa_text(
                    "SELECT label_trust_tier, labelled_by, labelled_at "
                    "FROM eval_scenarios WHERE question = 't0016 q'"
                )
            ).fetchone()
            assert row == (None, None, None), (
                "0016's columns must be nullable with no DEFAULT — an unlabelled "
                "row must claim nothing"
            )
        engine.dispose()

        # The CHECK refuses a model tier and accepts a human one.
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with pytest.raises(Exception) as excinfo:
            with engine.begin() as conn:
                conn.execute(
                    sa_text(
                        "UPDATE eval_scenarios SET label_trust_tier = "
                        "'model_generated' WHERE question = 't0016 q'"
                    )
                )
        assert "check" in str(excinfo.value).lower()

        # And it refuses a human tier over the empty reference_answer the row
        # was inserted with — a claim that a person authored nothing.
        with pytest.raises(Exception) as excinfo:
            with engine.begin() as conn:
                conn.execute(
                    sa_text(
                        "UPDATE eval_scenarios SET label_trust_tier = "
                        "'human_authored' WHERE question = 't0016 q'"
                    )
                )
        assert "check" in str(excinfo.value).lower()

        # The tier and the answer must land together, which is exactly what
        # label_service.record_human_label's single UPDATE does.
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "UPDATE eval_scenarios SET reference_answer = "
                    "'We refund within 14 days.', label_trust_tier = "
                    "'human_authored' WHERE question = 't0016 q'"
                )
            )
        engine.dispose()
        assert psycopg2 is not None  # import kept honest

        command.downgrade(cfg, "0015")
        cols = _scenario_columns()
        for column in LABEL_COLUMNS:
            assert column not in cols

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            surviving = conn.execute(
                sa_text(
                    "SELECT COUNT(*) FROM eval_scenarios WHERE question = 't0016 q'"
                )
            ).scalar()
            assert surviving == 1, "rollback must not destroy eval_scenarios rows"
        engine.dispose()

        command.upgrade(cfg, "0016")
        assert "label_trust_tier" in _scenario_columns()

    finally:
        admin_engine = create_engine(
            admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
        )
        try:
            with admin_engine.connect() as conn:
                conn.execute(
                    sa_text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :dbname AND pid <> pg_backend_pid()"
                    ),
                    {"dbname": db_name},
                )
                conn.execute(sa_text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        finally:
            admin_engine.dispose()
