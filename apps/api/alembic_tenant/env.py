"""
Alembic env.py for per-tenant Neon DBs.

Supports two modes:
1. CLI mode: uses engine_from_config with NullPool (direct connection, no pooler)
2. Programmatic/Celery mode: uses config.attributes['connection'] injection

IMPORTANT (Research §Pitfall 1): Alembic migrations MUST use the direct (non-pooled)
connection string, not the PgBouncer pooler endpoint. PgBouncer in transaction mode
does not support DDL advisory locks or CREATE EXTENSION.

Tenant DBs do not have ORM models in M1 (schema is managed via raw DDL in the
migration file). target_metadata is set to None — all migrations use op.execute().

Research source: RESEARCH.md §Pattern 5
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# target_metadata is None for tenant DBs in M1
# All schema changes are expressed as raw DDL in op.execute() calls.
# ---------------------------------------------------------------------------
target_metadata = None

# ---------------------------------------------------------------------------
# Alembic config
# ---------------------------------------------------------------------------
config = context.config

# Wire up Python logging from alembic.ini (if config file exists)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """
    Run migrations without a live DB connection (generates SQL to stdout).
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations with a live DB connection.

    If config.attributes['connection'] is set (programmatic/Celery mode),
    use that connection directly.  Otherwise create a new engine from
    alembic.ini's sqlalchemy.url (CLI mode) with NullPool.

    IMPORTANT: Always use the DIRECT (non-pooled) connection string here.
    The pooled endpoint does not support DDL transactions.
    """
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        # CLI mode — build engine from config, use NullPool for Neon direct connection
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
            )
            with context.begin_transaction():
                context.run_migrations()
    else:
        # Programmatic mode — use the injected connection directly
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
