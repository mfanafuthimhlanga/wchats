"""
Tests for Migration 0019 — blast-radius warning thresholds, actor_mode,
envelope-hash acknowledgement (BLR-01, BLR-02, CAP-03/04; control DB).

Covers:
  1. Migration source assertions (file exists, revision 0019, down_revision
     0018, all five ADD COLUMN IF NOT EXISTS statements, the guarded
     actor_mode CHECK constraint, the three legal actor_mode shapes, a
     downgrade scoped to 0019's own additions).
  2. ORM model assertions (CapabilityEnvelope.actor_mode + server default,
     ChecklistRun.envelope_hash / envelope_acknowledged_at,
     Tenant.blast_radius_warn_single_cents / blast_radius_warn_hourly_cents).
  3. Settings assertions (the three Phase 18 blast-radius platform defaults
     are readable with zero environment configuration).
  4. Migration DB roundtrip (guarded by INTEGRATION_TESTS_ENABLED=1): upgrade
     to 0019, verify all five columns exist, downgrade to 0018 (verify they
     are gone and that pre-existing columns survive), re-upgrade to 0019.

Note on encoding:
  All open() calls use encoding="utf-8" to avoid Windows cp1252
  UnicodeDecodeError (cf. 14-04-SUMMARY deviations).
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
    os.path.join(_TESTS_DIR, "../../alembic/versions/0019_blast_radius_capability_v2.py")
)
INTEGRATION_TESTS_ENABLED = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

# ---------------------------------------------------------------------------
# Migration source assertions
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert os.path.isfile(MIGRATION_FILE), (
        f"Migration 0019 not found at expected path: {MIGRATION_FILE}"
    )


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0019", MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "0019", f"Expected revision '0019', got {mod.revision!r}"


def test_migration_down_revision_chains_from_0018():
    """Confirms the control chain: 0019 must chain from 0018 (prompt_versions),
    the verified control head at this plan's authoring time."""
    mod = _load_migration()
    assert mod.down_revision == "0018", (
        f"Expected down_revision '0018' (control chain head at planning time), "
        f"got {mod.down_revision!r}"
    )


def test_migration_source_adds_all_five_columns():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    for col in (
        "envelope_hash",
        "envelope_acknowledged_at",
        "actor_mode",
        "blast_radius_warn_single_cents",
        "blast_radius_warn_hourly_cents",
    ):
        assert col in source, f"Migration 0019 source missing column name {col!r}"
    assert source.count("ADD COLUMN IF NOT EXISTS") >= 5, (
        "Expected at least 5 'ADD COLUMN IF NOT EXISTS' statements in migration 0019"
    )


def test_migration_source_guards_actor_mode_constraint():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "ck_capability_envelopes_actor_mode" in source
    assert "duplicate_object" in source, (
        "actor_mode CHECK add must be wrapped to tolerate a re-run "
        "(DO block catching duplicate_object)"
    )


def test_migration_source_actor_mode_check_accepts_three_shapes():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "'always-on'" in source
    assert "'off'" in source
    assert "sample_at_rate_" in source


def test_downgrade_scoped_to_0019_additions():
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    idx = source.index("def downgrade")
    downgrade_body = source[idx:]
    assert "IF EXISTS" in downgrade_body
    assert "approved_by" not in downgrade_body, (
        "downgrade() must not reference pre-existing checklist_runs columns"
    )
    assert "daily_budget_usd" not in downgrade_body, (
        "downgrade() must not reference pre-existing tenants columns"
    )


# ---------------------------------------------------------------------------
# ORM model assertions
# ---------------------------------------------------------------------------


def test_orm_capability_envelope_has_actor_mode():
    from app.models.capability_envelope import CapabilityEnvelope

    assert "actor_mode" in CapabilityEnvelope.__table__.c


def test_orm_capability_envelope_actor_mode_server_default_always_on():
    from app.models.capability_envelope import CapabilityEnvelope

    col = CapabilityEnvelope.__table__.c.actor_mode
    assert col.server_default is not None
    assert "always-on" in str(col.server_default.arg)


