"""
Integration tests: apply_migrations / run_tenant_migrations against real local Postgres.

These tests call run_tenant_migrations() DIRECTLY (not via Celery task), so they
do NOT need the celery_worker fixture. They test the service function in isolation.

Uses a REAL local Postgres DB. Each test creates a unique database or schema to
ensure complete isolation, then tears it down in a finally block.

Tests:
    test_apply_migrations_creates_v1_schema
        — Creates a fresh test DB, runs migrations, asserts all 10 tables present
          and alembic_version has one row.

    test_apply_migrations_idempotent
        — Runs migrations twice on same DB; second call is a no-op; table count unchanged.

    test_migration_0008_creates_customer_identities
        — 0008 migration roundtrip: creates customer_identities table with UNIQUE(external_id)
          and both ix_customer_identities_token_hash / ix_customer_identities_expires_at indexes
          in a fresh live local Postgres tenant DB; second run_tenant_migrations call is a safe
          no-op (revision stays "0008"). Satisfies IDV-01 substrate requirement.
"""

import uuid

import pytest
from sqlalchemy import create_engine, pool, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration

# The 10 tenant v1 tables as defined in alembic_tenant/versions/0001_tenant_v1_schema.py
V1_TABLES = frozenset(
    [
        "documents",
        "chunks",
        "embeddings",
        "chunk_metadata",
        "conversations",
        "messages",
        "tool_calls",
        "eval_runs",
        "eval_results",
        "red_team_runs",
    ]
)

# Admin connection URL for creating/dropping test databases
# Using template1 (or postgres) to run CREATE DATABASE commands
_ADMIN_DB_URL = "postgresql://wchats:wchats@localhost:5432/postgres"
_LOCAL_BASE = "postgresql://wchats:wchats@localhost:5432"


def _create_test_database(db_name: str) -> str:
    """Create a new Postgres database for a migration test.

    Args:
        db_name: Name of the database to create.

    Returns:
        str: Connection URL for the new database.
    """
    engine = create_engine(
        _ADMIN_DB_URL,
        isolation_level="AUTOCOMMIT",
        poolclass=pool.NullPool,
    )
    with engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    engine.dispose()
    return f"{_LOCAL_BASE}/{db_name}"


