"""
Issue #63 — a pipeline task that runs out of retries says so.

Celery's ``Task.retry(exc=exc)`` re-raises ``exc`` once ``request.retries``
reaches ``max_retries`` (celery/app/task.py: ``if max_retries is not None and
retries > max_retries: if exc: raise_with_context(exc)``).  It does NOT raise
``MaxRetriesExceededError`` when an exception was handed to it, so the shape a
pipeline task must handle is "the last attempt raised", not "MaxRetriesExceeded
arrived".  Every test here drives that shape: push a request already at
``max_retries``, make one seam raise, and read what reached the job row and the
event stream.

The observed defect, per module, was the same two absences: the ``jobs`` row
stayed in whatever state it was last written in, and no ``job.failed`` reached
``job_events`` or the SSE channel, so the ingest page hung on the last progress
event for ever.

``job.failed`` is captured at the events layer rather than at each task module,
because the shared helper (app/services/job_failure.py) calls
``events.emit`` while every task module holds its own imported ``emit``.  Both
are patched onto one list so the assertion reads "a job.failed reached the event
layer", whichever side emitted it.

Patch targets are the symbols imported INTO each task module, never the
originals (e.g. app.worker.tasks.pipeline.chunk.psycopg2, not psycopg2).
"""

import base64
import os

# ---------------------------------------------------------------------------
# Environment setup — MUST run before any `from app` import (pydantic-settings)
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

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

from contextlib import contextmanager
from unittest.mock import MagicMock

import psycopg2
import pytest
from redis.exceptions import ConnectionError as RedisUnreachable
from sqlalchemy.exc import PendingRollbackError

from app.models.job import Job

# ---------------------------------------------------------------------------
# Distinctly named failures, so the error TYPE is readable in what the job row
# and the job.failed payload carry.
# ---------------------------------------------------------------------------


class TenantDatabaseGone(RuntimeError):
    """Stands in for any unexpected error inside a pipeline task's body."""


class PoolerUnreachable(psycopg2.OperationalError):
    """Stands in for the Neon pooler DNS failure parse_documents retries on."""


class DocumentBytesUnavailable(OSError):
    """Stands in for an S3 read that fails inside chunk_documents' per-document try.

    Not a RuntimeError, so it reaches the per-document ``except Exception``
    rather than the ``except RuntimeError`` skip-this-document branch above it.
    """


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


def _sync_db_context(mock_db):
    """A get_sync_db() stand-in yielding one session, re-enterable."""

    @contextmanager
    def _ctx():
        yield mock_db

    return _ctx


def _fake_agent():
    agent = MagicMock()
    agent.name = "acme support"
    agent.status = "provisioning"
    agent.neon_project_id = None
    agent.neon_connection_string = b"encrypted-pooled"
    agent.neon_direct_connection_string = b"encrypted-direct"
    agent.retrieval_strategy = {}
    agent.strategy_resynthesis_flagged = False
    return agent


def _fake_job_row():
    job_row = MagicMock(spec=Job)
    job_row.id = "j"
    job_row.status = "running"
    job_row.error = None
    job_row.finished_at = None
    return job_row


def _fake_db(agent, job_row):
    """A Session answering both db.get(Model, id) and db.query(Job)...first()."""
    db = MagicMock()

    def _get(model, _id):
        if model.__name__ == "Job":
            return job_row
        if model.__name__ == "Agent":
            return agent
        return MagicMock()

    db.get.side_effect = _get
    db.query.return_value.filter.return_value.first.return_value = job_row
    return db


def _capture_events(monkeypatch, module_path):
    """Capture every emit, from the task module and from the events layer.

    Returns the list the (event_type, payload) pairs land in.
    """
    captured: list[tuple[str, dict]] = []

    def _emit(job_id, event_type, payload, db, redis):
        captured.append((event_type, payload or {}))

    monkeypatch.setattr(module_path + ".emit", _emit)
    import app.services.events as events_module

    monkeypatch.setattr(events_module, "emit", _emit)
    return captured


def _at_last_attempt(task):
    """Put the task on the attempt after which Celery re-raises rather than retries."""
    task.push_request(retries=task.max_retries)


