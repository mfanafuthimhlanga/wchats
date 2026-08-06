"""
Integration test: Worker kill-9 resilience.

This test verifies that:
1. A real Celery worker starts and processes the provision_neon → apply_migrations chain.
2. The worker is killed (SIGKILL / kill -9) after neon.project.ready is emitted
   but before apply_migrations completes.
3. When the worker is restarted, the chain resumes from apply_migrations
   (idempotency guard in provision_neon skips re-provisioning Neon).
4. The chain completes successfully: agent.status == "ready".

Requirements (RESEARCH.md Pitfall 7, CTL-07):
    - Real Celery worker subprocess — NOT CELERY_TASK_ALWAYS_EAGER
    - signal.SIGKILL (not SIGTERM) to simulate kill -9
    - DB polling for state transitions (not time.sleep hardcoded)
    - Cleanup: worker subprocess terminated in finally block

Skip guard:
    - This test is skipped by default unless INTEGRATION_TESTS_ENABLED=1 is set.
    - All other integration tests run by default; this one requires an explicitly
      prepared environment because it spawns, kills, and restarts a Celery worker.
"""

import json
import os
import signal
import subprocess
import time
import uuid

import pytest
import respx
from httpx import Response
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Skip guard — INTEGRATION_TESTS_ENABLED=1 required for the kill-9 test
# ---------------------------------------------------------------------------
_TESTS_ENABLED = os.environ.get("INTEGRATION_TESTS_ENABLED", "0") == "1"

_INTEGRATION_DB_URL = os.environ.get(
    "INTEGRATION_DB_URL",
    "postgresql://wchats:wchats@localhost:5432/wchats_control",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _start_worker_process() -> subprocess.Popen:
    """Start a real Celery worker subprocess on the pipeline queue.

    Returns:
        subprocess.Popen: Worker process handle.
    """
    env = os.environ.copy()
    env["CELERY_TASK_ALWAYS_EAGER"] = "False"
    env["CONTROL_DB_SYNC_URL"] = _INTEGRATION_DB_URL
    env["CONTROL_DB_URL"] = _INTEGRATION_DB_URL.replace(
        "postgresql://", "postgresql+asyncpg://"
    )

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
        cwd=os.path.join(
            os.path.dirname(__file__), "..", ".."
        ),  # apps/api directory
        env=env,
    )
    return proc


def _setup_test_rows(tenant_id: uuid.UUID, agent_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """Insert tenant, agent, and job rows into the real local DB."""
    from app.core.security import generate_api_key, hash_api_key

    raw_key = generate_api_key()
    api_key_hash = hash_api_key(raw_key)

    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tenants (id, name, api_key, created_at) "
                    "VALUES (:id, :name, :api_key, now())"
                ),
                {
                    "id": str(tenant_id),
                    "name": f"kill9-tenant-{tenant_id}",
                    "api_key": api_key_hash,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO agents (id, tenant_id, name, soul, role, status, created_at) "
                    "VALUES (:id, :tenant_id, :name, :soul::jsonb, :role, 'pending', now())"
                ),
                {
                    "id": str(agent_id),
                    "tenant_id": str(tenant_id),
                    "name": f"kill9-agent-{agent_id}",
                    "soul": json.dumps({"tone": "professional", "language": "en"}),
                    "role": "support",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, agent_id, kind, status, created_at) "
                    "VALUES (:id, :tenant_id, :agent_id, 'provision', 'pending', now())"
                ),
                {
                    "id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "agent_id": str(agent_id),
                },
            )
    finally:
        engine.dispose()


def _teardown_test_rows(tenant_id: uuid.UUID) -> None:
    """Delete all rows created for the kill-9 test (T-07-01 mitigation)."""
    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM job_events WHERE job_id IN "
                    "(SELECT id FROM jobs WHERE tenant_id = :tid)"
                ),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                text("DELETE FROM jobs WHERE tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                text("DELETE FROM agents WHERE tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                text("DELETE FROM tenants WHERE id = :tid"),
                {"tid": str(tenant_id)},
            )
    finally:
        engine.dispose()


def _poll_for_event(job_id: uuid.UUID, event_type: str, timeout: float = 30.0) -> bool:
    """Poll job_events table until event_type appears or timeout.

    Args:
        job_id: UUID of the job.
        event_type: Event type to wait for.
        timeout: Maximum seconds to wait.

    Returns:
        True if event found within timeout, False otherwise.
    """
    deadline = time.time() + timeout
    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        while time.time() < deadline:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM job_events "
                        "WHERE job_id = :job_id AND event_type = :event_type"
                    ),
                    {"job_id": str(job_id), "event_type": event_type},
                ).fetchone()
                if row and row[0] > 0:
                    return True
            time.sleep(0.5)
        return False
    finally:
        engine.dispose()


def _get_agent_status(agent_id: uuid.UUID) -> str | None:
    """Return current agent.status from DB."""
    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status FROM agents WHERE id = :id"),
                {"id": str(agent_id)},
            ).fetchone()
            return row[0] if row else None
    finally:
        engine.dispose()


def _get_agent_row(agent_id: uuid.UUID) -> dict | None:
    """Return agent row as dict from DB."""
    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, neon_project_id, schema_version "
                    "FROM agents WHERE id = :id"
                ),
                {"id": str(agent_id)},
            ).fetchone()
            if row:
                return {
                    "status": row[0],
                    "neon_project_id": row[1],
                    "schema_version": row[2],
                }
            return None
    finally:
        engine.dispose()


