"""
Integration test: Worker kill-9 resilience.

This test verifies that:
1. A real Celery worker starts and processes the provision_neon → apply_migrations chain.
2. The worker is killed (SIGKILL / kill -9) after neon.project.ready is emitted
   but before the chain completes.
3. When a new worker starts, the chain resumes (the idempotency guard in
   provision_neon skips re-provisioning Neon).
4. The chain completes successfully: agent.status == "ready".

Requirements (RESEARCH.md Pitfall 7, CTL-07):
    - Real Celery worker subprocess — NOT CELERY_TASK_ALWAYS_EAGER
    - signal.SIGKILL (not SIGTERM) to simulate kill -9
    - DB polling for state transitions (not time.sleep hardcoded)
    - Cleanup: worker subprocesses terminated in finally block

Why this file was rewritten (2026-08-11)
----------------------------------------
It carried three defects its two siblings had already been fixed for, and one
they never had:

1. **The Neon mock was inert.** ``_register_neon_mock_routes`` installed respx
   routes in the pytest process, while ``provision_neon`` runs in a worker
   subprocess and reaches Neon through ``requests``, not ``httpx``. respx
   patches httpx, in this process. Neither half could intercept anything, so
   every Neon call went to the live API — see ``tests/integration/_neon_stub.py``
   for the full diagnosis and the fix this file now uses.
2. **The control DB was handed back as the tenant connection URI.** Both
   Alembic chains use the default ``alembic_version`` table, so running the
   tenant chain against ``wchats_control`` either fails on a missing revision or
   corrupts the control DB's migration state. ``_tenant_db.py`` exists for this.
3. **The worker was spawned as a bare ``"celery"``.** The console script is on
   PATH only inside an activated venv, and this repo's own gate invokes
   ``.venv/Scripts/python.exe`` directly — so it raised FileNotFoundError on
   every run it was ever enabled for.
4. **There was no Neon teardown of any kind.** With a working API key exported,
   every run created a real, billable project and deleted nothing.

Together those made this the last un-stubbed provisioning dispatch in the tree,
one exported ``NEON_API_KEY`` away from creating real cloud databases with no
cleanup — behind an env flag another module's docstring tells people to set.
It now runs through ``neon_stub_worker_factory``, which fails closed: the worker
refuses to start unless the stub reports itself installed *in its own pid*, and
its ``NEON_API_KEY`` is overwritten with a placeholder that cannot authenticate.
Nothing here can reach console.neon.tech, so there is nothing to tear down.

Skip guard:
    - Skipped by default unless INTEGRATION_TESTS_ENABLED=1 is set.
    - All other integration tests run by default; this one spawns, kills and
      restarts Celery workers and takes minutes.
"""

import json
import os
import signal
import subprocess
import time
import uuid

import pytest
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
                    "INSERT INTO tenants (id, name, api_key_hash, created_at) "
                    "VALUES (:id, :name, :api_key_hash, now())"
                ),
                {
                    "id": str(tenant_id),
                    "name": f"kill9-tenant-{tenant_id}",
                    "api_key_hash": api_key_hash,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO agents (id, tenant_id, name, soul, role, status, created_at) "
                    "VALUES (:id, :tenant_id, :name, CAST(:soul AS jsonb), :role, 'pending', now())"
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
    """Poll job_events until event_type appears or timeout."""
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


def _poll_for_agent_status(
    agent_id: uuid.UUID, statuses: set[str], timeout: float = 60.0
) -> str | None:
    """Poll until agent.status is one of *statuses*, or timeout."""
    deadline = time.time() + timeout
    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        while time.time() < deadline:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT status FROM agents WHERE id = :id"),
                    {"id": str(agent_id)},
                ).fetchone()
                if row and row[0] in statuses:
                    return row[0]
            time.sleep(0.5)
        return None
    finally:
        engine.dispose()


def _get_agent_row(agent_id: uuid.UUID) -> dict | None:
    """Return the agent row as a dict from the control DB."""
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


def _project_creates(calls: list[dict]) -> list[dict]:
    """Only the project-creation calls out of a stub call journal slice."""
    return [c for c in calls if c["method"] == "POST" and c["path"] == "/api/v2/projects"]


