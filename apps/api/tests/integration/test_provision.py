"""
Integration tests: provision_neon against a stubbed Neon API boundary.

The Neon transport is stubbed **inside the Celery worker subprocess** by
``tests/integration/_neon_stub.py`` (loaded via ``celery worker --include``);
everything else is real — real local Postgres for the control DB, a real
throwaway tenant database, a real Celery worker, real ``requests`` machinery up
to the socket.

Why not respx (what these tests used to do)
-------------------------------------------
The previous version wrapped each dispatch in ``respx.mock(...)``. That mock
never intercepted anything: respx patches ``httpx`` while ``app/services/neon.py``
uses ``requests``, and the mock lived in the pytest process while the task runs
in the worker subprocess. Both tests were making real, unauthenticated calls to
console.neon.tech and failing on the resulting 401. Exporting a working key
would not have fixed them — it would have made every run create real, billable
Neon projects with no teardown anywhere in the file.

Why stub rather than provision for real
---------------------------------------
Neither of these tests asserts a property of Neon. They assert properties of
*our* code: that the idempotency guard creates exactly one project, and that the
connection string is encrypted at rest as BYTEA. Running them against the live
API would create one project per test — and ``test_provision_neon_idempotency``
dispatches twice on purpose, so the exact failure it exists to catch (a broken
guard) is the run that leaks a second real project. Real-Neon coverage lives in
``tests/e2e/test_neon_e2e.py`` behind ``-m e2e``, which is deselected here.

Tests:
    test_provision_neon_idempotency
        — Neon POST /projects is called exactly once even when provision_neon is
          dispatched twice for the same agent. Asserted against the stub's call
          journal, which is the check the old docstring promised and the old
          code never performed.

    test_provision_neon_stores_encrypted_connection_string
        — agent.neon_connection_string is BYTEA that decrypts to the URI the
          Neon boundary returned.
"""

import time
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def _poll_for_neon_project_id(db_session, agent_id: uuid.UUID, timeout: int = 30):
    """Poll until agent.neon_project_id is set (not None) or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = db_session.execute(
            text("SELECT neon_project_id, status FROM agents WHERE id = :id"),
            {"id": str(agent_id)},
        ).fetchone()
        if row and row[0] is not None:
            return row[0]
        if row and row[1] == "failed":
            pytest.fail("agent.status became 'failed' — provision_neon did not succeed")
        time.sleep(1)
        db_session.expire_all()

    pytest.fail(f"agent.neon_project_id was not set within {timeout}s")


def _poll_for_connection_string(db_session, agent_id: uuid.UUID, timeout: int = 30):
    """Poll until agent.neon_connection_string is not None or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = db_session.execute(
            text(
                "SELECT neon_connection_string, status FROM agents WHERE id = :id"
            ),
            {"id": str(agent_id)},
        ).fetchone()
        if row and row[0] is not None:
            return row[0]  # bytes
        if row and row[1] == "failed":
            pytest.fail("agent.status became 'failed' — provision_neon did not succeed")
        time.sleep(1)
        db_session.expire_all()

    pytest.fail(f"agent.neon_connection_string was not set within {timeout}s")


def _project_creates(calls: list[dict]) -> list[dict]:
    """Only the project-creation calls out of a stub call journal slice."""
    return [c for c in calls if c["method"] == "POST" and c["path"] == "/api/v2/projects"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_provision_neon_idempotency(neon_stub_worker, test_agent_and_job, db_session):
    """Verify Neon is asked to create a project exactly once across two dispatches.

    Idempotency contract in provision_neon: if agent.neon_project_id and
    agent.neon_connection_string are already set, the task returns early without
    touching the Neon API (RESEARCH.md Pitfall 2).

    Asserts:
    - exactly one POST /api/v2/projects reaches the Neon boundary
    - agent.neon_project_id equals the id that single create returned
    """
    from app.worker.tasks.pipeline.provision import provision_neon

    tenant_id, agent_id, job_id = test_agent_and_job
    mark = neon_stub_worker.mark()

    # First dispatch
    provision_neon.apply_async(args=(str(tenant_id), str(agent_id)), queue="pipeline")
    _poll_for_neon_project_id(db_session, agent_id, timeout=30)
    # The guard only short-circuits once BOTH project_id and connection string
    # are stored, so wait for the second write before re-dispatching.
    _poll_for_connection_string(db_session, agent_id, timeout=30)

    # Second dispatch — must hit the idempotency guard and call nothing
    provision_neon.apply_async(args=(str(tenant_id), str(agent_id)), queue="pipeline")
    time.sleep(5)

    creates = _project_creates(neon_stub_worker.calls_since(mark))
    assert len(creates) == 1, (
        f"Neon project creation must happen exactly once across both dispatches; "
        f"the stub recorded {len(creates)}: {[c['project_id'] for c in creates]}"
    )

    row = db_session.execute(
        text("SELECT neon_project_id FROM agents WHERE id = :id"),
        {"id": str(agent_id)},
    ).fetchone()
    assert row is not None
    assert row[0] == creates[0]["project_id"], (
        f"Stored project_id {row[0]} is not the one the single create returned "
        f"({creates[0]['project_id']})"
    )


@pytest.mark.integration
def test_provision_neon_stores_encrypted_connection_string(
    neon_stub_worker, test_agent_and_job, db_session
):
    """Verify provision_neon stores the connection string encrypted, as bytes.

    After provision_neon completes:
    - agent.neon_connection_string is BYTEA (not str, not None)
    - fernet_decrypt(...) returns exactly the URI the Neon boundary handed back,
      proving the value was encrypted in transit to the DB and not mangled.
    """
    from app.core.security import fernet_decrypt
    from app.worker.tasks.pipeline.provision import provision_neon

    tenant_id, agent_id, job_id = test_agent_and_job
    mark = neon_stub_worker.mark()

    provision_neon.apply_async(args=(str(tenant_id), str(agent_id)), queue="pipeline")
    _poll_for_connection_string(db_session, agent_id, timeout=30)

    row = db_session.execute(
        text("SELECT neon_connection_string FROM agents WHERE id = :id"),
        {"id": str(agent_id)},
    ).fetchone()
    assert row is not None
    stored = row[0]

    # Must be bytes (BYTEA), not str
    assert isinstance(stored, (bytes, memoryview)), (
        f"neon_connection_string should be bytes, got {type(stored)}"
    )
    if isinstance(stored, memoryview):
        stored = bytes(stored)

    # Ciphertext, not the plaintext URI sitting in a BYTEA column.
    assert neon_stub_worker.tenant_db_url.encode() not in stored, (
        "connection string was stored verbatim — it is not encrypted at rest"
    )

    decrypted = fernet_decrypt(stored)
    assert decrypted == neon_stub_worker.tenant_db_url, (
        "decrypted connection string does not match the URI Neon returned"
    )

    # Both URIs must be fetched: pooled for app traffic, direct for Alembic
    # (RESEARCH.md Pitfall 1). Asserted at the boundary, not inferred.
    pooled_flags = {
        c.get("pooled")
        for c in neon_stub_worker.calls_since(mark)
        if c["path"].endswith("/connection_uri")
    }
    assert pooled_flags == {"true", "false"}, (
        f"expected both pooled and direct connection_uri fetches, got {pooled_flags}"
    )
