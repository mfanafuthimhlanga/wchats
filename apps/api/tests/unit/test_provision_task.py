"""
provision_neon ends the job row, whatever stopped it.

Observed on staging 2026-09-04 at 15:25 UTC. The task created a Neon project,
committed its id, and raised

    ValueError: Fernet key must be 32 url-safe base64-encoded bytes

out of `fernet_encrypt` on the next line, from an environment key that had lost
its base64 padding. The two Neon calls each had a failure handler; the line after
them had none. So the traceback went to the pipeline log, the `jobs` row stayed
`status='running'` with `neon.project.creating` as its last event, and the MCP
caller polling `get_job` had nothing to wait for and waited anyway.

The seam these tests break is `fernet_encrypt`, because that is the line the
failure was observed on and it sits in the region that had no handler. What is
asserted is not that fernet_encrypt is called but what the job row and the event
stream say afterwards, so the tests hold for any exception raised anywhere
between the job row and the return.

`tests/unit/test_pipeline_retry_exhaustion.py` covers the same task's Neon-call
failures (#63). This file covers the rest of the body.
"""

import base64
import os

# ---------------------------------------------------------------------------
# Environment setup. MUST run before any `from app` import (pydantic-settings)
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode()
)
os.environ.setdefault("NEON_API_KEY", "test_neon")
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb")
os.environ.setdefault("ADMIN_KEY", "test_admin")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage")

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from celery.exceptions import Retry

from app.models.job import Job

#: The message the staging environment's malformed key actually produced. Kept
#: verbatim so the payload assertion below is about a real failure's wording.
FERNET_KEY_MALFORMED = "Fernet key must be 32 url-safe base64-encoded bytes"

PROJECT_ID = "proud-sky-12345678"
#: Obvious dummies. The pre-commit secret guard reads anything shaped like a real
#: Neon host as a real credential, and it is right to.
POOLED_URI = "postgresql://<user>:<redacted>@pooled.example.invalid/neondb"
DIRECT_URI = "postgresql://<user>:<redacted>@direct.example.invalid/neondb"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _sync_db_context(db):
    """A get_sync_db() stand-in yielding one session, re-enterable."""

    @contextmanager
    def _ctx():
        yield db

    return _ctx


def _fresh_agent():
    """State C: nothing provisioned yet, which is what a new signup looks like."""
    agent = MagicMock()
    agent.name = "acme support"
    agent.status = "pending"
    agent.neon_project_id = None
    agent.neon_connection_string = None
    agent.neon_direct_connection_string = None
    return agent


def _running_job():
    job = MagicMock(spec=Job)
    job.id = "job-1"
    job.status = "pending"
    job.error = None
    job.started_at = None
    job.finished_at = None
    return job


def _db_for(agent, job, tenant):
    """A Session answering db.get(Model, id) and db.query(Job)...first().

    Every commit appends (project id, whether a connection string is stored) as
    they stood at that moment, so a test can ask whether the id reached the
    database BEFORE the line that raised rather than only whether the attribute
    was assigned. That ordering is the whole idempotency contract: a retry that
    finds the id takes state B and re-fetches the URIs instead of creating a
    second Neon project.
    """
    db = MagicMock()
    db.commits = []

    def _get(model, _id):
        if model.__name__ == "Job":
            return job
        if model.__name__ == "Agent":
            return agent
        return tenant

    def _commit():
        db.commits.append((agent.neon_project_id, bool(agent.neon_connection_string)))

    db.get.side_effect = _get
    db.query.return_value.filter.return_value.first.return_value = job
    db.commit.side_effect = _commit
    return db


def _capture_events(monkeypatch):
    """Every emit, from the task module and from the events layer, in order.

    Two patch targets because job_failure.py reaches emit through
    `app.services.events` while provision.py holds its own imported symbol.
    """
    captured: list[tuple[str, dict]] = []

    def _emit(job_id, event_type, payload, db, redis):
        captured.append((event_type, payload or {}))

    monkeypatch.setattr("app.worker.tasks.pipeline.provision.emit", _emit)
    import app.services.events as events_module

    monkeypatch.setattr(events_module, "emit", _emit)
    return captured


def _stub_the_neon_project(monkeypatch, module):
    """create_neon_project answers with a project and both of its URIs."""
    monkeypatch.setattr(
        module,
        "create_neon_project",
        MagicMock(return_value={"id": PROJECT_ID, "pooled_uri": POOLED_URI, "direct_uri": DIRECT_URI}),
    )


def _stub_apply_migrations(monkeypatch):
    """The chain's next hop, which provision_neon calls eagerly on success."""
    import app.worker.tasks.pipeline.migrations as migrations_module

    dispatched = MagicMock()
    monkeypatch.setattr(migrations_module, "apply_migrations", dispatched)
    return dispatched


