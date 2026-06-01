"""
Nightly E2E test — real Neon provisioning.

This test creates a REAL Neon project via the Neon API (no respx mock).
It requires:
  - NEON_API_KEY env var pointing to a real Neon test account key
  - A running local Postgres (wchats_control, with migrations applied)
  - A running local Redis

Teardown always deletes the Neon project in a finally block (T-08-02 mitigation).

Run with:
  pytest tests/e2e/test_neon_e2e.py -m e2e --tb=short

Do NOT run in CI without the NEON_API_KEY_TEST secret configured.
"""

import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from typing import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# ---------------------------------------------------------------------------
# E2E-specific constants
# ---------------------------------------------------------------------------
_E2E_DB_SYNC_URL = os.environ.get(
    "CONTROL_DB_SYNC_URL",
    "postgresql://wchats:wchats@localhost:5432/wchats_control",
)

_REQUIRED_TENANT_TABLES = [
    "documents",
    "chunks",
    "embeddings",
    "chunk_metadata",
    "conversations",
    "messages",
    "tool_calls",
    "eval_runs",
    "eval_results",
    "red_team_runs",
]

_EXPECTED_EVENTS = [
    "job.started",
    "neon.project.creating",
    "neon.project.ready",
    "migrations.running",
    "migrations.complete",
    "job.complete",
]

# ---------------------------------------------------------------------------
# Sync DB engine for E2E tests
# ---------------------------------------------------------------------------
_e2e_engine = create_engine(_E2E_DB_SYNC_URL, pool_pre_ping=True)
_E2ESession = sessionmaker(_e2e_engine)


@contextmanager
def e2e_db_session() -> Generator[Session, None, None]:
    with _E2ESession() as session:
        yield session


