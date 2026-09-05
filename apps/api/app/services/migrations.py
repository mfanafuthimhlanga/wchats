"""
Programmatic Alembic migration service for the W Chats control and tenant DBs.

Provides:
    run_control_migrations        runs alembic upgrade head against the control DB.
    run_tenant_migrations         — run alembic upgrade head against a tenant DB.
    get_current_alembic_revision  returns the current revision applied to either.

MUST use a DIRECT (non-pooled) connection string:
    PgBouncer in transaction-pooling mode does not support DDL advisory locks or
    SET search_path, causing Alembic to fail or silently skip migrations.
    Use the direct URI stored in agent.neon_direct_connection_string, never the
    pooled URI from agent.neon_connection_string. (RESEARCH.md Pitfall 1)

script_location:
    Absolute path derived from this file's location — never a CWD-relative string.
    Celery workers run from arbitrary working directories; a relative "alembic_tenant"
    path would resolve to different locations per worker. (RESEARCH.md §Open Questions Q2)

Connection injection:
    alembic_cfg.attributes["connection"] is set before command.upgrade() is called.
    The tenant alembic_tenant/env.py reads this attribute and uses it instead of
    creating its own engine — guaranteeing NullPool behaviour for migrations.
    (RESEARCH.md §Pattern 5)
"""

from pathlib import Path

import structlog
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, pool

from alembic import command

log = structlog.get_logger(__name__)

# Absolute path to the alembic_tenant directory.
# __file__ = apps/api/app/services/migrations.py
# .parent           → apps/api/app/services/
# .parent.parent    → apps/api/app/
# .parent.parent.parent → apps/api/
# .parent.parent.parent.parent → apps/  (incorrect — we want apps/api/alembic_tenant/)
#
# Correct chain:
# __file__                            = apps/api/app/services/migrations.py
# .parent                             = apps/api/app/services
# .parent.parent                      = apps/api/app
# .parent.parent.parent               = apps/api
# / "alembic_tenant"                  = apps/api/alembic_tenant  ← correct
_ALEMBIC_TENANT_DIR: Path = Path(__file__).parent.parent.parent / "alembic_tenant"

# The CONTROL DB's Alembic directory, the one `alembic.ini` names as
# `script_location = alembic`. Absolute for the same reason as the tenant path
# above: a release step and a Celery worker both run from working directories
# nobody chose.
_ALEMBIC_CONTROL_DIR: Path = Path(__file__).parent.parent.parent / "alembic"


def run_control_migrations(conn_string: str) -> None:
    """Run ``alembic upgrade head`` against the CONTROL DB at *conn_string*.

    The programmatic equivalent of ``alembic -c alembic.ini upgrade head``, and
    it differs from that line in two ways that both matter to a release step:

        `script_location` is absolute, so the working directory the release
        step happens to start in cannot change which migrations run.

        The connection is injected rather than named, so the credential never
        reaches the Alembic ``Config`` object where debug logging can print it
        (T-03-02). `alembic/env.py` already prefers an injected connection over
        `sqlalchemy.url`, which is the same branch `run_tenant_migrations` uses.

    Args:
        conn_string: sync (psycopg2) URI for the control database.

    Raises:
        alembic.util.exc.CommandError: On migration script errors.
        sqlalchemy.exc.OperationalError: If the DB is unreachable.
    """
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(_ALEMBIC_CONTROL_DIR))

    engine = create_engine(conn_string, poolclass=pool.NullPool)

    log.info("migrations.control_starting", script_location=str(_ALEMBIC_CONTROL_DIR))

    try:
        # One transaction for the whole upgrade. PostgreSQL DDL is
        # transactional and no control migration opens an autocommit block, so
        # a control DB that failed part-way through is back where it started
        # rather than half-migrated under new code.
        with engine.begin() as connection:
            alembic_cfg.attributes["connection"] = connection
            command.upgrade(alembic_cfg, "head")
    finally:
        engine.dispose()

    log.info("migrations.control_finished")


def run_tenant_migrations(conn_string: str) -> None:
    """Run ``alembic upgrade head`` against the tenant DB at *conn_string*.

    Args:
        conn_string: DIRECT (non-pooled) connection URI for the tenant Neon project.
                     Caller is responsible for passing the direct URI, not the pooled
                     one. Running Alembic through a PgBouncer pooler causes silent
                     migration failures. (RESEARCH.md Pitfall 1)

    Raises:
        alembic.util.exc.CommandError: On migration script errors.
        sqlalchemy.exc.OperationalError: If the DB is unreachable.
    """
    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(_ALEMBIC_TENANT_DIR))
    # Do NOT set sqlalchemy.url — connection is injected via attributes["connection"]
    # below. Setting sqlalchemy.url stores the plaintext credential in the Config
    # object where Alembic debug logging can expose it (T-03-02).

    # NullPool: short-lived migration connection; no pooling overhead.
    engine = create_engine(conn_string, poolclass=pool.NullPool)

    log.info(
        "migrations.starting",
        script_location=str(_ALEMBIC_TENANT_DIR),
    )

    with engine.begin() as connection:
        # Inject the live connection so alembic_tenant/env.py uses it directly
        # rather than creating its own engine. This is the cfg.attributes["connection"]
        # pattern documented in RESEARCH.md §Pattern 5.
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")

    engine.dispose()

    log.info("migrations.finished")


def get_current_alembic_revision(conn_string: str) -> str | None:
    """Return the current Alembic revision ID applied to the DB at *conn_string*.

    Args:
        conn_string: sync URI for the control DB, or the DIRECT (non-pooled)
                     connection URI for a tenant Neon project.

    Returns:
        Revision ID string (e.g. "ae1027a6acf2") or None if no migration has run.
    """
    engine = create_engine(conn_string, poolclass=pool.NullPool)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        rev = ctx.get_current_revision()
    engine.dispose()
    return rev
