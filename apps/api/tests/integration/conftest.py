"""
Integration test fixtures for Veridian API.

These fixtures use a REAL local Postgres (not mocked) for DB operations.
They do NOT set CELERY_TASK_ALWAYS_EAGER=True — integration tests require
a real Celery worker subprocess (see RESEARCH.md Pitfall 7).

The global conftest.py sets CELERY_TASK_ALWAYS_EAGER=True via setdefault().
This module explicitly overrides it to "False" for integration tests BEFORE
any app module imports happen.

DB URL: postgresql://veridian:veridian@localhost:5432/veridian_control
Each test creates unique tenant/agent/job rows (UUID-keyed) and tears them
down in finally blocks (T-07-01 mitigation).

Redis URL: redis://localhost:6379/0 (default from env)
"""

import base64
import os
import subprocess
import time
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# ---------------------------------------------------------------------------
# Override env vars for integration tests BEFORE any app import.
# The root conftest sets CELERY_TASK_ALWAYS_EAGER via setdefault(); we
# explicitly set it to "False" here so the integration worker process does
# not run tasks eagerly.
# ---------------------------------------------------------------------------
_INTEGRATION_DB_URL = os.environ.get(
    "INTEGRATION_DB_URL",
    "postgresql://veridian:veridian@localhost:5432/veridian_control",
)
_INTEGRATION_DB_SYNC_URL = _INTEGRATION_DB_URL  # already sync (psycopg2)
_INTEGRATION_DB_ASYNC_URL = _INTEGRATION_DB_URL.replace(
    "postgresql://", "postgresql+asyncpg://"
)

os.environ["CELERY_TASK_ALWAYS_EAGER"] = "False"
os.environ["CONTROL_DB_URL"] = _INTEGRATION_DB_ASYNC_URL
os.environ["CONTROL_DB_SYNC_URL"] = _INTEGRATION_DB_SYNC_URL

# Generate a stable Fernet key for integration test session (same key used by
# the worker subprocess — both read from environment).
if "NEON_ENCRYPTION_KEY" not in os.environ:
    os.environ["NEON_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
        os.urandom(32)
    ).decode()

os.environ.setdefault("NEON_API_KEY", "test_neon_key_integration")
os.environ.setdefault("ADMIN_KEY", "vrd_admin_test_key_for_tests_only")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Sync SQLAlchemy engine for integration tests
# ---------------------------------------------------------------------------
_sync_engine = create_engine(
    _INTEGRATION_DB_SYNC_URL,
    pool_pre_ping=True,
)
_SyncSession = sessionmaker(_sync_engine)


@contextmanager
def integration_db_session():
    """Context manager yielding a sync SQLAlchemy Session against the real test DB."""
    with _SyncSession() as session:
        yield session


# ---------------------------------------------------------------------------
# Fixtures for creating and tearing down test data
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def db_session():
    """Yields a sync Session for integration test use.

    The caller is responsible for inserting and cleaning up rows.
    """
    with _SyncSession() as session:
        yield session


@pytest.fixture(scope="function")
def test_tenant(db_session: Session):
    """Create a real Tenant row in the test DB. Tears down in finally block.

    Returns:
        tuple: (tenant_id: UUID, raw_api_key: str)
    """
    from app.core.security import generate_api_key, hash_api_key

    tenant_id = uuid.uuid4()
    raw_key = generate_api_key()
    api_key_hash = hash_api_key(raw_key)

    db_session.execute(
        text(
            """
            INSERT INTO tenants (id, name, api_key, created_at)
            VALUES (:id, :name, :api_key, now())
            """
        ),
        {"id": str(tenant_id), "name": f"test-tenant-{tenant_id}", "api_key": api_key_hash},
    )
    db_session.commit()

    yield tenant_id, raw_key

    try:
        # Teardown: delete events, jobs, agents, then tenant in dependency order
        db_session.execute(
            text("DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE tenant_id = :tid)"),
            {"tid": str(tenant_id)},
        )
        db_session.execute(
            text("DELETE FROM jobs WHERE tenant_id = :tid"),
            {"tid": str(tenant_id)},
        )
        db_session.execute(
            text("DELETE FROM agents WHERE tenant_id = :tid"),
            {"tid": str(tenant_id)},
        )
        db_session.execute(
            text("DELETE FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
        db_session.commit()
    except Exception:
        db_session.rollback()


@pytest.fixture(scope="function")
def test_agent_and_job(db_session: Session, test_tenant):
    """Create real Agent + Job rows for the test tenant. Tears down in finally block.

    Returns:
        tuple: (tenant_id: UUID, agent_id: UUID, job_id: UUID)
    """
    tenant_id, _ = test_tenant
    agent_id = uuid.uuid4()
    job_id = uuid.uuid4()

    soul = {"tone": "professional", "language": "en"}

    db_session.execute(
        text(
            """
            INSERT INTO agents (id, tenant_id, name, soul, role, status, created_at)
            VALUES (:id, :tenant_id, :name, :soul::jsonb, :role, 'pending', now())
            """
        ),
        {
            "id": str(agent_id),
            "tenant_id": str(tenant_id),
            "name": f"test-agent-{agent_id}",
            "soul": '{"tone": "professional", "language": "en"}',
            "role": "support",
        },
    )

    db_session.execute(
        text(
            """
            INSERT INTO jobs (id, tenant_id, agent_id, kind, status, created_at)
            VALUES (:id, :tenant_id, :agent_id, 'provision', 'pending', now())
            """
        ),
        {
            "id": str(job_id),
            "tenant_id": str(tenant_id),
            "agent_id": str(agent_id),
        },
    )
    db_session.commit()

    yield tenant_id, agent_id, job_id


# ---------------------------------------------------------------------------
# Celery worker subprocess fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def celery_worker():
    """Start a real Celery worker subprocess for integration tests.

    Uses the pipeline queue with concurrency=1 so task ordering is deterministic.
    CELERY_TASK_ALWAYS_EAGER must NOT be set to True in this worker process
    (see RESEARCH.md Pitfall 7).

    The worker inherits the current process environment (including env vars set
    above for test DB/Redis URLs and encryption key).

    Yields:
        subprocess.Popen: The worker process handle.
    """
    env = os.environ.copy()
    # Explicitly unset CELERY_TASK_ALWAYS_EAGER in worker process
    env["CELERY_TASK_ALWAYS_EAGER"] = "False"

    proc = subprocess.Popen(
        [
            "celery",
            "-A",
            "app.worker.celery_app",
            "worker",
            "--queues=pipeline",
            "--concurrency=1",
            "--loglevel=warning",
        ],
        cwd="/c/Users/Bantu/mzansi-agentive/veridian/apps/api",
        env=env,
    )
    # Wait for worker to become ready (allow time for imports + broker connect)
    time.sleep(4)
    yield proc
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
