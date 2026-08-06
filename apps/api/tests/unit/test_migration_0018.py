"""
Tests for Migration 0018 — prompt_versions (OPS-16, control DB).

NUMBERING NOTE: PLAN.md's task text calls this "0017". Plan 21-04 already
claimed control revision 0017 (alerts_index_staleness_type) earlier in this
phase. This migration is 0018, down_revision "0017", to keep a single control
chain — confirmed by listing alembic/versions/ before writing the migration.
See the plan's <CRITICAL_MIGRATION_NUMBER_CORRECTION> and 21-09-SUMMARY.md.

Covers:
  1. Migration source assertions (file exists, revision 0018, down_revision
     0017, table name, label/canary_percent CHECK constraints).
  2. ORM model assertions (PromptVersion exposes soul_role/soul_voice/
     soul_do_list/soul_donot_list/label/canary_percent; label server_default
     'draft').
  3. Migration DB roundtrip (guarded by INTEGRATION_TESTS_ENABLED=1): upgrade
     to 0018, verify prompt_versions exists, downgrade to 0017, re-upgrade.

Note on encoding:
  All open() calls use encoding="utf-8" to avoid Windows cp1252 UnicodeDecodeError
  (cf. 14-04-SUMMARY deviations).
"""

from __future__ import annotations

import importlib.util
import os

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(__file__)
MIGRATION_FILE = os.path.normpath(
    os.path.join(_TESTS_DIR, "../../alembic/versions/0018_prompt_versions.py")
)
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

# ---------------------------------------------------------------------------
# Migration source assertions
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), (
        f"Migration 0018 not found at expected path: {MIGRATION_FILE}"
    )


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0018", MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0018", f"Expected revision '0018', got {mod.revision!r}"


def test_migration_down_revision_chains_from_0017():
    """Confirms the control chain: 0018 must chain from 0017 (alerts_index_staleness_type),
    NOT 0016 — 0017 was already claimed by plan 21-04 before this plan executed."""
    mod = _load_migration()
    assert mod.down_revision == "0017", (
        f"Expected down_revision '0017' (control chain head at execution time), "
        f"got {mod.down_revision!r}"
    )


def test_migration_source_contains_prompt_versions_table():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "prompt_versions" in source


def test_migration_source_contains_label_check():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "'production', 'canary', 'draft', 'archived'" in source, (
        "Migration 0018 must CHECK label IN ('production','canary','draft','archived')"
    )


def test_migration_source_contains_canary_percent_check():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "canary_percent BETWEEN 0 AND 100" in source


# ---------------------------------------------------------------------------
# ORM model assertions
# ---------------------------------------------------------------------------


def test_orm_prompt_version_has_soul_columns():
    from app.models import PromptVersion

    for col in ("soul_role", "soul_voice", "soul_do_list", "soul_donot_list"):
        assert col in PromptVersion.__table__.c, f"PromptVersion missing column {col!r}"


def test_orm_prompt_version_has_label_and_canary_percent():
    from app.models import PromptVersion

    assert "label" in PromptVersion.__table__.c
    assert "canary_percent" in PromptVersion.__table__.c


def test_orm_prompt_version_label_server_default_draft():
    from app.models import PromptVersion

    col = PromptVersion.__table__.c.label
    assert col.server_default is not None
    assert "draft" in str(col.server_default.arg).lower()


def test_orm_prompt_version_has_version_number():
    from app.models import PromptVersion

    assert "version_number" in PromptVersion.__table__.c


# ---------------------------------------------------------------------------
# Integration gate: DB roundtrip (skipped in unit mode)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INTEGRATION_TESTS,
    reason="INTEGRATION_TESTS_ENABLED=1 required for migration DB roundtrip",
)
def test_migration_0018_db_roundtrip():
    """Integration: upgrade to 0018 -> downgrade to 0017 -> re-upgrade, no error.

    Verifies prompt_versions exists with the expected columns after upgrade
    and is fully removed after downgrade.
    """
    from alembic.config import Config

    from alembic import command

    sync_url = os.environ.get("CONTROL_DB_SYNC_URL", "")
    if not sync_url or "test" not in sync_url.lower():
        pytest.skip("CONTROL_DB_SYNC_URL must point to a test DB (contain 'test' in URL)")

    alembic_ini = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic.ini"))
    cfg = Config(alembic_ini)
    cfg.set_main_option("sqlalchemy.url", sync_url)

    command.upgrade(cfg, "0018")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(sync_url)
    insp = sa_inspect(engine)

    col_names = {c["name"] for c in insp.get_columns("prompt_versions")}
    for col in (
        "id", "agent_id", "version_number", "soul_role", "soul_voice",
        "soul_do_list", "soul_donot_list", "label", "canary_percent", "created_at",
    ):
        assert col in col_names, f"Column '{col}' not found in prompt_versions after upgrade"

    engine.dispose()

    command.downgrade(cfg, "0017")

    engine = create_engine(sync_url)
    insp = sa_inspect(engine)
    assert "prompt_versions" not in insp.get_table_names(), (
        "prompt_versions table still exists after downgrade to 0017"
    )
    engine.dispose()

    command.upgrade(cfg, "0018")

    engine = create_engine(sync_url)
    insp = sa_inspect(engine)
    assert "prompt_versions" in insp.get_table_names()
    engine.dispose()
