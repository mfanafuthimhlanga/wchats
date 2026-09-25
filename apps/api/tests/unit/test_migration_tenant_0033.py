"""Tests for TENANT migration 0033, evidence and claims on red_team_findings (#310).

Named test_migration_tenant_0033 like its 0014 to 0032 siblings: the CONTROL-DB
tree numbers its revisions independently and a reader who assumes one tree looks
in the wrong directory.

A finding row says what it stood on and which claim kinds stood. The constraints
this file holds the migration to:

  - two columns on red_team_findings, nothing else touched either way
  - `evidence` TEXT NOT NULL DEFAULT 'attacker_report', so a row from before
    0033 reads as the attacker-word finding it was
  - a named CHECK on the three evidence values
  - `claims` JSONB NOT NULL DEFAULT an empty list, with a named CHECK that it
    is a JSON array
  - each CHECK added by a block guarded on its name in pg_constraint, after its
    column, so a column present without its CHECK gains it on a re-run
  - downgrade drops exactly what upgrade added, guarded

The statements are read by patching `alembic.op.execute`, never by reading the
migration as text; the revision graph comes from the 0023 sibling's helper.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

from tests.unit.test_migration_tenant_0023 import VERSIONS_DIR, _all_tenant_revisions

MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0033_red_team_finding_evidence.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0033", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(direction: str) -> list[str]:
    module = _load_migration()
    statements: list[str] = []
    with patch("alembic.op.execute", side_effect=statements.append):
        getattr(module, direction)()
    assert statements, f"{direction}() issued no statement at all"
    return [" ".join(str(s).split()).upper() for s in statements]


def _ddl_only(statement: str) -> str:
    """The statement with every string literal emptied, so comment text never matches."""
    return re.sub(r"'(?:[^']|'')*'", "''", statement)


def _added_columns() -> list[str]:
    return [s for s in _statements("upgrade") if "ADD COLUMN" in s]


_GUARDED_CHECK = re.compile(
    r"^DO \$\$ BEGIN IF NOT EXISTS \(SELECT 1 FROM PG_CONSTRAINT WHERE CONNAME = '(\w+)'\) "
    r"THEN ALTER TABLE RED_TEAM_FINDINGS ADD CONSTRAINT (\w+) CHECK \((.*)\); "
    r"END IF; END \$\$$"
)


def _guarded_checks() -> dict[str, str]:
    """Each guarded CHECK block in upgrade() as {constraint name: check expression}.

    The block's pg_constraint guard and its ADD CONSTRAINT must name the same
    constraint.
    """
    checks = {}
    for statement in _statements("upgrade"):
        if not statement.startswith("DO $$"):
            continue
        match = _GUARDED_CHECK.match(statement)
        assert match, statement
        guarded, added, check = match.groups()
        assert guarded == added, statement
        checks[added] = check
    return checks


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), f"missing migration: {MIGRATION_FILE}"


def test_migration_revision_and_parent():
    module = _load_migration()
    assert module.revision == "0033"
    assert module.down_revision == "0032"


def test_0033_is_the_sole_child_of_0032_and_the_tree_is_unforked():
    revisions = _all_tenant_revisions()
    assert "0033" in revisions
    children_of_0032 = [rev for rev, down in revisions.items() if down == "0032"]
    assert children_of_0032 == ["0033"], (
        f"0032 must have exactly one child, found {sorted(children_of_0032)}"
    )
    parents = [down for down in revisions.values() if down is not None]
    assert len(parents) == len(set(parents)), "two revisions share a parent"


def test_upgrade_adds_exactly_two_columns_on_red_team_findings_guarded():
    added = _added_columns()
    assert len(added) == 2
    for statement in added:
        assert statement.startswith("ALTER TABLE RED_TEAM_FINDINGS ")
        assert statement.count("ADD COLUMN") == 1
        assert "ADD COLUMN IF NOT EXISTS" in statement


def test_evidence_is_not_null_text_defaulting_to_the_attackers_word():
    [evidence] = [s for s in _added_columns() if "EVIDENCE TEXT" in s]
    assert "ADD COLUMN IF NOT EXISTS EVIDENCE TEXT NOT NULL DEFAULT 'ATTACKER_REPORT'" in evidence


def test_evidence_carries_a_named_check_on_exactly_the_three_values():
    check = _guarded_checks()["RED_TEAM_FINDINGS_EVIDENCE_CHECK"]
    [values] = re.findall(r"^EVIDENCE IN \(([^)]*)\)$", check)
    assert sorted(v.strip() for v in values.split(",")) == [
        "'ATTACKER_REPORT'", "'LANDED_VERDICT_TAG'", "'RECORDED_PROMPT_RUN'",
    ]


def test_claims_carries_a_named_check_that_it_is_a_json_array():
    assert _guarded_checks()["RED_TEAM_FINDINGS_CLAIMS_ARRAY"] == "JSONB_TYPEOF(CLAIMS) = 'ARRAY'"


def test_upgrade_adds_exactly_the_two_checks_each_guarded_and_after_its_column():
    statements = _statements("upgrade")
    assert set(_guarded_checks()) == {
        "RED_TEAM_FINDINGS_EVIDENCE_CHECK", "RED_TEAM_FINDINGS_CLAIMS_ARRAY",
    }
    for column, name in (
        ("EVIDENCE", "RED_TEAM_FINDINGS_EVIDENCE_CHECK"),
        ("CLAIMS", "RED_TEAM_FINDINGS_CLAIMS_ARRAY"),
    ):
        [column_at] = [
            i for i, s in enumerate(statements) if f"ADD COLUMN IF NOT EXISTS {column} " in s
        ]
        [check_at] = [i for i, s in enumerate(statements) if f"ADD CONSTRAINT {name} " in s]
        assert column_at < check_at, name
    for statement in statements:
        if "ADD COLUMN" in statement:
            assert "CONSTRAINT" not in statement, "a CHECK inside ADD COLUMN skips with it"


def test_claims_is_not_null_jsonb_defaulting_to_an_empty_list():
    [claims] = [s for s in _added_columns() if "CLAIMS JSONB" in s]
    assert "ADD COLUMN IF NOT EXISTS CLAIMS JSONB NOT NULL DEFAULT '[]'::JSONB" in claims


def test_upgrade_comments_both_columns():
    comments = [s for s in _statements("upgrade") if s.startswith("COMMENT ON COLUMN")]
    targets = sorted(s.split(" IS ")[0] for s in comments)
    assert targets == [
        "COMMENT ON COLUMN RED_TEAM_FINDINGS.CLAIMS",
        "COMMENT ON COLUMN RED_TEAM_FINDINGS.EVIDENCE",
    ]


def test_upgrade_touches_only_red_team_findings():
    for statement in _statements("upgrade"):
        ddl = _ddl_only(statement)
        assert "RED_TEAM_FINDINGS" in ddl
        for other in ("RED_TEAM_RUNS", "RED_TEAM_PROBES", "RED_TEAM_STRATEGIES", "EVAL_"):
            assert other not in ddl, other


def test_downgrade_drops_exactly_what_upgrade_added_and_is_guarded():
    assert _statements("downgrade") == [
        "ALTER TABLE RED_TEAM_FINDINGS DROP CONSTRAINT IF EXISTS RED_TEAM_FINDINGS_CLAIMS_ARRAY",
        "ALTER TABLE RED_TEAM_FINDINGS DROP CONSTRAINT IF EXISTS RED_TEAM_FINDINGS_EVIDENCE_CHECK",
        "ALTER TABLE RED_TEAM_FINDINGS DROP COLUMN IF EXISTS CLAIMS",
        "ALTER TABLE RED_TEAM_FINDINGS DROP COLUMN IF EXISTS EVIDENCE",
    ]
