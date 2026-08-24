"""
Unit tests for parse_documents Celery task — ING-02.

Tests:
  1. test_parse_documents_acks_late       — acks_late=True, max_retries=3, default_retry_delay=5
  2. test_parse_documents_no_conn_string_in_signature — signature has no conn/password params
  3. test_parse_documents_idempotency_skips_parsed_doc — Layer 1 guard fires, parse_document NOT called
  4. test_parse_documents_hands_on_the_job_it_built: IngestionJob in, its wire form out
  5. test_parse_documents_refuses_an_empty_id_at_the_source: the head validates once,
     in the type
  6. test_parse_documents_emits_event_sequence: ingestion.started, then parsing.started,
     then parsing.complete

parse_documents is the chain's HEAD (ticket #43): it takes the four ids as task
arguments from the upload route, builds the IngestionJob, and returns that job's
wire form. The three hops after it take the wire form and give it back.

Patch targets are symbols imported into app.worker.tasks.pipeline.parse, NOT
the original module paths (e.g. patch app.worker.tasks.pipeline.parse.fernet_decrypt,
not app.core.security.fernet_decrypt).
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
os.environ.setdefault(
    "CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb"
)
os.environ.setdefault(
    "CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb"
)
os.environ.setdefault("ADMIN_KEY", "test_admin")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage")

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

import inspect
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.domain.ingestion_job import IngestionJob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sync_db_context(mock_db):
    """Return a context manager factory that yields mock_db."""
    @contextmanager
    def _ctx():
        yield mock_db
    return _ctx


def _make_mock_agent(agent_id="a1"):
    """Return a MagicMock Agent with a usable neon_connection_string."""
    agent = MagicMock()
    agent.id = agent_id
    agent.neon_connection_string = b"encrypted-conn"
    return agent


def _make_mock_cursor_for_parsed_doc(doc_id="d1"):
    """Return a mock psycopg2 cursor where the document is already parsed."""
    cursor = MagicMock()

    # First call: unparsed_count pre-check → 0 (all parsed)
    # Second call per doc: SELECT source_uri, source_type, source_hash, parse_status
    cursor.fetchone.side_effect = [
        # Pre-check returns 0 un-parsed docs — triggers early return
        (0,),
    ]
    return cursor


def _make_mock_cursor_for_pending_doc(doc_id="d1", source_type="pdf"):
    """Return a mock cursor where the document is pending (parse_status='pending')."""
    cursor = MagicMock()

    # Pre-check: 1 unparsed document
    # Document row: source_uri, source_type, source_hash, parse_status
    cursor.fetchone.side_effect = [
        (1,),  # pre-check: 1 unparsed
        (f"file://{doc_id}.pdf", source_type, None, "pending"),  # SELECT row
    ]
    return cursor


# ---------------------------------------------------------------------------
# Test 1: acks_late, max_retries, default_retry_delay
# ---------------------------------------------------------------------------


def test_parse_documents_acks_late():
    """parse_documents must have acks_late=True, max_retries=3, default_retry_delay=5."""
    from app.worker.tasks.pipeline.parse import parse_documents

    assert parse_documents.acks_late is True
    assert parse_documents.max_retries == 3
    assert parse_documents.default_retry_delay == 5


# ---------------------------------------------------------------------------
# Test 2: No connection string in task signature
# ---------------------------------------------------------------------------


def test_parse_documents_no_conn_string_in_signature():
    """Task signature must not contain any connection-string-like parameter.

    Uses parse_documents.run to access the underlying function (Celery wraps it).
    Mirrors the existing test_task_args.py pattern.
    """
    from app.worker.tasks.pipeline.parse import parse_documents

    sig = inspect.signature(parse_documents.run)
    param_names = list(sig.parameters)

    # Exact expected params (Celery bind=True: .run does not include 'self' in signature)
    assert param_names == ["tenant_id", "agent_id", "job_id", "document_ids"], (
        f"Unexpected parameters: {param_names}"
    )

    # No connection-string-like parameter names
    for p in sig.parameters.values():
        assert "conn" not in p.name.lower(), (
            f"Parameter '{p.name}' looks like a connection string (contains 'conn')"
        )
        assert "password" not in p.name.lower(), (
            f"Parameter '{p.name}' looks like it contains a password"
        )


# ---------------------------------------------------------------------------
# Test 3: Idempotency guard — skip already-parsed document
# ---------------------------------------------------------------------------


def test_parse_documents_idempotency_skips_parsed_doc(monkeypatch):
    """parse_documents skips parsing when all documents are already parsed (Layer 1 guard).

    If unparsed_count == 0 (pre-check), the task must return early without
    calling parse_document.
    """
    mock_db = MagicMock()
    mock_agent = _make_mock_agent("a1")
    mock_db.get.return_value = mock_agent

    mock_cursor = _make_mock_cursor_for_parsed_doc("d1")
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    # Patch all external dependencies
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.fernet_decrypt",
        lambda _: "fake-conn-str",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.psycopg2.connect",
        lambda conn_str: mock_conn,
    )

    mock_parse = MagicMock()
    monkeypatch.setattr("app.worker.tasks.pipeline.parse.parse_document", mock_parse)

    mock_emit = MagicMock()
    monkeypatch.setattr("app.worker.tasks.pipeline.parse.emit", mock_emit)

    from app.worker.tasks.pipeline.parse import parse_documents

    # parse_documents.run is a bound method (bind=True) — call without self.
    # The early-return path (all docs parsed) never reaches self.request.retries,
    # so no retry-context patching is needed here.
    result = parse_documents.run("t1", "a1", "j1", ["d1"])

    # parse_document must NOT have been called (idempotency guard)
    mock_parse.assert_not_called()

    # The early exit hands on the same job as the full path, read as the type
    # rather than as four key spellings.
    assert IngestionJob.from_dict(result) == IngestionJob("t1", "a1", "j1", ["d1"])


# ---------------------------------------------------------------------------
# Test 4: Returns chain dict for pending document
# ---------------------------------------------------------------------------


def test_parse_documents_hands_on_the_job_it_built(monkeypatch):
    """The head builds an IngestionJob from its four arguments and sends its wire form.

    Two assertions, and they say different things. The first reads the return
    value as the type, so the chain contract is what the next hop can construct.
    The second pins the wire form itself, because Celery serialises JSON and the
    dict on the broker is the one the chain has always sent.

    P13-06: file-source branch now reads bytes from S3 via storage_service.get_bytes
    and parses via parse_document_from_bytes — no local-disk read.
    """
    mock_db = MagicMock()
    mock_agent = _make_mock_agent("a1")
    mock_db.get.return_value = mock_agent

    doc_id = "d1"
    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [
        (1,),  # pre-check: 1 unparsed
        (f"{doc_id}.pdf", "pdf", None, "pending"),  # document row
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.fernet_decrypt",
        lambda _: "fake-conn-str",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.psycopg2.connect",
        lambda conn_str: mock_conn,
    )

    # P13-06: mock S3 get_bytes to return fake PDF bytes (no local disk read)
    monkeypatch.setattr(
        "app.services.storage_service.get_bytes",
        lambda key: b"%PDF-1.4 fake content from S3",
    )

    # Mock parse_document_from_bytes (file-source now uses bytes path, not file path)
    mock_doc = MagicMock()
    mock_doc.pages = {1: MagicMock()}  # 1 page
    mock_parse = MagicMock(return_value=mock_doc)
    monkeypatch.setattr("app.worker.tasks.pipeline.parse.parse_document_from_bytes", mock_parse)

    mock_emit = MagicMock()
    monkeypatch.setattr("app.worker.tasks.pipeline.parse.emit", mock_emit)

    from app.worker.tasks.pipeline.parse import parse_documents

    # parse_documents.run is a bound method — call without self.
    # The happy-path (pending → parsed) does not exercise retry logic.
    result = parse_documents.run("t1", "a1", "j1", [doc_id])

    job = IngestionJob("t1", "a1", "j1", [doc_id])
    assert IngestionJob.from_dict(result) == job
    assert result == job.to_dict()


# ---------------------------------------------------------------------------
# Test 5: The head refuses a job it cannot name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("position", [0, 1, 2])
def test_parse_documents_refuses_an_empty_id_at_the_source(monkeypatch, position):
    """An empty tenant_id, agent_id or job_id stops the chain at its head.

    The three hops downstream have always refused such a dict and returned it
    unchanged. The head never checked, so an empty job_id reached emit() and
    published every event of the run to a channel nobody is subscribed to. The
    ids are the type's to validate now, and the head is where they enter.
    """
    mock_db = MagicMock()
    mock_db.get.return_value = _make_mock_agent("a1")

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = _make_mock_cursor_for_parsed_doc("d1")

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.fernet_decrypt", lambda _: "fake-conn-str"
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.psycopg2.connect", lambda conn_str: mock_conn
    )
    monkeypatch.setattr("app.worker.tasks.pipeline.parse.emit", MagicMock())

    from app.worker.tasks.pipeline.parse import parse_documents

    args = ["t1", "a1", "j1"]
    args[position] = ""

    with pytest.raises(ValueError):
        parse_documents.run(*args, ["d1"])


# ---------------------------------------------------------------------------
# Test 6: Correct event emission order
# ---------------------------------------------------------------------------


def test_parse_documents_emits_event_sequence(monkeypatch):
    """parse_documents emits ingestion.started → parsing.started → parsing.complete in order.

    Each emit call's first positional arg must be job_id ("j1"), not agent_id.

    P13-06: file-source branch now reads bytes from S3 via storage_service.get_bytes
    and parses via parse_document_from_bytes — no local-disk read.
    """
    mock_db = MagicMock()
    mock_agent = _make_mock_agent("a1")
    mock_db.get.return_value = mock_agent

    doc_id = "d1"
    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [
        (1,),  # pre-check: 1 unparsed
        (f"{doc_id}.pdf", "pdf", None, "pending"),  # document row
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.fernet_decrypt",
        lambda _: "fake-conn-str",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.psycopg2.connect",
        lambda conn_str: mock_conn,
    )

    # P13-06: mock S3 get_bytes (file-source reads from S3, not local disk)
    monkeypatch.setattr(
        "app.services.storage_service.get_bytes",
        lambda key: b"%PDF-1.4 fake content from S3",
    )

    mock_doc = MagicMock()
    mock_doc.pages = {1: MagicMock()}
    mock_parse = MagicMock(return_value=mock_doc)
    monkeypatch.setattr("app.worker.tasks.pipeline.parse.parse_document_from_bytes", mock_parse)

    # Capture all emit() calls
    emitted_events = []

    def capture_emit(job_id, event_type, payload, db, redis_client):
        emitted_events.append((job_id, event_type))

    monkeypatch.setattr("app.worker.tasks.pipeline.parse.emit", capture_emit)

    from app.worker.tasks.pipeline.parse import parse_documents

    # parse_documents.run is a bound method — call without self
    parse_documents.run("t1", "a1", "j1", [doc_id])

    # Verify event order
    event_types = [e[1] for e in emitted_events]
    assert event_types == ["ingestion.started", "parsing.started", "parsing.complete"], (
        f"Expected event sequence [ingestion.started, parsing.started, parsing.complete] "
        f"but got {event_types}"
    )

    # Verify all events are bound to job_id ("j1"), not agent_id
    for job_id_arg, event_type in emitted_events:
        assert job_id_arg == "j1", (
            f"Event '{event_type}' emitted with job_id={job_id_arg!r}, expected 'j1'"
        )
