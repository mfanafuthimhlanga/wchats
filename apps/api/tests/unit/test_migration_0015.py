"""
Tests for Migration 0015 — Idempotency Reservation Columns.

TDD RED→GREEN:
  Task 1 RED: tests fail because migration 0015 does not exist and ORM lacks new columns.
  Task 1 GREEN: migration + ORM created; all non-integration tests pass.

Covers:
  1. Migration source assertions (file exists, revision, down_revision, column names,
     status/args_hash/reserved_at presence, DROP NOT NULL on result).
  2. ORM model assertions for new columns (status, args_hash, reserved_at) and
     relaxed result (nullable=True) with correct server_default for status.
  3. Migration DB roundtrip (guarded by INTEGRATION_TESTS_ENABLED=1):
     upgrade → downgrade → upgrade round-trips cleanly.

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
    os.path.join(_TESTS_DIR, "../../alembic/versions/0015_idempotency_reservation.py")
)
INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

# ---------------------------------------------------------------------------
# Task 1 — Migration source assertions
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    """RED: fails before migration 0015 is created."""
    assert os.path.isfile(MIGRATION_FILE), (
        f"Migration 0015 not found at expected path: {MIGRATION_FILE}"
    )


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0015", MIGRATION_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_revision():
    """Migration must have revision = '0015'."""
    mod = _load_migration()
    assert mod.revision == "0015", f"Expected revision '0015', got {mod.revision!r}"


def test_migration_down_revision():
    """Migration must chain from '0014'."""
    mod = _load_migration()
    assert mod.down_revision == "0014", (
        f"Expected down_revision '0014', got {mod.down_revision!r}"
    )


def test_migration_has_status_column():
    """Migration source must mention the 'status' column addition."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "status" in source, (
        "Migration 0015 source must include 'status' column"
    )


def test_migration_has_args_hash_column():
    """Migration source must mention the 'args_hash' column addition."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "args_hash" in source, (
        "Migration 0015 source must include 'args_hash' column"
    )


def test_migration_has_reserved_at_column():
    """Migration source must mention the 'reserved_at' column addition."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "reserved_at" in source, (
        "Migration 0015 source must include 'reserved_at' column"
    )


