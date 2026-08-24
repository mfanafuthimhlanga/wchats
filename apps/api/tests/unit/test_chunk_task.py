"""
Unit tests for chunk_documents Celery task — ING-03, ING-04, ING-05.

Tests:
  1. test_chunk_documents_acks_late                      — acks_late=True, max_retries=3
  2. test_chunk_documents_signature_takes_only_result_dict — (self, result) only; no conn param
  3. test_chunk_documents_upserts_with_on_conflict       — INSERT INTO chunks ... ON CONFLICT + UPDATE chunk_count
  4. test_chunk_documents_emits_event_sequence           — chunking.started then chunking.complete
  5. test_chunk_documents_returns_chain_dict_unmodified  — output matches input result dict
  6. test_chunk_documents_persists_is_table              — is_table reaches the INSERT parameters

Patch targets are symbols imported into app.worker.tasks.pipeline.chunk, NOT the
original module paths (e.g. patch app.worker.tasks.pipeline.chunk.fernet_decrypt,
not app.core.security.fernet_decrypt).

The stubs that stand in for chunk_document return real `Chunk` objects (ticket
#42), not the dicts the service emitted until 2026-08-24. A stub dict would keep
passing while the task read `chunk["text"]` off a frozen dataclass and raised in
the worker, which is the whole reason the seam is typed.
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

from app.domain.chunk import Chunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sync_db_context(mock_db):
    """Return a context manager factory that yields mock_db."""
    @contextmanager
    def _ctx():
        yield mock_db
    return _ctx


def _make_mock_agent():
    """Return a mock Agent with a usable neon_connection_string."""
    agent = MagicMock()
    agent.neon_connection_string = b"encrypted-conn"
    return agent


def _make_mock_tenant_conn(sql_records: list | None = None):
    """Return a mock psycopg2 connection whose cursor records execute() calls.

    Args:
        sql_records: list to which (sql, params) tuples from cur.execute() are appended.
    """
    if sql_records is None:
        sql_records = []

    mock_cursor = MagicMock()

    # Simulate the SELECT on documents returning a parsed document row
    # source_uri, source_type, parse_status, chunk_count
    mock_cursor.fetchone.return_value = ("x.pdf", "pdf", "parsed", None)

    # Record execute() calls for assertion
    def _record_execute(sql, params=None):
        sql_records.append((str(sql), params))

    mock_cursor.execute.side_effect = _record_execute

    # Support 'with tenant_conn.cursor() as cur:' (context manager)
    mock_cursor.__enter__ = lambda s: s
    mock_cursor.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor, sql_records


# ---------------------------------------------------------------------------
# Test 1: acks_late and max_retries
# ---------------------------------------------------------------------------


def test_chunk_documents_acks_late():
    """chunk_documents must have acks_late=True and max_retries=3."""
    from app.worker.tasks.pipeline.chunk import chunk_documents

    assert chunk_documents.acks_late is True
    assert chunk_documents.max_retries == 3


# ---------------------------------------------------------------------------
# Test 2: Signature accepts only (self, result: dict); no connection param
# ---------------------------------------------------------------------------


def test_chunk_documents_signature_takes_only_result_dict():
    """Task signature must be (result: dict) — no conn/password parameter.

    Note: Celery bind=True exposes .run as a bound method. inspect.signature()
    on .run does NOT include 'self' in the returned parameters (it is already
    bound). This mirrors the parse_documents pattern (02-02 decision).
    """
    from app.worker.tasks.pipeline.chunk import chunk_documents

    sig = inspect.signature(chunk_documents.run)
    param_names = list(sig.parameters)

    # Celery bind=True: .run is a bound method — 'self' does NOT appear
    # in inspect.signature() output (it is already bound).
    assert param_names == ["self", "result"] or param_names == ["result"], (
        f"Expected ['result'] (or ['self', 'result']) but got {param_names}"
    )

    # No connection-string-like parameter names
    sig_str = str(sig).lower()
    assert "conn" not in sig_str, (
        f"Signature contains 'conn' — connection string must not be in task args: {sig}"
    )


# ---------------------------------------------------------------------------
# Test 3: UPSERT with ON CONFLICT and chunk_count UPDATE
# ---------------------------------------------------------------------------


def test_chunk_documents_upserts_with_on_conflict(monkeypatch):
    """chunk_documents calls INSERT INTO chunks ... ON CONFLICT (id) DO UPDATE
    and UPDATE documents SET chunk_count."""
    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    sql_records = []
    mock_conn, mock_cursor, sql_records = _make_mock_tenant_conn(sql_records)

    # Patch all external dependencies
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.psycopg2.connect",
        lambda _: mock_conn,
    )

    # Mock parse_document to return a sentinel doc
    mock_doc = MagicMock()
    # BACKLOG 1.26: chunk_documents reads document bytes from S3, not from
    # disk. Patching a local-path reader is exactly what let the real defect
    # survive the whole life of PROD-13, so these stubs stand at the storage
    # boundary instead.
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.storage_service.get_bytes",
        lambda key: b"%PDF-1.4 stub bytes",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.parse_document_from_bytes",
        lambda content, source_uri: mock_doc,
    )

    # Mock chunk_document to return one chunk dict
    mock_chunks = (
        Chunk(
            document_id="d1",
            ordinal=0,
            content="hello world",
            token_count=2,
            is_table=False,
        ),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.chunk_document",
        lambda doc, doc_id: mock_chunks,
    )

    # Mock emit
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.emit",
        MagicMock(),
    )

    from app.worker.tasks.pipeline.chunk import chunk_documents

    input_result = {
        "tenant_id": "t",
        "agent_id": "a",
        "job_id": "j",
        "document_ids": ["d1"],
    }

    # chunk_documents.run is a bound method (bind=True) — call without explicit self.
    # Same pattern used in test_parse_task.py (02-02 decision).
    chunk_documents.run(input_result)

    # Flatten all SQL strings for assertion
    all_sqls = [sql for sql, _ in sql_records]

    # At least one INSERT INTO chunks ... ON CONFLICT (id) DO UPDATE must exist
    upsert_sqls = [s for s in all_sqls if "INSERT INTO chunks" in s and "ON CONFLICT (id) DO UPDATE" in s]
    assert len(upsert_sqls) >= 1, (
        "Expected at least one 'INSERT INTO chunks ... ON CONFLICT (id) DO UPDATE' SQL.\n"
        "All recorded SQLs:\n" + "\n".join(all_sqls)
    )

    # At least one UPDATE documents SET chunk_count must exist
    update_sqls = [s for s in all_sqls if "UPDATE documents SET chunk_count" in s]
    assert len(update_sqls) >= 1, (
        "Expected at least one 'UPDATE documents SET chunk_count' SQL.\n"
        "All recorded SQLs:\n" + "\n".join(all_sqls)
    )


# ---------------------------------------------------------------------------
# Test 4: Event emission sequence — chunking.started then chunking.complete
# ---------------------------------------------------------------------------


def test_chunk_documents_emits_event_sequence(monkeypatch):
    """chunk_documents emits chunking.started then chunking.complete (in that order)."""
    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    mock_conn, mock_cursor, _ = _make_mock_tenant_conn()

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.psycopg2.connect",
        lambda _: mock_conn,
    )

    mock_doc = MagicMock()
    # BACKLOG 1.26: chunk_documents reads document bytes from S3, not from
    # disk. Patching a local-path reader is exactly what let the real defect
    # survive the whole life of PROD-13, so these stubs stand at the storage
    # boundary instead.
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.storage_service.get_bytes",
        lambda key: b"%PDF-1.4 stub bytes",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.parse_document_from_bytes",
        lambda content, source_uri: mock_doc,
    )

    mock_chunks = (
        Chunk(
            document_id="d1",
            ordinal=0,
            content="hello",
            token_count=1,
            is_table=False,
        ),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.chunk_document",
        lambda doc, doc_id: mock_chunks,
    )

    # Capture emit() calls
    emitted_events = []

    def capture_emit(job_id, event_type, payload, db, redis_client):
        emitted_events.append(event_type)

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.emit",
        capture_emit,
    )

    from app.worker.tasks.pipeline.chunk import chunk_documents

    # chunk_documents.run is a bound method — call without explicit self
    chunk_documents.run(
        {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]},
    )

    assert emitted_events == ["chunking.started", "chunking.complete"], (
        f"Expected event sequence ['chunking.started', 'chunking.complete'] "
        f"but got {emitted_events}"
    )


# ---------------------------------------------------------------------------
# Test 5: Returns chain dict unmodified
# ---------------------------------------------------------------------------


def test_chunk_documents_returns_chain_dict_unmodified(monkeypatch):
    """chunk_documents returns the same dict structure it received (for chain forwarding)."""
    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    mock_conn, mock_cursor, _ = _make_mock_tenant_conn()

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.psycopg2.connect",
        lambda _: mock_conn,
    )

    mock_doc = MagicMock()
    # BACKLOG 1.26: chunk_documents reads document bytes from S3, not from
    # disk. Patching a local-path reader is exactly what let the real defect
    # survive the whole life of PROD-13, so these stubs stand at the storage
    # boundary instead.
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.storage_service.get_bytes",
        lambda key: b"%PDF-1.4 stub bytes",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.parse_document_from_bytes",
        lambda content, source_uri: mock_doc,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.chunk_document",
        lambda doc, doc_id: (
            Chunk(
                document_id="d1", ordinal=0, content="t", token_count=1, is_table=False
            ),
        ),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.emit",
        MagicMock(),
    )

    from app.worker.tasks.pipeline.chunk import chunk_documents

    input_result = {
        "tenant_id": "t",
        "agent_id": "a",
        "job_id": "j",
        "document_ids": ["d1"],
    }

    # chunk_documents.run is a bound method — call without explicit self
    output = chunk_documents.run(input_result)

    assert output == input_result, (
        f"Return value must match input result dict for chain forwarding.\n"
        f"Expected: {input_result}\nGot: {output}"
    )


# ---------------------------------------------------------------------------
# Test 6: is_table reaches the INSERT parameters
# ---------------------------------------------------------------------------


def test_chunk_documents_persists_is_table(monkeypatch):
    """The table flag the chunker computed is written into the chunks row.

    It was computed and dropped until 2026-08-24: chunk_document set it on every
    chunk and the INSERT listed five columns, none of them is_table. Retrieval
    could not tell a Markdown table from prose, so the flag existed only in the
    log line counting how many there were.
    """
    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    sql_records = []
    mock_conn, mock_cursor, sql_records = _make_mock_tenant_conn(sql_records)

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.psycopg2.connect",
        lambda _: mock_conn,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.storage_service.get_bytes",
        lambda key: b"%PDF-1.4 stub bytes",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.parse_document_from_bytes",
        lambda content, source_uri: MagicMock(),
    )

    prose = Chunk(
        document_id="d1", ordinal=0, content="prose", token_count=1, is_table=False
    )
    table = Chunk(
        document_id="d1", ordinal=1, content="| a |", token_count=2, is_table=True
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.chunk.chunk_document",
        lambda doc, doc_id: (prose, table),
    )
    monkeypatch.setattr("app.worker.tasks.pipeline.chunk.emit", MagicMock())

    from app.worker.tasks.pipeline.chunk import chunk_documents

    chunk_documents.run(
        {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]},
    )

    inserts = [
        (sql, params) for sql, params in sql_records if "INSERT INTO chunks" in sql
    ]
    assert len(inserts) == 2, (
        f"Expected one INSERT per chunk, got {len(inserts)}"
    )

    for sql, _ in inserts:
        assert "is_table" in sql, (
            "the INSERT column list must name is_table: " + sql
        )

    # The flag lands per chunk, in the order the chunker issued them, and the id
    # is the one Chunk derived rather than anything the task invented.
    assert [params[-1] for _, params in inserts] == [False, True]
    assert [params[0] for _, params in inserts] == [str(prose.id), str(table.id)]
    assert [params[2] for _, params in inserts] == [0, 1]
