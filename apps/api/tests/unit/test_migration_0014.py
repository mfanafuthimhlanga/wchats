"""
Tests for Migration 0014 — Transactional Substrate Tables.

TDD RED→GREEN:
  Task 1 RED: tests 1-6 fail because migration file does not exist yet.
  Task 1 GREEN: migration file created; tests 1-6 pass.
  Task 2 RED: tests 7-11 fail because ORM models do not exist yet.
  Task 2 GREEN: ORM models created + registered; all tests pass.

Covers:
  1. Migration source assertions (file exists, revision, down_revision, constraint names,
     table names, fail-closed enabled default).
  2. ORM model imports and Base.metadata registration.
  3. Migration DB roundtrip (guarded by INTEGRATION_TESTS_ENABLED=1).
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
    os.path.join(_TESTS_DIR, "../../alembic/versions/0014_transactional_substrate.py")
)
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

# ---------------------------------------------------------------------------
# Task 1 — Migration source assertions
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    """RED: fails before migration file is created."""
    assert os.path.isfile(MIGRATION_FILE), (
        f"Migration 0014 not found at expected path: {MIGRATION_FILE}"
    )


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0014", MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_revision():
    """Migration must have revision = '0014'."""
    mod = _load_migration()
    assert mod.revision == "0014", f"Expected revision '0014', got {mod.revision!r}"


def test_migration_down_revision():
    """Migration must chain from '0013'."""
    mod = _load_migration()
    assert mod.down_revision == "0013", (
        f"Expected down_revision '0013', got {mod.down_revision!r}"
    )


def test_migration_has_unique_constraint_names():
    """Both UNIQUE constraint names must be present in the migration source."""
    with open(MIGRATION_FILE) as fh:
        source = fh.read()
    assert "uq_capability_envelopes_agent_skill" in source, (
        "Missing UNIQUE constraint name 'uq_capability_envelopes_agent_skill'"
    )
    assert "uq_tool_idempotency_keys" in source, (
        "Missing UNIQUE constraint name 'uq_tool_idempotency_keys'"
    )


def test_migration_has_all_four_tables():
    """All four transactional substrate table names must appear in the migration."""
    with open(MIGRATION_FILE) as fh:
        source = fh.read()
    for table in (
        "capability_envelopes",
        "tool_calls_audit",
        "pending_confirmations",
        "tool_idempotency_keys",
    ):
        assert table in source, f"Table '{table}' not found in migration source"


def test_migration_enabled_default_false():
    """capability_envelopes.enabled must default to false (fail-closed T-14-01-01)."""
    with open(MIGRATION_FILE) as fh:
        source = fh.read()
    # Positive: 'false' must appear as a default somewhere in the source
    assert "false" in source.lower(), (
        "'false' not found in migration — enabled default must be false (fail-closed)"
    )
    # Negative: must NOT have 'DEFAULT true' for enabled column
    assert "DEFAULT true" not in source, (
        "Migration must not have 'DEFAULT true' — would create fail-open default"
    )


# ---------------------------------------------------------------------------
# Task 1 — DB roundtrip (integration gate, skipped in unit mode)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INTEGRATION_TESTS,
    reason="INTEGRATION_TESTS_ENABLED=1 required for migration DB roundtrip",
)
def test_migration_db_roundtrip():
    """Integration: apply migration to test DB and verify tables + constraints exist."""
    import sqlalchemy
    from sqlalchemy import create_engine, inspect as sa_inspect

    sync_url = os.environ.get("CONTROL_DB_SYNC_URL", "")
    if not sync_url or "test" not in sync_url.lower():
        pytest.skip("CONTROL_DB_SYNC_URL must point to a test DB")

    engine = create_engine(sync_url)
    insp = sa_inspect(engine)
    table_names = insp.get_table_names()

    for table in (
        "capability_envelopes",
        "tool_calls_audit",
        "pending_confirmations",
        "tool_idempotency_keys",
    ):
        assert table in table_names, f"Table '{table}' not found in DB after migration"

    uc_ce = {uc["name"] for uc in insp.get_unique_constraints("capability_envelopes")}
    assert "uq_capability_envelopes_agent_skill" in uc_ce, (
        "UNIQUE constraint uq_capability_envelopes_agent_skill not found in capability_envelopes"
    )

    uc_tik = {uc["name"] for uc in insp.get_unique_constraints("tool_idempotency_keys")}
    assert "uq_tool_idempotency_keys" in uc_tik, (
        "UNIQUE constraint uq_tool_idempotency_keys not found in tool_idempotency_keys"
    )


# ---------------------------------------------------------------------------
# Task 2 — ORM model imports + Base.metadata registration
# ---------------------------------------------------------------------------


def test_orm_imports():
    """All four ORM models are importable from app.models with correct __tablename__."""
    from app.models import (  # noqa: F401
        CapabilityEnvelope,
        PendingConfirmation,
        ToolCallsAudit,
        ToolIdempotencyKey,
    )

    assert CapabilityEnvelope.__tablename__ == "capability_envelopes"
    assert ToolCallsAudit.__tablename__ == "tool_calls_audit"
    assert PendingConfirmation.__tablename__ == "pending_confirmations"
    assert ToolIdempotencyKey.__tablename__ == "tool_idempotency_keys"


def test_orm_models_in_base_metadata():
    """All four ORM models are registered in Base.metadata.tables."""
    from app.models import Base  # noqa: F401  # importing models registers them

    # Trigger registration by importing the four models
    from app.models import (  # noqa: F401
        CapabilityEnvelope,
        PendingConfirmation,
        ToolCallsAudit,
        ToolIdempotencyKey,
    )

    table_names = set(Base.metadata.tables.keys())
    for table in (
        "capability_envelopes",
        "tool_calls_audit",
        "pending_confirmations",
        "tool_idempotency_keys",
    ):
        assert table in table_names, (
            f"'{table}' not found in Base.metadata.tables — "
            "model must be imported in app/models/__init__.py"
        )


def test_capability_envelope_unique_constraint():
    """CapabilityEnvelope declares UNIQUE(agent_id, skill) with the canonical name."""
    from sqlalchemy import UniqueConstraint

    from app.models import CapabilityEnvelope

    uc_names = {
        c.name
        for c in CapabilityEnvelope.__table__.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert "uq_capability_envelopes_agent_skill" in uc_names, (
        f"Expected UniqueConstraint 'uq_capability_envelopes_agent_skill', found: {uc_names}"
    )


def test_tool_idempotency_key_unique_constraint():
    """ToolIdempotencyKey declares UNIQUE(agent_id, skill, idempotency_key)."""
    from sqlalchemy import UniqueConstraint

    from app.models import ToolIdempotencyKey

    uc_names = {
        c.name
        for c in ToolIdempotencyKey.__table__.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert "uq_tool_idempotency_keys" in uc_names, (
        f"Expected UniqueConstraint 'uq_tool_idempotency_keys', found: {uc_names}"
    )


def test_capability_envelope_enabled_default_false():
    """CapabilityEnvelope.enabled server_default is 'false' (fail-closed at ORM level)."""
    from app.models import CapabilityEnvelope

    col = CapabilityEnvelope.__table__.c.enabled
    assert col.server_default is not None, "CapabilityEnvelope.enabled has no server_default"
    assert "false" in str(col.server_default.arg).lower(), (
        f"CapabilityEnvelope.enabled server_default must be 'false', "
        f"got: {col.server_default.arg!r}"
    )