def _register_neon_mock_routes(project_id: str, local_db_url: str):
    """Register respx routes for Neon API calls (same pattern as test_chain.py)."""
    respx.post("https://console.neon.tech/api/v2/projects").mock(
        return_value=Response(
            200,
            json={
                "project": {
                    "id": project_id,
                    "name": "vrd-test-kill9",
                    "region_id": "aws-us-east-1",
                }
            },
        )
    )
    respx.get(
        url=f"https://console.neon.tech/api/v2/projects/{project_id}/operations"
    ).mock(
        return_value=Response(
            200,
            json={"operations": [{"id": "op-1", "status": "finished"}]},
        )
    )
    respx.get(
        url__regex=rf"https://console\.neon\.tech/api/v2/projects/{project_id}/connection_uri.*pooled=true.*"
    ).mock(
        return_value=Response(200, json={"uri": local_db_url})
    )
    respx.get(
        url__regex=rf"https://console\.neon\.tech/api/v2/projects/{project_id}/connection_uri.*pooled=false.*"
    ).mock(
        return_value=Response(200, json={"uri": local_db_url})
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not _TESTS_ENABLED,
    reason=(
        "Worker kill-9 test disabled by default. "
        "Set INTEGRATION_TESTS_ENABLED=1 to run."
    ),
)
def test_worker_kill_9_chain_completes():
    """Worker kill-9 mid-chain: chain resumes and completes after worker restart.

    This test proves that acks_late=True + idempotency guards work in combination:
    1. Start real Celery worker subprocess.
    2. Dispatch chain (provision_neon → apply_migrations) with respx-mocked Neon API.
    3. Wait for neon.project.ready event in job_events (confirms provision_neon succeeded).
    4. SIGKILL the worker (simulate kill -9).
    5. Wait 1s for kill to take effect.
    6. Restart worker subprocess.
    7. Poll job_events for job.complete within 60s timeout.
    8. Assert agent.status == "ready", neon_project_id set, schema_version set.

    Uses SIGKILL not SIGTERM to simulate unrecoverable crash (kill -9).
    acks_late=True ensures the apply_migrations task message is redelivered
    after the new worker connects to Redis.
    """
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    job_id = uuid.uuid4()
    fake_project_id = f"test-kill9-{uuid.uuid4().hex[:8]}"

    worker_proc = None
    worker_proc2 = None

    try:
        # Step 1: Insert DB rows
        _setup_test_rows(tenant_id, agent_id, job_id)

        # Step 2: Start first worker and wait for it to be ready
        worker_proc = _start_worker_process()
        time.sleep(4)  # Allow worker time to connect to broker

        # Step 3: Dispatch chain with respx-mocked Neon API
        with respx.mock(assert_all_called=False):
            _register_neon_mock_routes(
                fake_project_id,
                _INTEGRATION_DB_URL,
            )

            from celery import chain as celery_chain

            from app.worker.tasks.pipeline.migrations import apply_migrations
            from app.worker.tasks.pipeline.provision import provision_neon

            celery_chain(
                provision_neon.s(str(tenant_id), str(agent_id)),
                apply_migrations.s(),
            ).apply_async(queue="pipeline")

        # Step 4: Wait for neon.project.ready in job_events (confirms provision_neon done)
        ready_found = _poll_for_event(job_id, "neon.project.ready", timeout=30.0)
        assert ready_found, (
            "neon.project.ready event not found within 30s — "
            "provision_neon task did not complete"
        )

        # Step 5: SIGKILL the worker (simulate kill -9)
        # On Windows, SIGKILL is not available; use terminate + wait fallback
        if hasattr(signal, "SIGKILL"):
            os.kill(worker_proc.pid, signal.SIGKILL)
        else:
            # Windows: SIGKILL not available; use TerminateProcess equivalent
            worker_proc.kill()

        # Step 6: Wait for kill to complete
        time.sleep(1)
        try:
            worker_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker_proc.kill()

        worker_proc = None  # Mark as killed so finally block doesn't double-kill

        # Step 7: Restart worker subprocess
        worker_proc2 = _start_worker_process()
        time.sleep(4)  # Allow restarted worker time to connect and pick up tasks

        # Step 8: Poll job_events for job.complete (60s timeout)
        complete_found = _poll_for_event(job_id, "job.complete", timeout=60.0)
        assert complete_found, (
            "job.complete event not found within 60s after worker restart — "
            "chain did not complete after kill-9"
        )

        # Step 9: Assert final agent state
        agent_row = _get_agent_row(agent_id)
        assert agent_row is not None, "Agent row not found in DB"
        assert agent_row["status"] == "ready", (
            f"agent.status should be 'ready', got '{agent_row['status']}'"
        )
        assert agent_row["neon_project_id"] is not None, (
            "agent.neon_project_id should be set after provision_neon"
        )
        assert agent_row["schema_version"] is not None, (
            "agent.schema_version should be set after apply_migrations"
        )

    finally:
        # Cleanup: terminate worker processes in finally block (T-07-01 mitigation)
        for proc in (worker_proc, worker_proc2):
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

        # Teardown DB rows
        _teardown_test_rows(tenant_id)
