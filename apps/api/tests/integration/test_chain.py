"""
Integration tests: provision_neon → apply_migrations end-to-end flow.

These tests use:
- A REAL local Postgres control DB and a REAL throwaway tenant database.
- The Neon API stubbed inside the worker process (tests/integration/_neon_stub.py),
  loaded via `celery worker --include` by the `neon_stub_worker` fixture.
- A real Celery worker subprocess.
- .apply_async() dispatch (NEVER CELERY_TASK_ALWAYS_EAGER).
- DB polling with a timeout to detect completion.

Note: apply_migrations is dispatched by provision_neon directly inside the task
body (not via Celery chain). This avoids the Windows billiard issue #299 where
the chain callback mechanism triggers select.select() on a stale broker connection.

What is real here and what is not
---------------------------------
Only Neon's HTTP transport is stubbed. The connection URI it returns points at a
real, empty Postgres database created for the module, so apply_migrations runs
the *real* tenant Alembic chain to head against real Postgres with pgvector, and
"ready" means the schema genuinely landed.

The previous version handed back `INTEGRATION_DB_URL` — the control DB — as the
tenant URI. That could never have worked: both chains use Alembic's default
`alembic_version` table, and the control DB already holds the control head
there. It was never noticed because provisioning died at a Neon 401 (the respx
mock patched httpx, while the Neon client uses requests, in another process
entirely) long before migrations ran.

Acceptance criteria:
- test_full_chain_completes: agent.status == "ready", job.status == "complete"
- test_event_sequence_in_order: all 6 events in exact order in job_events table
"""

import time
import uuid

import pytest
from sqlalchemy import create_engine, pool, text

pytestmark = pytest.mark.integration


def _poll_for_agent_status(db_session, agent_id: uuid.UUID, expected_status: str, timeout: int = 60):
    """Poll the agents table until agent.status == expected_status or timeout.

    Args:
        db_session: SQLAlchemy sync Session.
        agent_id: UUID of the agent to poll.
        expected_status: Expected final status string.
        timeout: Maximum seconds to wait.

    Returns:
        str: Final agent status.

    Raises:
        AssertionError: If timeout is exceeded without reaching expected_status.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = db_session.execute(
            text("SELECT status FROM agents WHERE id = :id"),
            {"id": str(agent_id)},
        ).fetchone()
        if row and row[0] == expected_status:
            return row[0]
        if row and row[0] == "failed":
            # Don't keep polling on failure — return early for clean assertion
            return row[0]
        time.sleep(1)
        db_session.expire_all()

    # Timeout — fetch current status for error message
    row = db_session.execute(
        text("SELECT status FROM agents WHERE id = :id"),
        {"id": str(agent_id)},
    ).fetchone()
    current_status = row[0] if row else "NOT FOUND"
    pytest.fail(
        f"Chain did not reach '{expected_status}' within {timeout}s. "
        f"Current agent.status='{current_status}'"
    )


def _job_error(db_session, job_id: uuid.UUID) -> str | None:
    """The job's recorded error, so a red run says why rather than just 'failed'."""
    row = db_session.execute(
        text("SELECT error FROM jobs WHERE id = :id"), {"id": str(job_id)}
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_full_chain_completes(neon_stub_worker, test_agent_and_job, db_session):
    """Full chain integration test: dispatches via .apply_async(), polls DB for
    agent.status='ready'.

    Verifies:
    - agent.status == "ready" after chain
    - job.status == "complete" after chain
    - 6 events in job_events table for this job_id
    - the tenant schema actually exists in the tenant DB (a "ready" agent whose
      migrations did nothing is the failure this assertion closes off)
    """
    from app.worker.tasks.pipeline.provision import provision_neon

    tenant_id, agent_id, job_id = test_agent_and_job

    provision_neon.apply_async(
        args=[str(tenant_id), str(agent_id)],
        queue="pipeline",
    )

    final_status = _poll_for_agent_status(db_session, agent_id, "ready", timeout=120)
    assert final_status == "ready", (
        f"agent.status should be 'ready', got '{final_status}' "
        f"(job.error={_job_error(db_session, job_id)!r})"
    )

    # Verify job.status == "complete"
    job_row = db_session.execute(
        text("SELECT status FROM jobs WHERE id = :id"),
        {"id": str(job_id)},
    ).fetchone()
    assert job_row is not None, "Job row not found"
    assert job_row[0] == "complete", f"job.status should be 'complete', got '{job_row[0]}'"

    # Verify 6 events in job_events for this job
    events_result = db_session.execute(
        text(
            "SELECT event_type FROM job_events WHERE job_id = :job_id ORDER BY created_at, id"
        ),
        {"job_id": str(job_id)},
    ).fetchall()
    assert len(events_result) == 6, (
        f"Expected 6 job_events, got {len(events_result)}: {[r[0] for r in events_result]}"
    )

    # schema_version is the head revision apply_migrations recorded; the tenant
    # DB must actually carry it.
    schema_version = db_session.execute(
        text("SELECT schema_version FROM agents WHERE id = :id"),
        {"id": str(agent_id)},
    ).scalar()
    assert schema_version, "schema_version must be recorded once migrations complete"

    engine = create_engine(neon_stub_worker.tenant_db_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            applied = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            tables = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                ).fetchall()
            }
    finally:
        engine.dispose()

    assert applied == schema_version, (
        f"tenant DB is at revision {applied!r} but the agent row records {schema_version!r}"
    )
    for expected_table in ("documents", "chunks", "embeddings", "conversations", "messages"):
        assert expected_table in tables, (
            f"tenant table '{expected_table}' missing after a 'ready' agent"
        )


@pytest.mark.integration
def test_event_sequence_in_order(neon_stub_worker, test_agent_and_job, db_session):
    """Verify all 6 SSE events are emitted in the exact required order.

    Expected order (CONTEXT.md §Event Emission Pattern):
        job.started → neon.project.creating → neon.project.ready →
        migrations.running → migrations.complete → job.complete
    """
    from app.worker.tasks.pipeline.provision import provision_neon

    tenant_id, agent_id, job_id = test_agent_and_job

    provision_neon.apply_async(
        args=[str(tenant_id), str(agent_id)],
        queue="pipeline",
    )

    _poll_for_agent_status(db_session, agent_id, "ready", timeout=120)

    # Verify event sequence in exact order
    events_result = db_session.execute(
        text(
            "SELECT event_type FROM job_events WHERE job_id = :job_id ORDER BY created_at, id"
        ),
        {"job_id": str(job_id)},
    ).fetchall()

    event_types = [r[0] for r in events_result]
    expected = [
        "job.started",
        "neon.project.creating",
        "neon.project.ready",
        "migrations.running",
        "migrations.complete",
        "job.complete",
    ]
    assert event_types == expected, (
        f"Event sequence mismatch.\n"
        f"Expected: {expected}\n"
        f"Got:      {event_types}\n"
        f"job.error: {_job_error(db_session, job_id)!r}"
    )