def _failed_payloads(captured):
    return [payload for event_type, payload in captured if event_type == "job.failed"]


def _assert_job_failed(captured, job_row, error_type_name):
    """The two absences #63 named: the terminal event, and the job row."""
    failed = _failed_payloads(captured)
    assert len(failed) == 1, (
        "retry exhaustion emitted no job.failed. The SSE stream and the admin "
        "ingest page treat job.failed as terminal, so the job hangs on its last "
        f"progress event for ever. Events seen: {[e for e, _ in captured]}"
    )
    assert error_type_name in str(failed[0].get("error")), (
        f"job.failed does not name the error type: {failed[0]!r}"
    )
    assert job_row.status == "failed", (
        f"the jobs row was left at {job_row.status!r} after the task died"
    )
    assert error_type_name in str(job_row.error), (
        f"jobs.error does not name the error type: {job_row.error!r}"
    )
    assert job_row.finished_at is not None, "the jobs row has no finished_at"


# ---------------------------------------------------------------------------
# parse_documents
# ---------------------------------------------------------------------------


def test_parse_documents_retry_exhaustion_fails_the_job(monkeypatch):
    """The Neon pooler never resolves, the last attempt raises, the job says so."""
    from app.worker.tasks.pipeline import parse as module

    agent, job_row = _fake_agent(), _fake_job_row()
    db = _fake_db(agent, job_row)
    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(module, "fernet_decrypt", lambda _: "postgresql://tenant")
    monkeypatch.setattr(
        module.psycopg2, "connect", MagicMock(side_effect=PoolerUnreachable())
    )
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.parse")

    _at_last_attempt(module.parse_documents)
    try:
        with pytest.raises(PoolerUnreachable):
            module.parse_documents.run(
                tenant_id="t", agent_id="a", job_id="j", document_ids=["d1"]
            )
    finally:
        module.parse_documents.pop_request()

    _assert_job_failed(captured, job_row, "PoolerUnreachable")


# ---------------------------------------------------------------------------
# chunk_documents
# ---------------------------------------------------------------------------


