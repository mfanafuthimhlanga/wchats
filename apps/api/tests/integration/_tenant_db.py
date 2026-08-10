"""Create and drop throwaway tenant databases on the local Postgres.

Kept out of ``conftest.py`` for the same reason as ``_paths.py``: importing
``tests.integration.conftest`` from elsewhere would execute that module's
environment mutations as an import side effect.

Why a separate database rather than reusing ``wchats_control``
--------------------------------------------------------------
The old mocks handed ``INTEGRATION_DB_URL`` (the control DB) back as the
tenant connection URI. That was never exercised, because provisioning died at
the Neon 401 long before ``apply_migrations`` ran. It cannot work: both chains
use Alembic's default ``alembic_version`` table (no ``version_table`` override
in ``alembic_tenant/env.py``), and ``wchats_control`` already holds the control
chain's head there. Running the tenant chain against it would either fail with
"Can't locate revision" or corrupt the control DB's migration state.

A per-module throwaway database instead means ``apply_migrations`` runs the
real tenant chain, all the way to head, against real Postgres with pgvector.

Nothing here touches the environment or the network at import time.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, pool, text

# Same env-var overrides test_migrations.py already uses, so a machine with
# non-default local credentials configures both from one place.
ADMIN_DB_URL = os.getenv(
    "TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres"
)
LOCAL_BASE = os.getenv("TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432")


def create_tenant_database(db_name: str) -> str:
    """CREATE DATABASE *db_name* and return its connection URL."""
    engine = create_engine(
        ADMIN_DB_URL, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
    )
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        engine.dispose()
    return f"{LOCAL_BASE}/{db_name}"


def drop_tenant_database(db_name: str) -> None:
    """DROP DATABASE *db_name*, terminating live backends first.

    The worker subprocess may still hold a connection when teardown runs, so
    the backends are terminated before the drop; otherwise Postgres refuses
    with "database is being accessed by other users".
    """
    engine = create_engine(
        ADMIN_DB_URL, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
    )
    try:
        with engine.connect() as conn:
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
