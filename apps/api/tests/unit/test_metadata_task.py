"""
Unit tests for generate_metadata Celery task — ING-06.

Tests:
  1. test_generate_metadata_acks_late                                    — acks_late=True, max_retries=3
  2. test_generate_metadata_signature                                     — (self, result) only; no conn/api_key param
  3. test_layer_3_idempotency_skips_haiku_when_metadata_exists           — Layer 3 fires; enrich_chunk NOT called
  4. test_generate_metadata_calls_enrich_when_no_existing_metadata        — enrich_chunk called once with "content"
  5. test_generate_metadata_upserts_entities_with_on_conflict_normalized_type — entity UPSERT SQL shape
  6. test_generate_metadata_emits_event_sequence                          — metadata.started then metadata.complete

Patch targets are symbols imported into app.worker.tasks.pipeline.metadata, NOT
the original module paths (e.g. patch app.worker.tasks.pipeline.metadata.fernet_decrypt,
not app.core.security.fernet_decrypt).
"""

import os
import base64

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
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("MAX_UPLOAD_SIZE_MB", "50")

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

import inspect
import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, call


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


class _MockCursor:
    """Mock psycopg2 cursor that records execute() SQL calls.

    Returns configurable fetchone() results in sequence. Supports
    'with cursor:' context manager protocol.
    """

    def __init__(self, fetchone_sequence=None):
        """
        Args:
            fetchone_sequence: list of values returned by consecutive fetchone() calls.
                               When exhausted, returns None.
        """
        self.executed_sqls = []  # (sql_str, params) tuples
        self._fetchone_seq = list(fetchone_sequence or [])
        self._fetchone_idx = 0
        self.fetchall_result = []  # override for fetchall()

    def execute(self, sql, params=None):
        self.executed_sqls.append((str(sql), params))

    def fetchone(self):
        if self._fetchone_idx < len(self._fetchone_seq):
            val = self._fetchone_seq[self._fetchone_idx]
            self._fetchone_idx += 1
            return val
        return None

    def fetchall(self):
        return self.fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _make_mock_tenant_conn(mock_cursor):
    """Return a mock psycopg2 connection that returns mock_cursor from .cursor()."""
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    # cursor() as context manager (with tenant_conn.cursor() as cur:)
    mock_conn.cursor.return_value.__enter__ = lambda s: s
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn


# ---------------------------------------------------------------------------
# Test 1: acks_late and max_retries
# ---------------------------------------------------------------------------


def test_generate_metadata_acks_late():
    """generate_metadata must have acks_late=True and max_retries=3."""
    from app.worker.tasks.pipeline.metadata import generate_metadata

    assert generate_metadata.acks_late is True
    assert generate_metadata.max_retries == 3


# ---------------------------------------------------------------------------
# Test 2: Signature accepts only (self, result: dict); no connection/api_key param
# ---------------------------------------------------------------------------


def test_generate_metadata_signature():
    """Task signature must be (result: dict) — no conn or api_key parameter.

    Note: Celery bind=True exposes .run as a bound method. inspect.signature()
    on .run does NOT include 'self' in the returned parameters (it is already
    bound). This mirrors the chunk_documents pattern (02-03 decision).
    """
    from app.worker.tasks.pipeline.metadata import generate_metadata

    sig = inspect.signature(generate_metadata.run)
    param_names = list(sig.parameters)

    # Celery bind=True: .run may or may not expose 'self' depending on Celery version
    assert param_names == ["self", "result"] or param_names == ["result"], (
        f"Expected ['result'] (or ['self', 'result']) but got {param_names}"
    )

    sig_str = str(sig).lower()
    assert "conn" not in sig_str, (
        f"Signature contains 'conn' — connection string must not be in task args: {sig}"
    )
    assert "api_key" not in sig_str, (
        f"Signature contains 'api_key' — API keys must not be in task args: {sig}"
    )


# ---------------------------------------------------------------------------
# Test 3: Layer 3 idempotency — skip Haiku call when chunk_metadata row exists
# ---------------------------------------------------------------------------


