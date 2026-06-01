"""
Alembic env.py for the W Chats control DB.

Supports two modes:
1. CLI mode: uses engine_from_config with NullPool (no connection pooling for
   migrations — safer for Neon direct connections)
2. Programmatic/Celery mode: uses config.attributes['connection'] injection
   (Celery tasks inject a live connection so they can manage the transaction)

Research source: RESEARCH.md §Pattern 5
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Make app importable from this env.py (works regardless of CWD)
# ---------------------------------------------------------------------------
_api_root = Path(__file__).parent.parent
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))

# Import the shared Base so Alembic can autogenerate diffs against ORM metadata
from app.models import Base  # noqa: E402

# ---------------------------------------------------------------------------
# Alembic config
# ---------------------------------------------------------------------------
config = context.config
target_metadata = Base.metadata

# Wire CONTROL_DB_SYNC_URL into alembic config for CLI mode.
# alembic.ini intentionally has no sqlalchemy.url; the URL comes from
# the environment so the same ini works across local, Docker, and CI.
if "CONTROL_DB_SYNC_URL" in os.environ:
    config.set_main_option("sqlalchemy.url", os.environ["CONTROL_DB_SYNC_URL"])

# Wire up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """
    Run migrations without a live DB connection (generates SQL to stdout).
    Used only during development for SQL review.
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
    alembic.ini's sqlalchemy.url (CLI mode) using NullPool to avoid
    connection-pool issues with Neon direct connections.
    """
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        # CLI mode — build engine from config, use NullPool
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