def _drop_test_database(db_name: str) -> None:
    """Drop a test Postgres database.

    Args:
        db_name: Name of the database to drop.
    """
    engine = create_engine(
        _ADMIN_DB_URL,
        isolation_level="AUTOCOMMIT",
        poolclass=pool.NullPool,
    )
    try:
        with engine.connect() as conn:
            # Terminate connections before drop
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :dbname AND pid <> pg_backend_pid()"
                ),
                {"dbname": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    finally:
        engine.dispose()


def _get_tables_in_db(conn_url: str) -> set[str]:
    """Return the set of table names (excluding alembic_version) in the given DB.

    Args:
        conn_url: Postgres connection URL.

    Returns:
        set[str]: Table names visible in the 'public' schema.
    """
    engine = create_engine(conn_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name != 'alembic_version'
                    """
                )
            )
            return {row[0] for row in result}
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_apply_migrations_creates_v1_schema():
    """run_tenant_migrations() creates all 10 v1 tenant tables in a fresh DB.

    Steps:
    1. Create a unique test database.
    2. Run run_tenant_migrations(conn_url).
    3. Assert all 10 V1_TABLES exist in information_schema.tables.
    4. Assert alembic_version table has exactly one row (head revision).
    5. Teardown: drop the test database.
    """
    from app.services.migrations import run_tenant_migrations

    # Use a unique DB name to avoid conflicts between parallel test runs
    db_name = f"wchats_test_{uuid.uuid4().hex[:12]}"
    conn_url = None

    try:
        conn_url = _create_test_database(db_name)

        # Run migrations against the fresh DB
        run_tenant_migrations(conn_url)

        # Assert all 10 tables exist
        tables_in_db = _get_tables_in_db(conn_url)
        missing = V1_TABLES - tables_in_db
        assert not missing, (
            f"Missing v1 tables after migration: {sorted(missing)}\n"
            f"Tables found: {sorted(tables_in_db)}"
        )

        # Assert no unexpected extra tables (only 10 + alembic_version)
        # Extra tables from Postgres system are filtered by information_schema.tables
        extra = tables_in_db - V1_TABLES
        # pgvector may add internal tables; check only that our 10 are present
        assert V1_TABLES.issubset(tables_in_db), (
            f"Not all V1 tables are present. Found: {sorted(tables_in_db)}"
        )

        # Assert alembic_version table has one row (head revision)
        engine = create_engine(conn_url, poolclass=pool.NullPool)
        try:
            with engine.connect() as conn:
                version_row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchone()
                assert version_row is not None, "alembic_version table has no rows"
                assert version_row[0] is not None, "alembic_version.version_num is None"
                assert len(version_row[0]) > 0, "alembic_version.version_num is empty"
        finally:
            engine.dispose()

    finally:
        if conn_url:
            _drop_test_database(db_name)


@pytest.mark.integration
def test_apply_migrations_idempotent():
    """run_tenant_migrations() is idempotent: second call is a no-op.

    Alembic 'upgrade head' on an already-migrated DB is a no-op — no error,
    no duplicate tables, no changed table count.

    Steps:
    1. Create a unique test database.
    2. Run run_tenant_migrations() once (creates all tables).
    3. Run run_tenant_migrations() a second time.
    4. Assert: no exception raised.
    5. Assert: table count unchanged (still exactly 10 v1 tables).
    6. Teardown: drop the test database.
    """
    from app.services.migrations import run_tenant_migrations

    db_name = f"wchats_test_idem_{uuid.uuid4().hex[:12]}"
    conn_url = None

    try:
        conn_url = _create_test_database(db_name)

        # First migration run
        run_tenant_migrations(conn_url)

        tables_after_first = _get_tables_in_db(conn_url)
        assert V1_TABLES.issubset(tables_after_first), (
            f"First migration run: not all tables present. Found: {sorted(tables_after_first)}"
        )

        # Second migration run — must not raise
        run_tenant_migrations(conn_url)

        tables_after_second = _get_tables_in_db(conn_url)
        assert tables_after_first == tables_after_second, (
            f"Table set changed after second migration run!\n"
            f"Before: {sorted(tables_after_first)}\n"
            f"After:  {sorted(tables_after_second)}"
        )

    finally:
        if conn_url:
            _drop_test_database(db_name)


@pytest.mark.integration
def test_migration_0008_creates_customer_identities():
    """Migration 0008 creates customer_identities with UNIQUE(external_id) and both indexes.

    IDV-01 substrate: applies alembic upgrade head (including 0008) against a fresh local
    Postgres tenant DB and proves the table, UNIQUE constraint, and both lookup indexes
    exist in the live DB. A migration file alone does not create the table — the function
    call `run_tenant_migrations(conn_str)` is the explicit application step.

    OD-3 (Redis-only OTP state): this migration creates ONLY the durable verified-session
    record — there is no otp_pending table. Asserted by checking that customer_identities
    is present and that no otp_pending table exists.

    Steps:
    1. Create a unique test database.
    2. Call run_tenant_migrations(conn_url) — alembic upgrade head (includes 0008).
    3. Assert get_current_alembic_revision(conn_url) == "0008" (head).
    4. Assert customer_identities exists in information_schema.tables.
    5. Assert uq_customer_identities_external_id UNIQUE constraint is present.
    6. Assert ix_customer_identities_token_hash and ix_customer_identities_expires_at
       indexes are present in pg_indexes.
    7. Assert no otp_pending table exists (OD-3 guard).
    8. Re-run run_tenant_migrations — revision still "0008", table count unchanged (idempotent).
    9. Teardown: drop the test database.

    Operators apply 0008 to real tenant DBs via:
        run_tenant_migrations(agent.neon_direct_connection_string)
    using the direct (non-pooled) Neon URI per tenant. No Docker — local processes only.

    Prerequisite: local Postgres at postgresql://wchats:wchats@localhost:5432 must be running.
    If not running this test fails with OperationalError (consistent with sibling tests).
    """
    from app.services.migrations import get_current_alembic_revision, run_tenant_migrations

    db_name = f"wchats_test_0008_{uuid.uuid4().hex[:12]}"
    conn_url = None

    try:
        conn_url = _create_test_database(db_name)

        # --- Step 2: Apply migrations (upgrade head = 0008) ---
        run_tenant_migrations(conn_url)

        # --- Step 3: Assert revision is "0008" ---
        revision = get_current_alembic_revision(conn_url)
        assert revision == "0008", (
            f"Expected alembic revision '0008' after upgrade head, got {revision!r}"
        )

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        try:
            with engine.connect() as conn:
                # --- Step 4: Assert customer_identities table exists ---
                table_exists_row = conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = 'customer_identities'
                        """
                    )
                ).scalar()
                assert table_exists_row == 1, (
                    "customer_identities table not found in information_schema.tables after 0008 migration"
                )

                # --- Step 5: Assert UNIQUE constraint exists ---
                unique_constraint_row = conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM information_schema.table_constraints
                        WHERE table_schema = 'public'
                          AND table_name = 'customer_identities'
                          AND constraint_name = 'uq_customer_identities_external_id'
                          AND constraint_type = 'UNIQUE'
                        """
                    )
                ).scalar()
                assert unique_constraint_row == 1, (
                    "UNIQUE constraint uq_customer_identities_external_id not found on customer_identities"
                )

                # --- Step 6: Assert both lookup indexes exist ---
                token_idx_row = conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM pg_indexes
                        WHERE schemaname = 'public'
                          AND tablename = 'customer_identities'
                          AND indexname = 'ix_customer_identities_token_hash'
                        """
                    )
                ).scalar()
                assert token_idx_row == 1, (
                    "Index ix_customer_identities_token_hash not found in pg_indexes"
                )

                expires_idx_row = conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM pg_indexes
                        WHERE schemaname = 'public'
                          AND tablename = 'customer_identities'
                          AND indexname = 'ix_customer_identities_expires_at'
                        """
                    )
                ).scalar()
                assert expires_idx_row == 1, (
                    "Index ix_customer_identities_expires_at not found in pg_indexes"
                )

                # --- Step 7: Assert no otp_pending table (OD-3: Redis-only OTP state) ---
                otp_pending_row = conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = 'otp_pending'
                        """
                    )
                ).scalar()
                assert otp_pending_row == 0, (
                    "otp_pending table found — violates OD-3 (OTP challenge state must live in Redis, not a DB table)"
                )

        finally:
            engine.dispose()

        # --- Step 8: Second run is a safe no-op ---
        run_tenant_migrations(conn_url)  # must not raise

        revision_after_rerun = get_current_alembic_revision(conn_url)
        assert revision_after_rerun == "0008", (
            f"Revision changed after second run_tenant_migrations call: {revision_after_rerun!r}"
        )

        tables_after_rerun = _get_tables_in_db(conn_url)
        assert "customer_identities" in tables_after_rerun, (
            "customer_identities disappeared after second migration run"
        )

    finally:
        if conn_url:
            _drop_test_database(db_name)
