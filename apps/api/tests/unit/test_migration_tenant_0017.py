"""
Tests for TENANT migration 0017 — tool_calls.retrieved_chunks (BACKLOG 7.34).

Named test_migration_tenant_0017 rather than test_migration_0017 for the same
reason as its 0014/0015/0016 siblings: the CONTROL-DB tree numbers its revisions
independently and a reader who assumes one tree will look in the wrong
directory.

What the migration is for, stated where its constraints are checked:
`grounding_fidelity`'s rubric asks whether a claim is traceable to a chunk
"provided in the tool_calls log", and nothing outside the worker could provide
one. The untruncated chunks lived on the in-process `tool_calls_log`,
`retrieved_context_json` was a Celery task argument and a char-count log line,
and the customer SSE carries a 200-character repr. So the rubric's PASS branch
was unreachable and every grounding verdict had to FAIL whatever the answer
said. 0017 is where the chunks get written down.

THIS MIGRATION HAS BEEN APPLIED, unlike its siblings' claim. On 2026-08-18 it
ran against the local `wchats_tenant_probe` cluster through the production path
and round-tripped: 0016 -> 0017, the column arrives `jsonb` and nullable with no
DEFAULT, downgrade drops it, re-upgrade restores it.

So the assertions below are no longer the ONLY evidence, and they keep a
different job: they constrain what the migration is allowed to CONTAIN, which a
successful apply does not (a migration can apply cleanly and still carry a
DEFAULT that quietly rewrites history). The constraints:

  - additive columns only  — no DROP/RENAME of anything pre-existing
  - nullable only          — no NOT NULL, no DEFAULT, no backfill. NULL and `[]`
                             are different observations here (see
                             test_no_default_invents_a_retrieval_for_an_existing_row)
  - one table touched      — tool_calls, nothing else
  - downgrade drops only what upgrade added, every statement IF EXISTS

Note on encoding:
  All open() calls use encoding="utf-8" to avoid Windows cp1252
  UnicodeDecodeError (cf. 14-04-SUMMARY deviations).
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(__file__)
VERSIONS_DIR = os.path.normpath(
    os.path.join(_TESTS_DIR, "../../alembic_tenant/versions")
)
MIGRATION_FILE = os.path.join(
    VERSIONS_DIR, "0017_tool_calls_retrieved_chunks.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0017", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql_only(fn) -> str:
    """The function's source with comments and docstring stripped.

    Every assertion below is about the SQL the migration EXECUTES. Matching the
    raw source would let a comment mentioning DROP fail the additive check, and
    would equally let a real DROP hide inside a docstring that a reader skims.
    """
    src = inspect.getsource(fn)
    # Strip the DOCSTRING specifically, via `fn.__doc__`, not every triple-quoted
    # block by regex. `op.execute()` takes its SQL in triple quotes, so a regex
    # over `""".*?"""` deletes the statements this file exists to check and
    # leaves every assertion below trivially true against an empty string.
    doc = fn.__doc__
    if doc:
        src = src.replace(doc, "", 1)
    src = re.sub(r"#.*", "", src)
    return src


# ---------------------------------------------------------------------------
# Identity and parentage
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), f"missing migration: {MIGRATION_FILE}"


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0017", f"Expected revision '0017', got {mod.revision!r}"


def test_migration_down_revision():
    mod = _load_migration()
    assert mod.down_revision == "0016", (
        f"Expected down_revision '0016', got {mod.down_revision!r}"
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


def test_0017_is_the_sole_child_of_0016_and_the_tree_is_unforked():
    """A fork is invisible here and fatal on a live tenant.

    `alembic upgrade head` refuses to run with two heads, so a second child of
    0016 breaks every subsequent tenant provision, and nothing on this machine
    would notice because no migration is ever applied here.
    """
    revisions = _all_tenant_revisions()
    assert "0017" in revisions, "0017 was not discovered in alembic_tenant/versions"

    children_of_0016 = [rev for rev, down in revisions.items() if down == "0016"]
    assert children_of_0016 == ["0017"], (
        f"0016 must have exactly one child, found {sorted(children_of_0016)}"
    )

    parents = [down for down in revisions.values() if down is not None]
    heads = set(revisions) - set(parents)
    assert len(heads) == 1, (
        f"the tenant tree is forked — expected a single head, got {sorted(heads)}"
    )


# Head IDENTITY lives in tests/unit/test_migration_tenant_0018.py.
#
# `test_0017_is_the_tenant_head` stood here, asserting `heads == {"0017"}`, with
# a docstring saying 0018 would move this line and only this line. On 2026-08-24
# it went red the first time 0018 existed, which is the whole point of it, and it
# moved as instructed rather than being relaxed or deleted. The assertion above,
# that the tree has one head and 0016 has one child, stays here and is about a
# different failure.
#
# 0019 moves it again, out of the 0018 file.


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------


def test_upgrade_adds_the_column_nullably():
    sql = _sql_only(_load_migration().upgrade).upper()
    assert "RETRIEVED_CHUNKS JSONB" in " ".join(sql.split()), (
        "0017 must add retrieved_chunks as jsonb"
    )
    assert "ADD COLUMN IF NOT EXISTS" in " ".join(sql.split()), (
        "the add must be guarded so a re-run is a no-op, matching 0016"
    )


def test_upgrade_touches_only_tool_calls():
    sql = _sql_only(_load_migration().upgrade).upper()
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"TOOL_CALLS"}, (
        f"0017 must touch tool_calls and nothing else, found {sorted(tables)}"
    )


def test_upgrade_adds_exactly_one_column():
    sql = _sql_only(_load_migration().upgrade).upper()
    assert sql.count("ADD COLUMN") == 1, (
        "0017 adds one column; a second one landing here without its own "
        "rationale is the kind of drift this file exists to catch"
    )


@pytest.mark.parametrize(
    "forbidden",
    ["NOT NULL", "DEFAULT", "UPDATE ", "DROP COLUMN", "DROP TABLE", "RENAME", "DELETE "],
)
def test_upgrade_is_strictly_additive_and_nullable(forbidden):
    """No default, no backfill, no destructive statement."""
    mod = _load_migration()
    assert forbidden not in _sql_only(mod.upgrade).upper(), (
        f"0017 upgrade must not contain {forbidden!r} — it is required to be "
        "strictly additive and strictly nullable (it cannot be verified "
        "against a live DB on this machine)"
    )


def test_no_default_invents_a_retrieval_for_an_existing_row():
    """The reason the column is nullable, stated where it is enforced.

    NULL means this call retrieves nothing, or its capture could not be decoded.
    `[]` means a retrieve ran and the corpus matched nothing. A DEFAULT of `[]`
    would assert the second about every row written before this column existed,
    and a judge shown an empty context marks every claim unsupported — which is
    BACKLOG 5.16's failure, manufacturing an ungrounded verdict that is about
    the migration rather than about the answer.
    """
    sql = _sql_only(_load_migration().upgrade).upper()
    assert "DEFAULT" not in sql
    assert "'[]'" not in sql and '"[]"' not in sql


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_only_what_upgrade_added():
    sql = _sql_only(_load_migration().downgrade).upper()
    dropped = set(re.findall(r"DROP COLUMN IF EXISTS\s+(\w+)", sql))
    assert dropped == {"RETRIEVED_CHUNKS"}, (
        f"downgrade must drop exactly the column upgrade added, found {sorted(dropped)}"
    )


def test_every_downgrade_statement_is_guarded():
    """A downgrade that raises leaves a tenant half-migrated."""
    sql = _sql_only(_load_migration().downgrade).upper()
    for statement in [s for s in sql.split(";") if "DROP" in s]:
        assert "IF EXISTS" in statement, (
            f"unguarded DROP in downgrade: {statement.strip()!r}"
        )


def test_downgrade_touches_only_tool_calls():
    sql = _sql_only(_load_migration().downgrade).upper()
    tables = set(re.findall(r"ALTER TABLE\s+(\w+)", sql))
    assert tables == {"TOOL_CALLS"}, (
        f"downgrade must touch tool_calls and nothing else, found {sorted(tables)}"
    )