def test_migration_drops_not_null_on_result():
    """Migration source must DROP NOT NULL on result column (allows pending rows with no result yet)."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    # Check for the ALTER COLUMN ... DROP NOT NULL pattern
    source_upper = source.upper()
    assert "DROP NOT NULL" in source_upper, (
        "Migration 0015 must ALTER COLUMN result DROP NOT NULL — "
        "a pending reservation has no result yet"
    )


def test_migration_status_default_completed():
    """Migration source must set status DEFAULT 'completed' (fail-safe for legacy rows)."""
    with open(MIGRATION_FILE, encoding="utf-8") as fh:
        source = fh.read()
    assert "completed" in source, (
        "Migration 0015 status column must have DEFAULT 'completed' "
        "so pre-existing rows are never seen as pending"
    )


# ---------------------------------------------------------------------------
# ORM model assertions
# ---------------------------------------------------------------------------


def test_orm_result_nullable():
    """ToolIdempotencyKey.result must be nullable=True after migration 0015."""
    from app.models import ToolIdempotencyKey

    col = ToolIdempotencyKey.__table__.c.result
    assert col.nullable is True, (
        f"ToolIdempotencyKey.result must be nullable (pending rows have no result), "
        f"got nullable={col.nullable}"
    )


def test_orm_has_status_column():
    """ToolIdempotencyKey must expose a 'status' column."""
    from app.models import ToolIdempotencyKey

    assert "status" in ToolIdempotencyKey.__table__.c, (
        "ToolIdempotencyKey must have a 'status' column"
    )


def test_orm_status_not_nullable():
    """ToolIdempotencyKey.status must be NOT NULL."""
    from app.models import ToolIdempotencyKey

    col = ToolIdempotencyKey.__table__.c.status
    assert col.nullable is False, (
        f"ToolIdempotencyKey.status must be nullable=False, got nullable={col.nullable}"
    )


def test_orm_status_server_default_completed():
    """ToolIdempotencyKey.status server_default must render to 'completed' (fail-safe default)."""
    from app.models import ToolIdempotencyKey

    col = ToolIdempotencyKey.__table__.c.status
    assert col.server_default is not None, (
        "ToolIdempotencyKey.status must have a server_default"
    )
    assert "completed" in str(col.server_default.arg).lower(), (
        f"ToolIdempotencyKey.status server_default must be 'completed', "
        f"got: {col.server_default.arg!r}"
    )


def test_orm_has_args_hash_column():
    """ToolIdempotencyKey must expose an 'args_hash' column."""
    from app.models import ToolIdempotencyKey

    assert "args_hash" in ToolIdempotencyKey.__table__.c, (
        "ToolIdempotencyKey must have an 'args_hash' column"
    )


def test_orm_args_hash_nullable():
    """ToolIdempotencyKey.args_hash must be nullable (legacy rows have no hash)."""
    from app.models import ToolIdempotencyKey

    col = ToolIdempotencyKey.__table__.c.args_hash
    assert col.nullable is True, (
        f"ToolIdempotencyKey.args_hash must be nullable=True (legacy rows have no hash), "
        f"got nullable={col.nullable}"
    )


def test_orm_has_reserved_at_column():
    """ToolIdempotencyKey must expose a 'reserved_at' column."""
    from app.models import ToolIdempotencyKey

    assert "reserved_at" in ToolIdempotencyKey.__table__.c, (
        "ToolIdempotencyKey must have a 'reserved_at' column"
    )


def test_orm_reserved_at_not_nullable():
    """ToolIdempotencyKey.reserved_at must be NOT NULL."""
    from app.models import ToolIdempotencyKey

    col = ToolIdempotencyKey.__table__.c.reserved_at
    assert col.nullable is False, (
        f"ToolIdempotencyKey.reserved_at must be nullable=False, got nullable={col.nullable}"
    )


# ---------------------------------------------------------------------------
# Integration gate: DB roundtrip (skipped in unit mode)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not INTEGRATION_TESTS,
    reason="INTEGRATION_TESTS_ENABLED=1 required for migration DB roundtrip",
)
def test_migration_0015_db_roundtrip():
    """Integration: upgrade → downgrade → upgrade round-trips without error.

    Verifies:
    - status, args_hash, reserved_at columns exist after upgrade
    - result is nullable after upgrade
    - downgrade removes the columns without error
    - second upgrade re-adds them without error
    """
    from alembic.config import Config

    from alembic import command

    sync_url = os.environ.get("CONTROL_DB_SYNC_URL", "")
    if not sync_url or "test" not in sync_url.lower():
        pytest.skip("CONTROL_DB_SYNC_URL must point to a test DB (contain 'test' in URL)")

    # Locate alembic.ini relative to this test file
    alembic_ini = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic.ini"))
    cfg = Config(alembic_ini)
    cfg.set_main_option("sqlalchemy.url", sync_url)

    # Upgrade to 0015
    command.upgrade(cfg, "0015")

    from sqlalchemy import create_engine
    from sqlalchemy import inspect as sa_inspect

    engine = create_engine(sync_url)
    insp = sa_inspect(engine)

    # Verify columns exist after upgrade
    col_names = {c["name"] for c in insp.get_columns("tool_idempotency_keys")}
    for col in ("status", "args_hash", "reserved_at"):
        assert col in col_names, (
            f"Column '{col}' not found in tool_idempotency_keys after upgrade to 0015"
        )

    # Verify result is nullable
    result_col = next(
        c for c in insp.get_columns("tool_idempotency_keys") if c["name"] == "result"
    )
    assert result_col["nullable"] is True, (
        "tool_idempotency_keys.result must be nullable after migration 0015"
    )

    engine.dispose()

    # Downgrade to 0014 — must not error
    command.downgrade(cfg, "0014")

    engine = create_engine(sync_url)
    insp = sa_inspect(engine)

    # Verify columns removed after downgrade
    col_names_after = {c["name"] for c in insp.get_columns("tool_idempotency_keys")}
    for col in ("status", "args_hash", "reserved_at"):
        assert col not in col_names_after, (
            f"Column '{col}' still exists after downgrade from 0015 to 0014"
        )

    engine.dispose()

    # Re-upgrade to 0015 — idempotent
    command.upgrade(cfg, "0015")

    engine = create_engine(sync_url)
    insp = sa_inspect(engine)
    col_names_re = {c["name"] for c in insp.get_columns("tool_idempotency_keys")}
    for col in ("status", "args_hash", "reserved_at"):
        assert col in col_names_re, (
            f"Column '{col}' not found after re-upgrade to 0015"
        )
    engine.dispose()
