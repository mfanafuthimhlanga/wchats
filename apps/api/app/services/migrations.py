"""
Programmatic Alembic migration service for Veridian tenant DBs.

Provides:
    run_tenant_migrations         — run alembic upgrade head against a tenant DB.
    get_current_alembic_revision  — return the current revision applied to a tenant DB.

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
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, pool

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
    alembic_cfg.set_main_option("sqlalchemy.url", conn_string)

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
    """Return the current Alembic revision ID applied to the tenant DB.

    Args:
        conn_string: DIRECT (non-pooled) connection URI for the tenant Neon project.

    Returns:
        Revision ID string (e.g. "ae1027a6acf2") or None if no migration has run.
    """
    engine = create_engine(conn_string, poolclass=pool.NullPool)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        rev = ctx.get_current_revision()
    engine.dispose()
    return rev