def test_chunk_documents_retry_exhaustion_fails_the_job(monkeypatch):
    """chunk.py's rollback-and-log path (#63) now writes the row and the event."""
    from app.worker.tasks.pipeline import chunk as module

    agent, job_row = _fake_agent(), _fake_job_row()
    db = _fake_db(agent, job_row)
    tenant_conn = MagicMock()
    tenant_conn.cursor.side_effect = TenantDatabaseGone("connection reset by peer")

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(module, "fernet_decrypt", lambda _: "postgresql://tenant")
    monkeypatch.setattr(module.psycopg2, "connect", lambda _: tenant_conn)
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.chunk")

    _at_last_attempt(module.chunk_documents)
    try:
        with pytest.raises(TenantDatabaseGone):
            module.chunk_documents.run(
                {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
            )
    finally:
        module.chunk_documents.pop_request()

    _assert_job_failed(captured, job_row, "TenantDatabaseGone")


def test_chunk_documents_inner_failure_emits_one_job_failed(monkeypatch):
    """The per-document handler and the outer handler must not both end the job.

    chunk_documents nests two ``except Exception`` blocks: the per-document one
    (the Docling re-parse) calls ``retry_or_fail_the_job`` and re-raises, and the
    outer one catches that same exception and calls it again. On the last attempt
    both reached ``fail_the_job``, so the event stream carried
    ``['chunking.started', 'job.failed', 'job.failed']`` and the control session
    committed the terminal write twice. A client reading the SSE stream sees the
    job end twice, and the second write re-stamps ``finished_at``.
    """
    from app.worker.tasks.pipeline import chunk as module

    agent, job_row = _fake_agent(), _fake_job_row()
    db = _fake_db(agent, job_row)
    cursor = MagicMock()
    cursor.fetchone.return_value = ("s3://bucket/doc.pdf", "pdf", "parsed", 0)
    tenant_conn = MagicMock()
    tenant_conn.cursor.return_value.__enter__.return_value = cursor

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(module, "fernet_decrypt", lambda _: "postgresql://tenant")
    monkeypatch.setattr(module.psycopg2, "connect", lambda _: tenant_conn)
    monkeypatch.setattr(
        module.storage_service,
        "get_bytes",
        MagicMock(side_effect=DocumentBytesUnavailable("s3 read failed")),
    )
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.chunk")

    _at_last_attempt(module.chunk_documents)
    try:
        with pytest.raises(DocumentBytesUnavailable):
            module.chunk_documents.run(
                {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
            )
    finally:
        module.chunk_documents.pop_request()

    assert len(_failed_payloads(captured)) == 1, (
        "the per-document handler and the outer handler both failed the job, so "
        f"the stream ends twice. Events seen: {[e for e, _ in captured]}"
    )
    _assert_job_failed(captured, job_row, "DocumentBytesUnavailable")


# ---------------------------------------------------------------------------
# generate_metadata
# ---------------------------------------------------------------------------


def test_generate_metadata_retry_exhaustion_fails_the_job(monkeypatch):
    """metadata.py already failed the job when it enriched nothing; now it does
    the same when the last attempt raises instead of finishing."""
    from app.worker.tasks.pipeline import metadata as module

    agent, job_row = _fake_agent(), _fake_job_row()
    db = _fake_db(agent, job_row)

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(module, "fernet_decrypt", lambda _: "postgresql://tenant")
    monkeypatch.setattr(module.psycopg2, "connect", lambda _: MagicMock())
    monkeypatch.setattr(module, "ledger_recorder", lambda _dsn: MagicMock())
    monkeypatch.setattr(
        module,
        "_enrich_document",
        MagicMock(side_effect=TenantDatabaseGone("statement timeout")),
    )
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.metadata")

    _at_last_attempt(module.generate_metadata)
    try:
        with pytest.raises(TenantDatabaseGone):
            module.generate_metadata.run(
                {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
            )
    finally:
        module.generate_metadata.pop_request()

    _assert_job_failed(captured, job_row, "TenantDatabaseGone")


# ---------------------------------------------------------------------------
# embed_and_migrate — the module that already did this, kept pinned
# ---------------------------------------------------------------------------


def test_embed_and_migrate_retry_exhaustion_fails_the_job(monkeypatch):
    """embed.py was the one module that already wrote both. It still does, and
    the payload now names the error type the other modules gained."""
    from app.worker.tasks.pipeline import embed as module

    agent, job_row = _fake_agent(), _fake_job_row()
    db = _fake_db(agent, job_row)
    tenant_conn = MagicMock()
    tenant_conn.cursor.side_effect = TenantDatabaseGone("no such relation: chunks")

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(module, "fernet_decrypt", lambda _: "postgresql://tenant")
    monkeypatch.setattr(module.psycopg2, "connect", lambda _: tenant_conn)
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.embed")

    _at_last_attempt(module.embed_and_migrate)
    try:
        with pytest.raises(TenantDatabaseGone):
            module.embed_and_migrate.run(
                {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
            )
    finally:
        module.embed_and_migrate.pop_request()

    _assert_job_failed(captured, job_row, "TenantDatabaseGone")


# ---------------------------------------------------------------------------
# provision_neon
# ---------------------------------------------------------------------------


def test_provision_neon_retry_exhaustion_fails_the_job(monkeypatch):
    """provision.py caught MaxRetriesExceededError, which Celery never raises
    when retry() was handed an exception, so _mark_failed never ran."""
    from app.services.neon import NeonHTTPError
    from app.worker.tasks.pipeline import provision as module

    agent, job_row = _fake_agent(), _fake_job_row()
    db = _fake_db(agent, job_row)
    tenant = MagicMock()
    tenant.clerk_user_id = "user_abcdefgh"

    def _get(model, _id):
        if model.__name__ == "Job":
            return job_row
        if model.__name__ == "Agent":
            return agent
        return tenant

    db.get.side_effect = _get

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(
        module,
        "create_neon_project",
        MagicMock(side_effect=NeonHTTPError(503, "neon is down")),
    )
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.provision")

    _at_last_attempt(module.provision_neon)
    try:
        with pytest.raises(NeonHTTPError):
            module.provision_neon.run(tenant_id="t", agent_id="a")
    finally:
        module.provision_neon.pop_request()

    _assert_job_failed(captured, job_row, "NeonHTTPError")
    assert agent.status == "failed", (
        f"provisioning left the agent at {agent.status!r}, so the admin page still "
        "shows it provisioning"
    )


# ---------------------------------------------------------------------------
# apply_migrations
# ---------------------------------------------------------------------------


def test_apply_migrations_retry_exhaustion_fails_the_job(monkeypatch):
    """The Neon compute never becomes query-ready and the probe retries run out."""
    from app.worker.tasks.pipeline import migrations as module

    agent, job_row = _fake_agent(), _fake_job_row()
    agent.status = "provisioning"
    db = _fake_db(agent, job_row)

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(module, "fernet_decrypt", lambda _: "postgresql://tenant")
    monkeypatch.setattr(
        module,
        "wait_for_neon_ready",
        MagicMock(side_effect=TenantDatabaseGone("probe exhausted")),
    )
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.migrations")

    _at_last_attempt(module.apply_migrations)
    try:
        with pytest.raises(TenantDatabaseGone):
            module.apply_migrations.run({"agent_id": "a"})
    finally:
        module.apply_migrations.pop_request()

    _assert_job_failed(captured, job_row, "TenantDatabaseGone")


def test_apply_migrations_decrypt_failure_fails_the_job(monkeypatch):
    """The half of this task that had no handler at all until 2026-09-04.

    Only the connection probe was guarded, so the fernet_decrypt above it, both
    emits, get_current_alembic_revision after a successful upgrade, and the
    commit that writes agent.status='ready' all raised straight out of the task.
    A malformed Fernet key is the one that was actually observed, in
    provision_neon, on the same deployment's environment: the tenant DB reached
    head and the job row still said 'running'.
    """
    from app.worker.tasks.pipeline import migrations as module

    agent, job_row = _fake_agent(), _fake_job_row()
    agent.status = "provisioning"
    db = _fake_db(agent, job_row)

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(
        module,
        "fernet_decrypt",
        MagicMock(side_effect=ValueError("Fernet key must be 32 url-safe base64-encoded bytes")),
    )
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.migrations")

    _at_last_attempt(module.apply_migrations)
    try:
        with pytest.raises(ValueError, match="Fernet key"):
            module.apply_migrations.run({"agent_id": "a"})
    finally:
        module.apply_migrations.pop_request()

    _assert_job_failed(captured, job_row, "ValueError")


# ---------------------------------------------------------------------------
# synthesize_retrieval_strategy
# ---------------------------------------------------------------------------


def test_synthesize_retrieval_strategy_retry_exhaustion_fails_the_job(monkeypatch):
    """The chain's last hop. Its bare `raise` left the job on embed's
    job.complete with nothing saying the run actually died."""
    from app.worker.tasks.pipeline import strategy as module

    agent, job_row = _fake_agent(), _fake_job_row()
    db = _fake_db(agent, job_row)

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(module, "fernet_decrypt", lambda _: "postgresql://tenant")
    monkeypatch.setattr(
        module,
        "_fetch_corpus_signals_sync",
        MagicMock(side_effect=TenantDatabaseGone("corpus signals query failed")),
    )
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.strategy")

    _at_last_attempt(module.synthesize_retrieval_strategy)
    try:
        with pytest.raises(TenantDatabaseGone):
            module.synthesize_retrieval_strategy.run(
                {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
            )
    finally:
        module.synthesize_retrieval_strategy.pop_request()

    _assert_job_failed(captured, job_row, "TenantDatabaseGone")


# ---------------------------------------------------------------------------
# The other half of the rule: an attempt that still has retries left retries.
# ---------------------------------------------------------------------------


def test_an_attempt_with_retries_left_does_not_fail_the_job(monkeypatch):
    """Failing the job on the FIRST error would end a run a retry would have saved.

    chunk_documents stands for all of them: same helper, same branch.
    """
    from app.worker.tasks.pipeline import chunk as module

    agent, job_row = _fake_agent(), _fake_job_row()
    db = _fake_db(agent, job_row)
    tenant_conn = MagicMock()
    tenant_conn.cursor.side_effect = TenantDatabaseGone("connection reset by peer")

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(module, "fernet_decrypt", lambda _: "postgresql://tenant")
    monkeypatch.setattr(module.psycopg2, "connect", lambda _: tenant_conn)
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.chunk")

    module.chunk_documents.push_request(retries=0)
    try:
        with pytest.raises(Exception):
            module.chunk_documents.run(
                {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
            )
    finally:
        module.chunk_documents.pop_request()

    assert _failed_payloads(captured) == [], (
        "the first failed attempt failed the job outright, so a transient error "
        "ends the run instead of being retried"
    )
    assert job_row.status == "running", (
        f"the jobs row was written to {job_row.status!r} on a retryable attempt"
    )


# ---------------------------------------------------------------------------
# The failure path is a record, not a verdict: writing it cannot become the
# error the worker reports.
# ---------------------------------------------------------------------------


def test_a_poisoned_control_session_does_not_replace_the_original_error(monkeypatch):
    """A caller whose handler owns no rollback still ends with its own exception.

    parse_documents' psycopg2.OperationalError handler is that caller: it holds
    a control session whose last statement raised, so SQLAlchemy answers the
    terminal commit with PendingRollbackError. Unguarded, that bookkeeping error
    left the task in place of the pooler failure, and the worker recorded a run
    that died of the wrong thing.
    """
    from app.worker.tasks.pipeline import parse as module

    agent, job_row = _fake_agent(), _fake_job_row()
    db = _fake_db(agent, job_row)
    db.commit.side_effect = PendingRollbackError(
        "Can't reconnect until invalid transaction is rolled back"
    )

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(module, "fernet_decrypt", lambda _: "postgresql://tenant")
    monkeypatch.setattr(
        module.psycopg2, "connect", MagicMock(side_effect=PoolerUnreachable())
    )
    captured = _capture_events(monkeypatch, "app.worker.tasks.pipeline.parse")

    _at_last_attempt(module.parse_documents)
    try:
        with pytest.raises(PoolerUnreachable):
            module.parse_documents.run(
                tenant_id="t", agent_id="a", job_id="j", document_ids=["d1"]
            )
    finally:
        module.parse_documents.pop_request()

    assert db.rollback.called, (
        "the terminal write never rolled the control session back, so every "
        "caller that has no rollback of its own commits into a dead transaction"
    )
    assert len(_failed_payloads(captured)) == 1, (
        "the job.failed was lost with the row write. The row and the event are "
        f"written independently. Events seen: {[e for e, _ in captured]}"
    )


def test_an_unreachable_redis_does_not_replace_the_original_error(monkeypatch):
    """events.emit publishes to Redis first, so a dead broker raises inside it.

    Unguarded that ConnectionError travelled out of fail_the_job and became what
    the worker recorded, hiding the Neon failure that actually stopped the run.
    """
    from app.worker.tasks.pipeline import parse as module

    agent, job_row = _fake_agent(), _fake_job_row()
    db = _fake_db(agent, job_row)

    monkeypatch.setattr(module, "get_sync_db", _sync_db_context(db))
    monkeypatch.setattr(module, "fernet_decrypt", lambda _: "postgresql://tenant")
    monkeypatch.setattr(
        module.psycopg2, "connect", MagicMock(side_effect=PoolerUnreachable())
    )

    import app.services.events as events_module

    monkeypatch.setattr(
        events_module,
        "emit",
        MagicMock(side_effect=RedisUnreachable("Error 111 connecting to redis")),
    )
    monkeypatch.setattr(module, "emit", MagicMock())

    _at_last_attempt(module.parse_documents)
    try:
        with pytest.raises(PoolerUnreachable):
            module.parse_documents.run(
                tenant_id="t", agent_id="a", job_id="j", document_ids=["d1"]
            )
    finally:
        module.parse_documents.pop_request()

    assert job_row.status == "failed", (
        "the lost publish took the row write with it; the durable record is "
        f"the half that survives a dead broker, and it reads {job_row.status!r}"
    )