def test_orm_checklist_run_has_envelope_columns():
    from app.models.checklist_run import ChecklistRun

    for col in ("envelope_hash", "envelope_acknowledged_at"):
        assert col in ChecklistRun.__table__.c, f"ChecklistRun missing column {col!r}"


def test_orm_tenant_has_blast_radius_threshold_columns():
    from app.models.tenant import Tenant

    for col in ("blast_radius_warn_single_cents", "blast_radius_warn_hourly_cents"):
        assert col in Tenant.__table__.c, f"Tenant missing column {col!r}"


# ---------------------------------------------------------------------------
# Settings assertions
# ---------------------------------------------------------------------------


def test_settings_expose_blast_radius_defaults():
    from app.core.config import settings

    assert settings.BLAST_RADIUS_WARN_SINGLE_CENTS == 50000
    assert settings.BLAST_RADIUS_WARN_HOURLY_CENTS == 200000
    assert settings.BLAST_RADIUS_OBSERVED_WINDOW_DAYS == 7


# ---------------------------------------------------------------------------
# Integration gate: DB roundtrip (skipped in unit mode)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INTEGRATION_TESTS_ENABLED,
    reason="INTEGRATION_TESTS_ENABLED=1 required for migration DB roundtrip",
)
def test_migration_0019_db_roundtrip():
    """Integration: upgrade to 0019 -> downgrade to 0018 -> re-upgrade, no error.

    Verifies all five columns are present on their three tables after upgrade,
    absent after downgrade, and that checklist_runs.approved_by and
    capability_envelopes.skill (both pre-0019) survive the downgrade —
    proving the downgrade is scoped to 0019's own additions only.
    """
    from alembic.config import Config

    from alembic import command

    sync_url = os.environ.get("CONTROL_DB_SYNC_URL", "")
    if not sync_url or "test" not in sync_url.lower():
        pytest.skip("CONTROL_DB_SYNC_URL must point to a test DB (contain 'test' in URL)")

    alembic_ini = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic.ini"))
    cfg = Config(alembic_ini)
    cfg.set_main_option("sqlalchemy.url", sync_url)

    command.upgrade(cfg, "0019")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(sync_url)
    insp = sa_inspect(engine)

    checklist_runs_cols = {c["name"] for c in insp.get_columns("checklist_runs")}
    capability_envelopes_cols = {c["name"] for c in insp.get_columns("capability_envelopes")}
    tenants_cols = {c["name"] for c in insp.get_columns("tenants")}

    assert "envelope_hash" in checklist_runs_cols
    assert "envelope_acknowledged_at" in checklist_runs_cols
    assert "actor_mode" in capability_envelopes_cols
    assert "blast_radius_warn_single_cents" in tenants_cols
    assert "blast_radius_warn_hourly_cents" in tenants_cols

    engine.dispose()

    command.downgrade(cfg, "0018")

    engine = create_engine(sync_url)
    insp = sa_inspect(engine)

    checklist_runs_cols = {c["name"] for c in insp.get_columns("checklist_runs")}
    capability_envelopes_cols = {c["name"] for c in insp.get_columns("capability_envelopes")}
    tenants_cols = {c["name"] for c in insp.get_columns("tenants")}

    assert "envelope_hash" not in checklist_runs_cols
    assert "envelope_acknowledged_at" not in checklist_runs_cols
    assert "actor_mode" not in capability_envelopes_cols
    assert "blast_radius_warn_single_cents" not in tenants_cols
    assert "blast_radius_warn_hourly_cents" not in tenants_cols

    # Downgrade must be scoped — pre-0019 columns survive.
    assert "approved_by" in checklist_runs_cols
    assert "skill" in capability_envelopes_cols

    engine.dispose()

    command.upgrade(cfg, "0019")

    engine = create_engine(sync_url)
    insp = sa_inspect(engine)
    checklist_runs_cols = {c["name"] for c in insp.get_columns("checklist_runs")}
    assert "envelope_hash" in checklist_runs_cols
    engine.dispose()
