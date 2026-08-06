"""
Integration tests: provision_neon → apply_migrations end-to-end flow.

These tests use:
- A REAL local Postgres DB (not mocked).
- respx 0.23.1 to mock all Neon API HTTP calls.
- A real Celery worker subprocess started by the celery_worker fixture.
- .apply_async() dispatch (NEVER CELERY_TASK_ALWAYS_EAGER).
- DB polling with a 30s timeout to detect completion.

Note: apply_migrations is dispatched by provision_neon directly inside the task
body (not via Celery chain). This avoids the Windows billiard issue #299 where
the chain callback mechanism triggers select.select() on a stale broker connection.

Acceptance criteria:
- test_full_chain_completes: agent.status == "ready", job.status == "complete"
- test_event_sequence_in_order: all 6 events in exact order in job_events table
"""

import time
import uuid

import pytest
import respx
from httpx import Response
from sqlalchemy import text

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Neon API mock helpers
# ---------------------------------------------------------------------------


def _neon_mock_routes(project_id: str, local_db_url: str):
    """Register respx routes that simulate all Neon API calls in provision_neon.

    The mock Neon project returns the local Postgres URL as both pooled and
    direct URIs so that apply_migrations can actually run Alembic against the
    local test DB (which already has the control schema from startup migrations).

    Args:
        project_id: Fake Neon project ID to return.
        local_db_url: Local Postgres URL to return as the "connection URI".
                      apply_migrations will run Alembic against this DB.
    """
    # POST /projects — create project
    respx.post("https://console.neon.tech/api/v2/projects").mock(
        return_value=Response(
            200,
            json={
                "project": {
                    "id": project_id,
                    "name": "vrd-test",
                    "region_id": "aws-us-east-1",
                }
            },
        )
    )

    # GET /projects/{id}/operations — all finished
    respx.get(
        url=f"https://console.neon.tech/api/v2/projects/{project_id}/operations"
    ).mock(
        return_value=Response(
            200,
            json={
                "operations": [
                    {"id": "op-1", "status": "finished"},
                    {"id": "op-2", "status": "finished"},
                ]
            },
        )
    )

    # GET /projects/{id}/connection_uri?pooled=true — pooled endpoint
    respx.get(
        url__regex=rf"https://console\.neon\.tech/api/v2/projects/{project_id}/connection_uri.*pooled=true.*"
    ).mock(
        return_value=Response(
            200,
            json={"uri": local_db_url},
        )
    )

    # GET /projects/{id}/connection_uri?pooled=false — direct endpoint
    respx.get(
        url__regex=rf"https://console\.neon\.tech/api/v2/projects/{project_id}/connection_uri.*pooled=false.*"
    ).mock(
        return_value=Response(
            200,
            json={"uri": local_db_url},
        )
    )


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_full_chain_completes(celery_worker, test_agent_and_job, db_session):
    """Full chain integration test: dispatches via .apply_async(), polls DB for
    agent.status='ready'.

    Verifies:
    - agent.status == "ready" after chain
    - job.status == "complete" after chain
    - 6 events in job_events table for this job_id
    """
    from app.worker.tasks.pipeline.provision import provision_neon

    tenant_id, agent_id, job_id = test_agent_and_job

    # Local Postgres URL used as the fake Neon connection URI
    # The Alembic migration will run against a test-specific schema
    import os
    local_db_url = os.environ.get(
        "INTEGRATION_DB_URL",
        "postgresql://wchats:wchats@localhost:5432/wchats_control",
    )

    fake_project_id = f"test-project-{uuid.uuid4().hex[:8]}"

    with respx.mock(assert_all_called=False):
        _neon_mock_routes(fake_project_id, local_db_url)

        # provision_neon dispatches apply_migrations internally — just dispatch provision_neon.
        provision_neon.apply_async(
            args=[str(tenant_id), str(agent_id)],
            queue="pipeline",
        )

    # Poll DB for agent.status == "ready" (30s timeout)
    final_status = _poll_for_agent_status(db_session, agent_id, "ready", timeout=60)
    assert final_status == "ready", f"agent.status should be 'ready', got '{final_status}'"

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


@pytest.mark.integration
def test_event_sequence_in_order(celery_worker, test_agent_and_job, db_session):
    """Verify all 6 SSE events are emitted in the exact required order.

    Expected order (CONTEXT.md §Event Emission Pattern):
        job.started → neon.project.creating → neon.project.ready →
        migrations.running → migrations.complete → job.complete
    """
    from app.worker.tasks.pipeline.provision import provision_neon

    tenant_id, agent_id, job_id = test_agent_and_job

    import os
    local_db_url = os.environ.get(
        "INTEGRATION_DB_URL",
        "postgresql://wchats:wchats@localhost:5432/wchats_control",
    )
    fake_project_id = f"test-project-{uuid.uuid4().hex[:8]}"

    with respx.mock(assert_all_called=False):
        _neon_mock_routes(fake_project_id, local_db_url)

        # provision_neon dispatches apply_migrations internally — just dispatch provision_neon.
        provision_neon.apply_async(
            args=[str(tenant_id), str(agent_id)],
            queue="pipeline",
        )

    # Poll for completion
    _poll_for_agent_status(db_session, agent_id, "ready", timeout=60)

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
        f"Got:      {event_types}"
    )
