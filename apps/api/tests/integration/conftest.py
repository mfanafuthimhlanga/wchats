"""
Integration test fixtures for W Chats API.

These fixtures use a REAL local Postgres (not mocked) for DB operations.
They do NOT set CELERY_TASK_ALWAYS_EAGER=True — integration tests require
a real Celery worker subprocess (see RESEARCH.md Pitfall 7).

The global conftest.py sets CELERY_TASK_ALWAYS_EAGER=True via setdefault().
This module explicitly overrides it to "False" for integration tests BEFORE
any app module imports happen.

DB URL: postgresql://wchats:wchats@localhost:5432/wchats_control
Each test creates unique tenant/agent/job rows (UUID-keyed) and tears them
down in finally blocks (T-07-01 mitigation).

Redis URL: redis://localhost:6379/0 (default from env)
"""

import base64
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tests.integration._paths import api_root
from tests.integration._tenant_db import create_tenant_database, drop_tenant_database

# ---------------------------------------------------------------------------
# Override env vars for integration tests BEFORE any app import.
# The root conftest sets CELERY_TASK_ALWAYS_EAGER via setdefault(); we
# explicitly set it to "False" here so the integration worker process does
# not run tasks eagerly.
# ---------------------------------------------------------------------------
_INTEGRATION_DB_URL = os.environ.get(
    "INTEGRATION_DB_URL",
    "postgresql://wchats:wchats@localhost:5432/wchats_control",
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
            INSERT INTO tenants (id, name, api_key_hash, created_at)
            VALUES (:id, :name, :api_key_hash, now())
            """
        ),
        {"id": str(tenant_id), "name": f"test-tenant-{tenant_id}", "api_key_hash": api_key_hash},
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

    db_session.execute(
        text(
            """
            INSERT INTO agents (id, tenant_id, name, soul, role, status, created_at)
            VALUES (:id, :tenant_id, :name, CAST(:soul AS jsonb), :role, 'pending', now())
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
    proc = _spawn_pipeline_worker()
    # Wait for worker to become ready (allow time for imports + broker connect)
    time.sleep(4)
    yield proc
    _stop_worker(proc)


def _spawn_pipeline_worker(
    extra_args: list[str] | None = None, extra_env: dict | None = None
) -> subprocess.Popen:
    """Start a pipeline-queue Celery worker subprocess.

    Args:
        extra_args: Extra CLI arguments appended to the worker command line.
        extra_env:  Extra environment variables for the worker process only.
    """
    env = os.environ.copy()
    # Explicitly unset CELERY_TASK_ALWAYS_EAGER in worker process
    env["CELERY_TASK_ALWAYS_EAGER"] = "False"
    env.update(extra_env or {})

    return subprocess.Popen(
        [
            # `sys.executable -m celery`, not a bare "celery". The console script
            # lives in .venv/Scripts/ and is only on PATH when the venv is
            # activated, so the bare name raised FileNotFoundError on every
            # unactivated run — which is every run, since the repo's own gate
            # invokes .venv/Scripts/python.exe directly. Same defect class as the
            # hardcoded cwd below: one layer was fixed, the next was never seen
            # because this fixture had never reached a live database.
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.worker.celery_app",
            "worker",
            "--queues=pipeline",
            "--concurrency=1",
            "--loglevel=warning",
            *(extra_args or []),
        ],
        # Derived from this file's location, not hardcoded: the previous literal
        # was one developer's machine under the project's *former* name, so this
        # fixture raised FileNotFoundError everywhere else, CI included.
        cwd=str(api_root()),
        env=env,
    )


def _stop_worker(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


# ---------------------------------------------------------------------------
# Celery worker with the Neon API stubbed *inside the worker process*
# ---------------------------------------------------------------------------


class NeonStubWorker:
    """Handle onto a worker whose Neon transport is stubbed.

    Exposes the stub's call journal so a test can assert on what the worker
    actually sent to the Neon boundary — the previous respx-based tests claimed
    to check the call count and never did, because the mock was in the wrong
    process and patched the wrong HTTP library.
    """

    def __init__(self, proc: subprocess.Popen, log_path: str, tenant_db_url: str):
        self.proc = proc
        self.log_path = log_path
        self.tenant_db_url = tenant_db_url

    def records(self) -> list[dict]:
        """Every record the stub has written so far, oldest first."""
        try:
            with open(self.log_path, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except FileNotFoundError:
            return []

    def mark(self) -> int:
        """Current journal length, for scoping assertions to one test."""
        return len(self.records())

    def calls_since(self, mark: int) -> list[dict]:
        """Neon calls recorded after *mark* (excludes the install record)."""
        return [r for r in self.records()[mark:] if r.get("event") == "call"]

    def wait_until_installed(self, timeout: float = 60.0) -> None:
        """Block until the stub reports itself installed in the worker process.

        This is the assertion that makes the stubbed tests honest. If the
        `--include` module failed to load, the worker would otherwise come up
        without the stub and every Neon call would go to the real API. Here
        that is a hard failure with the worker's exit status attached, never a
        silent live call.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(r.get("event") == "installed" for r in self.records()):
                return
            if self.proc.poll() is not None:
                pytest.fail(
                    f"Neon stub worker exited with code {self.proc.returncode} before "
                    f"the stub reported installed. tests.integration._neon_stub did "
                    f"not load; no test may run against the real Neon API."
                )
            time.sleep(0.25)
        pytest.fail(
            f"tests.integration._neon_stub never reported installed within {timeout}s "
            f"(journal: {self.log_path}). Refusing to run — an un-stubbed worker "
            f"would call the real Neon API."
        )

    def wait_until_accepting_tasks(self, timeout: float = 60.0) -> None:
        """Block until the worker answers a Celery control ping.

        Replaces the fixed sleep used by the plain `celery_worker` fixture:
        a dispatch sent before the consumer is up sits in the queue and burns
        the test's polling budget instead of failing for a real reason.
        """
        from app.worker.celery_app import celery_app

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if celery_app.control.ping(timeout=1.0):
                    return
            except Exception:
                pass
            if self.proc.poll() is not None:
                pytest.fail(
                    f"Neon stub worker exited with code {self.proc.returncode} "
                    f"before accepting tasks."
                )
            time.sleep(0.5)
        pytest.fail(f"Neon stub worker did not accept tasks within {timeout}s")


@pytest.fixture(scope="module")
def neon_stub_worker(tmp_path_factory):
    """Celery worker whose Neon API transport is stubbed in-process.

    Provisioning tests need three things that no single-process mock can give:
    the task runs in a *subprocess*, the Neon client is `requests` (not httpx),
    and `apply_migrations` needs a real database to migrate. So this fixture

      1. creates a throwaway local tenant database (dropped in teardown),
      2. starts the worker with `--include=tests.integration._neon_stub`, whose
         canned connection URI points at that database, and
      3. refuses to yield until the stub confirms it is installed.

    Belt and braces on the safety rule: the worker's NEON_API_KEY is
    overwritten with a placeholder that cannot authenticate. If the stub ever
    failed to load, step 3 fails the test first — and even if it did not, the
    worker holds no credential capable of creating a real Neon project.
    """
    db_name = f"wchats_stub_tenant_{uuid.uuid4().hex[:12]}"
    log_path = str(tmp_path_factory.mktemp("neon_stub") / "neon_calls.jsonl")
    tenant_db_url = create_tenant_database(db_name)
    proc = None
    try:
        proc = _spawn_pipeline_worker(
            extra_args=["--include=tests.integration._neon_stub"],
            extra_env={
                "WCHATS_NEON_STUB_URI": tenant_db_url,
                "WCHATS_NEON_STUB_LOG": log_path,
                "NEON_API_KEY": "stub-key-never-valid-never-sent",
            },
        )
        handle = NeonStubWorker(proc, log_path, tenant_db_url)
        handle.wait_until_installed()
        handle.wait_until_accepting_tasks()
        yield handle
    finally:
        if proc is not None:
            _stop_worker(proc)
        drop_tenant_database(db_name)