# ---------------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_create_agent_real_neon() -> None:
    """Full end-to-end: create agent → real Neon provisioning → schema validated.

    Steps:
    1. Bootstrap tenant and agent rows in local Postgres.
    2. Start a real Celery worker subprocess.
    3. Dispatch provision_neon → apply_migrations chain (no respx mock).
    4. Poll agent.status until 'ready' or 120s timeout.
    5. Assert neon_project_id set (real Neon project ID).
    6. Assert schema_version set.
    7. Assert all 6 events in job_events with correct event_type values.
    8. Connect to real tenant DB and verify all 10 tables exist.
    9. Teardown: delete the Neon project in a finally block.
    """
    from app.core.security import generate_api_key, hash_api_key

    neon_project_id: str | None = None

    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    job_id = uuid.uuid4()

    # Label the project name with 'e2e' so the nightly teardown script in
    # the workflow can identify and delete orphaned projects.
    agent_name = f"e2e-agent-{agent_id}"

    try:
        # ------------------------------------------------------------------
        # 1. Bootstrap test data in local Postgres
        # ------------------------------------------------------------------
        raw_key = generate_api_key()
        api_key_hash = hash_api_key(raw_key)

        with e2e_db_session() as db:
            db.execute(
                text(
                    """
                    INSERT INTO tenants (id, name, api_key, created_at)
                    VALUES (:id, :name, :api_key, now())
                    """
                ),
                {
                    "id": str(tenant_id),
                    "name": f"e2e-tenant-{tenant_id}",
                    "api_key": api_key_hash,
                },
            )
            db.execute(
                text(
                    """
                    INSERT INTO agents (id, tenant_id, name, soul, role, status, created_at)
                    VALUES (:id, :tenant_id, :name, :soul::jsonb, :role, 'pending', now())
                    """
                ),
                {
                    "id": str(agent_id),
                    "tenant_id": str(tenant_id),
                    "name": agent_name,
                    "soul": '{"tone": "professional", "language": "en"}',
                    "role": "support",
                },
            )
            db.execute(
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
            db.commit()

        # ------------------------------------------------------------------
        # 2. Start a real Celery worker subprocess
        # ------------------------------------------------------------------
        worker_env = os.environ.copy()
        worker_env["CELERY_TASK_ALWAYS_EAGER"] = "False"

        api_dir = os.path.join(os.path.dirname(__file__), "..", "..")
        api_dir = os.path.abspath(api_dir)

        worker_proc = subprocess.Popen(
            [
                "celery",
                "-A",
                "app.worker.celery_app",
                "worker",
                "--queues=pipeline",
                "--concurrency=1",
                "--loglevel=warning",
            ],
            cwd=api_dir,
            env=worker_env,
        )

        try:
            # Give worker time to start and connect to broker
            time.sleep(5)

            # ------------------------------------------------------------------
            # 3. Dispatch the Celery chain
            # ------------------------------------------------------------------
            from celery import chain
            from app.worker.tasks.pipeline.provision import provision_neon
            from app.worker.tasks.pipeline.migrations import apply_migrations

            chain(
                provision_neon.s(str(tenant_id), str(agent_id)),
                apply_migrations.s(),
            ).apply_async(queue="pipeline")

            # ------------------------------------------------------------------
            # 4. Poll until agent.status == 'ready' (up to 120s)
            # ------------------------------------------------------------------
            deadline = time.time() + 120
            agent_status = "pending"
            while time.time() < deadline:
                time.sleep(5)
                with e2e_db_session() as db:
                    row = db.execute(
                        text(
                            "SELECT status, neon_project_id, schema_version "
                            "FROM agents WHERE id = :id"
                        ),
                        {"id": str(agent_id)},
                    ).fetchone()
                if row:
                    agent_status = row[0]
                    if agent_status in ("ready", "failed"):
                        break

            assert agent_status == "ready", (
                f"Agent did not reach 'ready' within 120s; final status: {agent_status}"
            )

            # ------------------------------------------------------------------
            # 5 & 6. Assert neon_project_id and schema_version are set
            # ------------------------------------------------------------------
            with e2e_db_session() as db:
                agent_row = db.execute(
                    text(
                        "SELECT neon_project_id, schema_version "
                        "FROM agents WHERE id = :id"
                    ),
                    {"id": str(agent_id)},
                ).fetchone()

            assert agent_row is not None
            neon_project_id = agent_row[0]
            assert neon_project_id, "neon_project_id must be set after provisioning"
            assert agent_row[1], "schema_version must be set after apply_migrations"

            # ------------------------------------------------------------------
            # 7. Assert all 6 events in job_events in correct order
            # ------------------------------------------------------------------
            with e2e_db_session() as db:
                events = db.execute(
                    text(
                        "SELECT event_type FROM job_events "
                        "WHERE job_id = :job_id "
                        "ORDER BY created_at ASC"
                    ),
                    {"job_id": str(job_id)},
                ).fetchall()

            event_types = [row[0] for row in events]
            assert event_types == _EXPECTED_EVENTS, (
                f"Expected events {_EXPECTED_EVENTS}, got {event_types}"
            )

            # ------------------------------------------------------------------
            # 8. Connect to tenant DB and verify all 10 tables exist
            # ------------------------------------------------------------------
            with e2e_db_session() as db:
                conn_row = db.execute(
                    text(
                        "SELECT neon_connection_string FROM agents WHERE id = :id"
                    ),
                    {"id": str(agent_id)},
                ).fetchone()

            assert conn_row and conn_row[0], "neon_connection_string must be stored"

            # Decrypt the connection string
            from app.core.security import fernet_decrypt

            raw_conn_str = fernet_decrypt(conn_row[0])
            # Convert to asyncpg/sync psycopg2 URL for validation
            sync_conn_str = raw_conn_str
            if "postgresql+asyncpg://" in sync_conn_str:
                sync_conn_str = sync_conn_str.replace(
                    "postgresql+asyncpg://", "postgresql://"
                )

            tenant_engine = create_engine(sync_conn_str, pool_pre_ping=True)
            try:
                with tenant_engine.connect() as conn:
                    for table_name in _REQUIRED_TENANT_TABLES:
                        result = conn.execute(
                            text(
                                "SELECT COUNT(*) FROM information_schema.tables "
                                "WHERE table_schema = 'public' AND table_name = :table"
                            ),
                            {"table": table_name},
                        ).scalar()
                        assert result == 1, (
                            f"Tenant table '{table_name}' missing from real Neon DB"
                        )
            finally:
                tenant_engine.dispose()

        finally:
            # Stop Celery worker
            try:
                worker_proc.terminate()
                worker_proc.wait(timeout=15)
            except Exception:
                worker_proc.kill()

    finally:
        # ------------------------------------------------------------------
        # 9. Teardown: always delete the real Neon project (T-08-02 mitigation)
        # ------------------------------------------------------------------
        if neon_project_id:
            try:
                from neon_api import NeonAPI

                client = NeonAPI(api_key=os.environ["NEON_API_KEY"])
                client.project_delete(neon_project_id)
            except Exception as exc:
                # Log but do not fail — the nightly workflow has a second
                # teardown step via the workflow's "if: always()" step.
                print(
                    f"WARNING: Could not delete Neon project {neon_project_id}: {exc}"
                )

        # Clean up control DB rows
        try:
            with e2e_db_session() as db:
                db.execute(
                    text(
                        "DELETE FROM job_events WHERE job_id IN "
                        "(SELECT id FROM jobs WHERE tenant_id = :tid)"
                    ),
                    {"tid": str(tenant_id)},
                )
                db.execute(
                    text("DELETE FROM jobs WHERE tenant_id = :tid"),
                    {"tid": str(tenant_id)},
                )
                db.execute(
                    text("DELETE FROM agents WHERE tenant_id = :tid"),
                    {"tid": str(tenant_id)},
                )
                db.execute(
                    text("DELETE FROM tenants WHERE id = :tid"),
                    {"tid": str(tenant_id)},
                )
                db.commit()
        except Exception as db_exc:
            print(f"WARNING: Control DB teardown failed: {db_exc}")