def _unacked_messages_mentioning(agent_id: uuid.UUID) -> list[str]:
    """Raw kombu ``unacked`` entries whose payload names *agent_id*.

    This is how ``acks_late=True`` is OBSERVED rather than asserted from the
    decorator. kombu's Redis transport keeps every delivered-but-unacknowledged
    message in the ``unacked`` hash and removes it on ack. With ``acks_late``
    the ack happens after the task body returns, so a worker killed mid-task
    leaves its message there; with ``acks_late=False`` the ack lands at delivery
    and the entry is already gone before the task starts. Reading this hash
    after the kill therefore distinguishes the two settings — the decorator's
    presence in the source does not.
    """
    import redis as redis_lib

    client = redis_lib.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )
    try:
        return [
            raw
            for raw in client.hvals("unacked")
            if str(agent_id) in raw
        ]
    finally:
        client.close()


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
def test_worker_kill_9_chain_completes(neon_stub_worker_factory):
    """Worker kill-9 mid-chain: the chain resumes and completes on a new worker.

    Sequence:
    1. Start a stubbed-Neon Celery worker.
    2. Dispatch provision_neon -> apply_migrations.
    3. Wait for neon.project.ready — provision_neon has committed the project id
       and is about to run apply_migrations inline (provision.py, end of body).
    4. kill -9 the worker, mid-migration.
    5. Assert the premise: the chain had NOT already finished. A run where the
       kill lands after completion proves nothing, and must say so rather than
       report a pass.
    6. Assert the message is still UNACKED in the broker — this is the direct
       observation of acks_late=True (see _unacked_messages_mentioning).
    7. Start a second worker on the same broker, journal and tenant database,
       redeliver the task, and wait for agent.status == 'ready'.
    8. Assert Neon was asked to create a project EXACTLY ONCE across both
       workers — the idempotency claim, read off the stub's call journal rather
       than inferred from the agent row.

    SIGKILL, not SIGTERM: a terminating worker would finish its task and prove
    nothing. On Windows SIGKILL does not exist, so Popen.kill() (TerminateProcess)
    is used — equally unrecoverable, equally unacked.

    WHY REDELIVERY IS DISPATCHED BY THE TEST, NOT WAITED FOR. With acks_late the
    killed message stays in kombu's `unacked` hash and the broker re-queues it
    only after `visibility_timeout` — which this application sets to
    BROKER_VISIBILITY_TIMEOUT_S = 7200 (celery_app.py:75), because a full eval
    run can legitimately hold a task for 90 minutes. Waiting for the real
    redelivery therefore means waiting two hours, so the original form of this
    test — poll 60s for job.complete after the restart — could never have passed
    on any broker this application configures. Step 6 observes the unacked state
    that redelivery would act on; step 7 then performs the redelivery the broker
    would eventually perform. What is proven is the resumption and the
    idempotency guard, plus that the message was still owed an ack. What is NOT
    proven here is kombu's own re-queue timer.
    """
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    job_id = uuid.uuid4()

    first = neon_stub_worker_factory()
    mark = first.mark()

    try:
        _setup_test_rows(tenant_id, agent_id, job_id)

        from celery import chain as celery_chain

        from app.worker.tasks.pipeline.migrations import apply_migrations
        from app.worker.tasks.pipeline.provision import provision_neon

        celery_chain(
            provision_neon.s(str(tenant_id), str(agent_id)),
            apply_migrations.s(),
        ).apply_async(queue="pipeline")

        assert _poll_for_event(job_id, "neon.project.ready", timeout=180.0), (
            "neon.project.ready not found within 180s — provision_neon never "
            "reached the point this test kills the worker after"
        )

        # kill -9 the worker that did the provisioning.
        if hasattr(signal, "SIGKILL"):
            os.kill(first.proc.pid, signal.SIGKILL)
        else:
            first.proc.kill()  # Windows: TerminateProcess, equally unrecoverable
        try:
            first.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            first.proc.kill()

        # The premise. If the whole chain finished before the kill landed, the
        # resumption below is never exercised and a pass would be vacuous.
        killed_state = _get_agent_row(agent_id)
        assert killed_state is not None, "agent row vanished"
        assert killed_state["status"] != "ready", (
            "the chain completed before the kill landed, so nothing was "
            "interrupted and the resumption below proves nothing. This is a "
            "failed premise, not a passing test — rerun, or move the kill "
            "earlier if the tenant migration chain has become fast enough that "
            "the window has closed."
        )

        # acks_late, observed at the broker rather than read off the decorator.
        assert _unacked_messages_mentioning(agent_id), (
            "no unacknowledged broker message names this agent after kill -9. "
            "With acks_late=False the ack lands at delivery, so the entry would "
            "already be gone — which is exactly the configuration this test "
            "exists to refuse."
        )

        # A second worker, sharing the broker, the stub journal and the tenant
        # database. wait_until_installed compares the pid, so the dead worker's
        # own 'installed' record cannot satisfy this one's readiness wait.
        second = neon_stub_worker_factory()

        # Stand in for the broker's own redelivery — see the docstring.
        provision_neon.apply_async(
            args=(str(tenant_id), str(agent_id)), queue="pipeline"
        )

        status = _poll_for_agent_status(agent_id, {"ready", "failed"}, timeout=300.0)
        assert status == "ready", (
            f"agent.status is {status!r} after the restart — the chain did not "
            "resume and complete following kill -9"
        )

        agent_row = _get_agent_row(agent_id)
        assert agent_row is not None, "Agent row not found in DB"
        assert agent_row["neon_project_id"] is not None, (
            "agent.neon_project_id should be set after provision_neon"
        )
        assert agent_row["schema_version"] is not None, (
            "agent.schema_version should be set after apply_migrations"
        )

        # The idempotency claim, asserted at the boundary. A guard that failed
        # would show up here as a second create — a *different* project id, not
        # a silent repeat, because the stub mints a fresh id per call.
        creates = _project_creates(second.calls_since(mark))
        assert len(creates) == 1, (
            f"Neon was asked to create {len(creates)} projects across the kill "
            f"and restart; the idempotency guard must make it exactly one: "
            f"{[c['project_id'] for c in creates]}"
        )
        assert agent_row["neon_project_id"] == creates[0]["project_id"], (
            f"stored project id {agent_row['neon_project_id']} is not the one "
            f"the single create returned ({creates[0]['project_id']})"
        )

    finally:
        # The factory owns worker lifetimes and the tenant database; this test
        # owns only its control-DB rows.
        _teardown_test_rows(tenant_id)