def _spy_on_retry(monkeypatch, task):
    """Record what the task asks Celery to schedule, and raise what Celery raises.

    A task driven by .run() has request.called_directly set, and Celery's own
    Task.retry re-raises the original exception in that case rather than Retry.
    That would make a scheduled attempt indistinguishable from an exhausted one
    by exception type, which is the distinction under test here.
    """
    scheduled: list[dict] = []

    def _retry(*_args, **kwargs):
        scheduled.append(kwargs)
        raise Retry("scheduled")

    monkeypatch.setattr(task, "retry", _retry)
    return scheduled


def _failed_payloads(captured):
    return [payload for event_type, payload in captured if event_type == "job.failed"]


def _provisioning_agent(monkeypatch, module, tenant=None):
    """The three doubles every case here shares, wired onto the task module."""
    agent, job = _fresh_agent(), _running_job()
    db = _db_for(agent, job, tenant or MagicMock(clerk_user_id="user_abcdefgh"))
    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    _stub_the_neon_project(monkeypatch, module)
    return agent, job, db


# ---------------------------------------------------------------------------
# The observed failure: encryption raises after the project id is committed
# ---------------------------------------------------------------------------


def test_encrypt_failure_on_the_last_attempt_fails_the_job(monkeypatch):
    """The staging failure, on the attempt after which Celery stops retrying.

    Everything the caller can see must say the run ended: the row, the terminal
    event, and the error type inside it. And the project id committed one line
    earlier must survive, because that commit is what makes a retry safe.
    """
    from app.worker.tasks.pipeline import provision as module

    agent, job, db = _provisioning_agent(monkeypatch, module)
    monkeypatch.setattr(
        module, "fernet_encrypt", MagicMock(side_effect=ValueError(FERNET_KEY_MALFORMED))
    )
    captured = _capture_events(monkeypatch)

    module.provision_neon.push_request(retries=module.provision_neon.max_retries)
    try:
        with pytest.raises(ValueError, match=FERNET_KEY_MALFORMED):
            module.provision_neon.run(tenant_id="t", agent_id="a")
    finally:
        module.provision_neon.pop_request()

    failed = _failed_payloads(captured)
    assert len(failed) == 1, (
        "a Fernet failure between the Neon call and the return emitted no "
        "job.failed. SSE treats job.failed as terminal, so the ingest page and "
        "any MCP caller polling get_job wait on 'neon.project.creating' for "
        f"ever. Events seen: {[event for event, _ in captured]}"
    )
    assert "ValueError" in str(failed[0]["error"]), (
        f"job.failed does not name the error type: {failed[0]!r}"
    )
    assert FERNET_KEY_MALFORMED in str(failed[0]["error"]), (
        f"job.failed does not carry what went wrong: {failed[0]!r}"
    )
    assert job.status == "failed", f"the jobs row was left at {job.status!r}"
    assert job.finished_at is not None, "the jobs row has no finished_at"
    assert agent.status == "failed", (
        f"the agent was left at {agent.status!r}, so the console still shows it provisioning"
    )
    assert agent.neon_project_id == PROJECT_ID, (
        "the failure lost the project id, so a retry would create a second Neon project"
    )
    assert (PROJECT_ID, False) in db.commits, (
        "the project id was never committed before the encryption ran, so the "
        "idempotency save point is not a save point"
    )


def test_encrypt_failure_on_an_earlier_attempt_schedules_a_retry(monkeypatch):
    """Attempts remain, so the task asks for another one and ends nothing.

    A retry is safe here precisely because the project id is committed: the next
    attempt reads it, takes state B, and re-fetches the URIs for the project that
    already exists rather than creating another.
    """
    from app.worker.tasks.pipeline import provision as module

    agent, job, _db = _provisioning_agent(monkeypatch, module)
    monkeypatch.setattr(
        module, "fernet_encrypt", MagicMock(side_effect=ValueError(FERNET_KEY_MALFORMED))
    )
    captured = _capture_events(monkeypatch)
    scheduled = _spy_on_retry(monkeypatch, module.provision_neon)

    module.provision_neon.push_request(retries=0)
    try:
        with pytest.raises(Retry):
            module.provision_neon.run(tenant_id="t", agent_id="a")
    finally:
        module.provision_neon.pop_request()

    assert len(scheduled) == 1, "the first attempt did not ask Celery for another"
    assert scheduled[0]["countdown"] == 1, (
        f"expected the 2**retries backoff the sibling tasks use, got {scheduled[0]!r}"
    )
    assert _failed_payloads(captured) == [], (
        "a job with attempts left emitted job.failed, which SSE treats as "
        "terminal: the client stops reading before the retry that would have "
        "succeeded"
    )
    assert job.status != "failed", f"the jobs row was ended at {job.status!r} with retries left"
    assert agent.neon_project_id == PROJECT_ID, "the retry has no project id to resume from"