def test_layer_3_idempotency_skips_haiku_when_metadata_exists(monkeypatch):
    """generate_metadata skips enrich_chunk when SELECT COUNT(*) FROM chunk_metadata > 0.

    This is the Layer 3 idempotency contract: previously enriched chunks must not
    trigger additional Haiku API calls on task retry (prevents re-billing).
    """
    from app.worker.tasks.pipeline.metadata import generate_metadata

    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    # Cursor sequence:
    #   fetchall() → [("c1", "content")]   (SELECT id, content FROM chunks)
    #   fetchone() → (1,)                  (SELECT COUNT(*) FROM chunk_metadata)
    mock_cursor = _MockCursor(fetchone_sequence=[(1,)])
    mock_cursor.fetchall_result = [("c1", "content")]
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    mock_enrich = MagicMock()
    mock_emit = MagicMock()

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.psycopg2.connect",
        lambda _: mock_conn,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.enrich_chunk",
        mock_enrich,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.emit",
        mock_emit,
    )

    generate_metadata.run(
        {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
    )

    # Layer 3 fired: enrich_chunk must NOT be called
    assert mock_enrich.call_count == 0, (
        f"Layer 3 idempotency failed: enrich_chunk was called {mock_enrich.call_count} "
        "time(s) but should have been skipped (chunk_metadata row exists)"
    )


# ---------------------------------------------------------------------------
# Test 4: enrich_chunk IS called when no existing chunk_metadata row
# ---------------------------------------------------------------------------


def test_generate_metadata_calls_enrich_when_no_existing_metadata(monkeypatch):
    """generate_metadata calls enrich_chunk once with the chunk content when no metadata exists."""
    from app.services.metadata_service import ChunkMetadataAndEntities
    from app.worker.tasks.pipeline.metadata import generate_metadata

    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    # Cursor sequence:
    #   fetchall() → [("c1", "the content")]
    #   fetchone() for COUNT(*) → (0,)        (no metadata row)
    mock_cursor = _MockCursor(fetchone_sequence=[(0,)])
    mock_cursor.fetchall_result = [("c1", "the content")]
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    mock_meta = ChunkMetadataAndEntities(
        summary="s", keywords=["k"], questions=["q?"], entities=[]
    )
    mock_enrich = MagicMock(return_value=mock_meta)
    mock_emit = MagicMock()

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.psycopg2.connect",
        lambda _: mock_conn,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.enrich_chunk",
        mock_enrich,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.emit",
        mock_emit,
    )

    generate_metadata.run(
        {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
    )

    assert mock_enrich.call_count == 1, (
        f"Expected enrich_chunk to be called once but got {mock_enrich.call_count}"
    )
    assert mock_enrich.call_args[0][0] == "the content", (
        f"Expected enrich_chunk called with 'the content' but got "
        f"{mock_enrich.call_args[0][0]!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: Entity UPSERT SQL shape — ON CONFLICT (normalized, type) + chunk_entities
# ---------------------------------------------------------------------------


def test_generate_metadata_upserts_entities_with_on_conflict_normalized_type(monkeypatch):
    """generate_metadata executes entity UPSERT with ON CONFLICT (normalized, type) DO UPDATE
    and chunk_entities INSERT with ON CONFLICT DO NOTHING.

    This proves the entity deduplication contract (T-02-04-02):
    same entity across chunks → one entities row, N chunk_entities rows.
    """
    from app.services.metadata_service import ChunkMetadataAndEntities, EntityExtraction
    from app.worker.tasks.pipeline.metadata import generate_metadata

    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    # Cursor sequence:
    #   fetchall() → [("c1", "content with Acme")]
    #   fetchone() for COUNT(*) → (0,)       (no metadata row — proceed)
    #   fetchone() for RETURNING id → ("ent-uuid-1",)   (entity UPSERT returns id)
    mock_cursor = _MockCursor(fetchone_sequence=[(0,), ("ent-uuid-1",)])
    mock_cursor.fetchall_result = [("c1", "content with Acme")]
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    entity = EntityExtraction(name="Acme Corp", type="product", normalized="acme corp")
    mock_meta = ChunkMetadataAndEntities(
        summary="s", keywords=["k"], questions=["q?"], entities=[entity]
    )
    mock_enrich = MagicMock(return_value=mock_meta)
    mock_emit = MagicMock()

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.psycopg2.connect",
        lambda _: mock_conn,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.enrich_chunk",
        mock_enrich,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.emit",
        mock_emit,
    )

    generate_metadata.run(
        {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
    )

    all_sqls = [sql for sql, _ in mock_cursor.executed_sqls]

    # Entity UPSERT: must contain INSERT INTO entities + ON CONFLICT (normalized, type)
    entity_upsert_sqls = [
        s for s in all_sqls
        if "INSERT INTO entities" in s and "ON CONFLICT (normalized, type)" in s
    ]
    assert len(entity_upsert_sqls) >= 1, (
        "Expected at least one SQL with 'INSERT INTO entities' AND "
        "'ON CONFLICT (normalized, type)' but found none.\n"
        f"All recorded SQLs:\n" + "\n".join(all_sqls)
    )

    # chunk_entities INSERT: must contain INSERT INTO chunk_entities + ON CONFLICT DO NOTHING
    ce_sqls = [
        s for s in all_sqls
        if "INSERT INTO chunk_entities" in s and "ON CONFLICT DO NOTHING" in s
    ]
    assert len(ce_sqls) >= 1, (
        "Expected at least one SQL with 'INSERT INTO chunk_entities' AND "
        "'ON CONFLICT DO NOTHING' but found none.\n"
        f"All recorded SQLs:\n" + "\n".join(all_sqls)
    )


# ---------------------------------------------------------------------------
# Test 6: Event sequence — metadata.started then metadata.complete
# ---------------------------------------------------------------------------


def test_generate_metadata_emits_event_sequence(monkeypatch):
    """generate_metadata emits metadata.started then metadata.complete (in that order)."""
    from app.services.metadata_service import ChunkMetadataAndEntities
    from app.worker.tasks.pipeline.metadata import generate_metadata

    mock_db = MagicMock()
    mock_agent = _make_mock_agent()
    mock_db.get.return_value = mock_agent

    # Cursor: COUNT(*) → (1,) so we skip enrich (not testing entity path here)
    mock_cursor = _MockCursor(fetchone_sequence=[(1,)])
    mock_cursor.fetchall_result = [("c1", "content")]
    mock_conn = _make_mock_tenant_conn(mock_cursor)

    emitted_events = []

    def capture_emit(job_id, event_type, payload, db, redis_client):
        emitted_events.append((job_id, event_type))

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.fernet_decrypt",
        lambda _: "fake-conn",
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.psycopg2.connect",
        lambda _: mock_conn,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.enrich_chunk",
        MagicMock(),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.metadata.emit",
        capture_emit,
    )

    generate_metadata.run(
        {"tenant_id": "t", "agent_id": "a", "job_id": "j", "document_ids": ["d1"]}
    )

    event_types = [et for _, et in emitted_events]
    assert event_types == ["metadata.started", "metadata.complete"], (
        f"Expected event sequence ['metadata.started', 'metadata.complete'] "
        f"but got {event_types}"
    )

    # Verify job_id is passed correctly to all emit calls
    for job_id_arg, _ in emitted_events:
        assert job_id_arg == "j", (
            f"Expected job_id='j' but got {job_id_arg!r}"
        )
