"""Tests for TENANT migration 0034, an owner re-tests a red-team finding.

`red_team_findings.status` gains `resolved`, and `red_team_findings.retest` holds
what the latest re-test did. Every gate reader counts `status = 'open'`, so a
resolved finding leaves the gate with no reader changed. The statements are read
by patching `alembic.op.execute`; the revision graph comes from the 0023 sibling.
"""

from __future__ import annotations

import importlib.util
import os
import re
from unittest.mock import patch

from tests.unit.test_migration_tenant_0023 import VERSIONS_DIR, _all_tenant_revisions

MIGRATION_FILE = os.path.join(VERSIONS_DIR, "0034_red_team_finding_retest.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("tenant_0034", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(direction: str) -> list[str]:
    module = _load_migration()
    statements: list[str] = []
    with patch("alembic.op.execute", side_effect=statements.append):
        getattr(module, direction)()
    return [" ".join(str(s).split()).upper() for s in statements]


def _status_values(statements: list[str]) -> list[str]:
    [add] = [s for s in statements if "ADD CONSTRAINT RED_TEAM_FINDINGS_STATUS_CHECK" in s]
    [values] = re.findall(r"CHECK \(STATUS IN \(([^)]*)\)\)$", add)
    return sorted(v.strip() for v in values.split(","))


def test_0034_follows_0033_alone_and_is_the_tenant_head():
    module = _load_migration()
    assert (module.revision, module.down_revision) == ("0034", "0033")
    revisions = _all_tenant_revisions()
    assert [rev for rev, down in revisions.items() if down == "0033"] == ["0034"]
    heads = set(revisions) - {down for down in revisions.values() if down is not None}
    assert heads == {"0034"}, (
        f"the tenant head is {sorted(heads)}, not 0034. If a later revision landed, "
        "move this assertion to its test file rather than deleting it"
    )


def test_upgrade_replaces_the_status_check_with_resolved_added():
    statements = _statements("upgrade")
    drop = "ALTER TABLE RED_TEAM_FINDINGS DROP CONSTRAINT IF EXISTS RED_TEAM_FINDINGS_STATUS_CHECK"
    assert statements.index(drop) < next(
        i for i, s in enumerate(statements) if "ADD CONSTRAINT RED_TEAM_FINDINGS_STATUS_CHECK" in s
    )
    assert _status_values(statements) == ["'CLOSED'", "'CONTAINED'", "'OPEN'", "'RESOLVED'"]


def test_upgrade_adds_the_retest_column_guarded_with_a_json_object_check():
    statements = _statements("upgrade")
    assert "ALTER TABLE RED_TEAM_FINDINGS ADD COLUMN IF NOT EXISTS RETEST JSONB" in statements
    [guard] = [s for s in statements if s.startswith("DO $$")]
    assert "WHERE CONNAME = 'RED_TEAM_FINDINGS_RETEST_OBJECT'" in guard
    assert "CHECK (RETEST IS NULL OR JSONB_TYPEOF(RETEST) = 'OBJECT')" in guard


def test_downgrade_turns_resolved_into_closed_before_restoring_the_three_values():
    statements = _statements("downgrade")
    assert statements[0] == "UPDATE RED_TEAM_FINDINGS SET STATUS = 'CLOSED' WHERE STATUS = 'RESOLVED'"
    assert _status_values(statements) == ["'CLOSED'", "'CONTAINED'", "'OPEN'"]
    assert statements[-1] == "ALTER TABLE RED_TEAM_FINDINGS DROP COLUMN IF EXISTS RETEST"
