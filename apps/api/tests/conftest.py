"""
Shared pytest fixtures for W Chats API unit and integration tests.

Environment variables are set at module level, BEFORE any app modules
are imported, to prevent pydantic-settings validation errors.

CELERY_TASK_ALWAYS_EAGER=True enables synchronous task execution in
unit tests without needing a running Celery worker.

Fixtures:
    anyio_backend        — force asyncio backend for anyio-based async tests
    mock_redis           — sync MagicMock Redis client for unit tests
    mock_async_redis     — async MagicMock Redis client for FastAPI route tests
    mock_db_session      — sync MagicMock SQLAlchemy Session for unit tests
    sample_tenant_id     — random UUID for tenant fixture data
    sample_agent_id      — random UUID for agent fixture data
    sample_job_id        — random UUID for job fixture data
"""

import base64
import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Set required environment variables BEFORE any app module is imported.
# This block must remain at module level and must run before any `from app`
# or `import app` statement anywhere in the test suite.
# ---------------------------------------------------------------------------
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault(
    "NEON_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(os.urandom(32)).decode(),
)
os.environ.setdefault(
    "CONTROL_DB_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test_wchats",
)
os.environ.setdefault(
    "CONTROL_DB_SYNC_URL",
    "postgresql://test:test@localhost:5432/test_wchats",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("ADMIN_KEY", "vrd_admin_test_key_for_tests_only")

# M2 ingestion pipeline keys — must be set before any app import (pydantic-settings)
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")

# M5: Langfuse — set before any import so module-level Langfuse() init in
# validation_service.py does not raise on missing keys during test discovery.
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "test-pk")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "test-sk")
os.environ.setdefault("LANGFUSE_HOST", "http://localhost:3000")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("MAX_UPLOAD_SIZE_MB", "50")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-tests-only")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")

# M4.1: Clerk JWKS URL — must be set explicitly to prevent the config default
# (https://api.clerk.com/v1/jwks) from being used. The default generic URL
# does not contain the instance-specific signing key; any token signed by an
# instance-specific Clerk key will fail verification (InvalidTokenError → 401)
# if this is not overridden. Unit tests that exercise the JWT path should mock
# verify_clerk_jwt directly; this value prevents the lru_cache from being
# poisoned with the generic URL for tests that import app.main early.
os.environ.setdefault("CLERK_JWKS_URL", "https://test.clerk.accounts.dev/.well-known/jwks.json")

# Celery eager mode — tasks run synchronously in the test process,
# no running worker required (unit tests only; integration tests override this).
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "True")


# ---------------------------------------------------------------------------
# Async backend declaration
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def anyio_backend():
    """Force asyncio backend for anyio-based async fixtures."""
    return "asyncio"


# ---------------------------------------------------------------------------
# Redis mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    """Sync Mock Redis client for unit tests (replaces redis.from_url)."""
    with patch("redis.from_url") as mock:
        r = MagicMock()
        mock.return_value = r
        yield r


@pytest.fixture
def mock_async_redis():
    """Async Mock Redis client for FastAPI route tests (replaces get_async_redis)."""
    r = AsyncMock()
    r.ping.return_value = True
    r.subscribe = AsyncMock()
    r.aclose = AsyncMock()
    return r


# ---------------------------------------------------------------------------
# DB session mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db_session():
    """Sync Mock SQLAlchemy Session for unit tests.

    Configured so that ``with session:`` context-manager usage works correctly.
    """
    session = MagicMock(spec=Session)
    session.__enter__ = lambda s: s
    session.__exit__ = MagicMock(return_value=False)
    return session


# ---------------------------------------------------------------------------
# UUID fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_tenant_id():
    """Random UUID representing a test tenant."""
    return uuid4()


@pytest.fixture
def sample_agent_id():
    """Random UUID representing a test agent."""
    return uuid4()


@pytest.fixture
def sample_job_id():
    """Random UUID representing a test job."""
    return uuid4()
