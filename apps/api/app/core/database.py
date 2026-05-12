"""
SQLAlchemy engine and session factories.

Async engine (asyncpg) — for FastAPI route handlers.
Sync engine (psycopg2) — for Celery tasks and Alembic CLI.

T-01-04: pool_pre_ping=True on both engines so stale connections are detected
         silently instead of surfacing DSN details in exception messages.
"""

from collections.abc import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# ---------------------------------------------------------------------------
# Async engine — FastAPI (asyncpg driver)
# ---------------------------------------------------------------------------
async_engine = create_async_engine(
    settings.CONTROL_DB_URL,  # postgresql+asyncpg://...
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    async_engine,
    expire_on_commit=False,
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async SQLAlchemy session."""
    async with AsyncSessionFactory() as session:
        yield session


# ---------------------------------------------------------------------------
# Sync engine — Celery tasks, Alembic programmatic API (psycopg2 driver)
# ---------------------------------------------------------------------------
sync_engine = create_engine(
    settings.CONTROL_DB_SYNC_URL,  # postgresql://...
    pool_pre_ping=True,
    echo=False,
)

SyncSessionFactory: sessionmaker[Session] = sessionmaker(
    sync_engine,
    expire_on_commit=False,
)


def get_sync_db() -> Generator[Session, None, None]:
    """Celery task helper: yields a sync SQLAlchemy session."""
    with SyncSessionFactory() as session:
        yield session