def test_a_neon_4xx_ends_the_job_without_asking_for_another_attempt(monkeypatch):
    """A 400 is the API's answer, not a bad moment. Retrying it three times only
    delays the same reply, so this path fails the job and leaves as its own type
    rather than falling through to the handler that schedules attempts."""
    from app.services.neon import NeonHTTPError
    from app.worker.tasks.pipeline import provision as module

    agent, job, _db = _provisioning_agent(monkeypatch, module)
    monkeypatch.setattr(
        module, "create_neon_project", MagicMock(side_effect=NeonHTTPError(400, "bad project name"))
    )
    captured = _capture_events(monkeypatch)
    scheduled = _spy_on_retry(monkeypatch, module.provision_neon)

    module.provision_neon.push_request(retries=0)
    try:
        with pytest.raises(module.ProvisioningAborted, match="aborted the chain"):
            module.provision_neon.run(tenant_id="t", agent_id="a")
    finally:
        module.provision_neon.pop_request()

    assert scheduled == [], "a 4xx bought retries against an API that already answered"
    assert len(_failed_payloads(captured)) == 1, (
        f"expected exactly one job.failed, got {[event for event, _ in captured]}"
    )
    assert job.status == "failed", f"the jobs row was left at {job.status!r}"
    assert agent.status == "failed", f"the agent was left at {agent.status!r}"


# ---------------------------------------------------------------------------
# The path that must not have changed
# ---------------------------------------------------------------------------


def test_happy_path_stores_both_uris_and_hands_the_chain_on(monkeypatch):
    """Nothing raises: both URIs are encrypted and stored, the events arrive in
    order, and apply_migrations is handed the agent and project id and nothing
    else (T-03-01: no connection string ever leaves in a task argument)."""
    from app.worker.tasks.pipeline import provision as module

    agent, job, db = _provisioning_agent(monkeypatch, module)
    monkeypatch.setattr(module, "fernet_encrypt", lambda uri: b"encrypted:" + uri.encode())
    captured = _capture_events(monkeypatch)
    dispatched = _stub_apply_migrations(monkeypatch)

    module.provision_neon.push_request(retries=0)
    try:
        result = module.provision_neon.run(tenant_id="t", agent_id="a")
    finally:
        module.provision_neon.pop_request()

    assert result == {"agent_id": "a", "project_id": PROJECT_ID}
    assert [event for event, _ in captured] == [
        "job.started",
        "neon.project.creating",
        "neon.project.ready",
    ]
    assert agent.neon_project_id == PROJECT_ID
    assert agent.neon_connection_string == b"encrypted:" + POOLED_URI.encode(), (
        "the pooled URI is what application traffic uses"
    )
    assert agent.neon_direct_connection_string == b"encrypted:" + DIRECT_URI.encode(), (
        "the direct URI is what Alembic uses; through PgBouncer, DDL fails silently"
    )
    assert job.status == "running", f"provisioning ended the job at {job.status!r}"
    dispatched.apply.assert_called_once_with(args=[{"agent_id": "a", "project_id": PROJECT_ID}])
    assert db.commits == [(None, False), (PROJECT_ID, False), (PROJECT_ID, True)], (
        "three commits in this order are the idempotency contract: the job row "
        "moves to running, then the project id lands ALONE, then the connection "
        "strings. Collapsing the middle one into the last is what leaves a "
        "kill-9'd worker creating a second Neon project on its next attempt"
    )


def test_state_b_refetches_the_uris_without_creating_a_second_project(monkeypatch):
    """The retry the two failure cases above set up, arriving.

    An agent carrying a project id and no connection string is a provision that
    died between the two. It must re-fetch that project's URIs, never call
    create_neon_project again.
    """
    from app.worker.tasks.pipeline import provision as module

    agent, job, _db = _provisioning_agent(monkeypatch, module)
    agent.neon_project_id = PROJECT_ID
    monkeypatch.setattr(module, "fernet_encrypt", lambda uri: b"encrypted:" + uri.encode())
    monkeypatch.setattr(
        module,
        "_connection_uris",
        MagicMock(return_value={"id": PROJECT_ID, "pooled_uri": POOLED_URI, "direct_uri": DIRECT_URI}),
    )
    _capture_events(monkeypatch)
    _stub_apply_migrations(monkeypatch)

    module.provision_neon.push_request(retries=0)
    try:
        result = module.provision_neon.run(tenant_id="t", agent_id="a")
    finally:
        module.provision_neon.pop_request()

    assert result == {"agent_id": "a", "project_id": PROJECT_ID}
    module.create_neon_project.assert_not_called()
    module._connection_uris.assert_called_once_with(PROJECT_ID)
    assert agent.neon_connection_string == b"encrypted:" + POOLED_URI.encode()
