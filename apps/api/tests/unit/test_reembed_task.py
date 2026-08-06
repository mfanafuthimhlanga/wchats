"""
Unit tests for reembed_corpus Celery task — PROD-06.

Tests:
  1. test_reembed_corpus_acks_late                          — acks_late=True, max_retries=3, default_retry_delay=10
  2. test_reembed_corpus_migrates_chunks                    — (case a) migration path upserts ON CONFLICT with correct model
  3. test_reembed_corpus_idempotent_when_fully_migrated     — (case b) idempotent: zero embed_texts calls when all rows migrated
  4. test_reembed_corpus_tenant_isolation                   — (case c) only one agent's conn strings decrypted; no tenant loop
  5. test_reembed_corpus_reindex_uses_direct_connection     — (case d) REINDEX uses neon_direct_connection_string in AUTOCOMMIT

Patch targets are symbols imported into app.worker.tasks.pipeline.reembed:
    - app.worker.tasks.pipeline.reembed.get_sync_db
    - app.worker.tasks.pipeline.reembed.fernet_decrypt
    - app.worker.tasks.pipeline.reembed.psycopg2.connect
    - app.worker.tasks.pipeline.reembed.bedrock_embedding_service
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
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("MAX_UPLOAD_SIZE_MB", "50")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret_value_for_tests_only")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_webhook_secret")

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

from contextlib import contextmanager
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TARGET_MODEL = "amazon.titan-embed-text-v2:0"
_POOLED_CONN = "fake-pooled-conn"
_DIRECT_CONN = "fake-direct-conn"
_AGENT_ID = "agent-test-uuid"
_ENCRYPTED_POOLED = b"encrypted-pooled"
_ENCRYPTED_DIRECT = b"encrypted-direct"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sync_db_context(mock_db):
    """Return a context-manager factory that yields mock_db."""

    @contextmanager
    def _ctx():
        yield mock_db

    return _ctx


def _make_mock_agent():
    """Return a mock Agent with distinct encrypted connection strings."""
    agent = MagicMock()
    agent.neon_connection_string = _ENCRYPTED_POOLED
    agent.neon_direct_connection_string = _ENCRYPTED_DIRECT
    return agent


def _make_mock_db(mock_agent):
    """Return a mock SQLAlchemy session that returns mock_agent for any db.get call."""
    mock_db = MagicMock()
    mock_db.get.return_value = mock_agent
    return mock_db


def _make_mock_bedrock_svc(vectors=None):
    """Return a mock bedrock_embedding_service with active_embedding_model and embed_texts."""
    if vectors is None:
        vectors = [[0.1] * 1024, [0.2] * 1024]
    mock_svc = MagicMock()
    mock_svc.active_embedding_model.return_value = _TARGET_MODEL
    mock_svc.embed_texts.return_value = vectors
    return mock_svc


class _StatefulCursor:
    """Mock psycopg2 cursor that records execute() calls and returns rows on first fetchall.

    Subsequent fetchall() calls return [] so the batch loop terminates.
    """

    def __init__(self, first_fetch_rows=None):
        self.executed_sqls = []
        self._first_fetch_rows = first_fetch_rows if first_fetch_rows is not None else []
        self._fetchall_count = 0

    def execute(self, sql, params=None):
        self.executed_sqls.append((str(sql), params))

    def fetchall(self):
        self._fetchall_count += 1
        if self._fetchall_count == 1:
            return self._first_fetch_rows
        return []

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _SimpleCursor:
    """Mock psycopg2 cursor for the REINDEX connection — records execute() only."""

    def __init__(self):
        self.executed_sqls = []

    def execute(self, sql, params=None):
        self.executed_sqls.append((str(sql), params))

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _make_dml_conn(cursor):
    """Return a mock psycopg2 DML connection that returns cursor from .cursor()."""
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def _make_reindex_conn(cursor):
    """Return a mock psycopg2 REINDEX connection."""
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.set_isolation_level = MagicMock()
    return conn


def _mock_fernet_decrypt(encrypted):
    """Map known encrypted bytes to test connection strings."""
    if encrypted == _ENCRYPTED_POOLED:
        return _POOLED_CONN
    if encrypted == _ENCRYPTED_DIRECT:
        return _DIRECT_CONN
    return f"unknown-{encrypted!r}"


def _run_task(monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_svc):
    """Patch all external dependencies and invoke reembed_corpus.run()."""
    from app.worker.tasks.pipeline.reembed import reembed_corpus

    connect_call_log = []

    def mock_connect(conn_str_arg):
        connect_call_log.append(conn_str_arg)
        if conn_str_arg == _POOLED_CONN:
            return mock_dml_conn
        if conn_str_arg == _DIRECT_CONN:
            return mock_reindex_conn
        raise ValueError(f"Unexpected conn_str: {conn_str_arg!r}")

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.reembed.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.reembed.fernet_decrypt",
        _mock_fernet_decrypt,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.reembed.psycopg2.connect",
        mock_connect,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.reembed.bedrock_embedding_service",
        mock_svc,
    )

    result = reembed_corpus.run(_AGENT_ID)
    return result, connect_call_log


# ---------------------------------------------------------------------------
# Test 1: acks_late=True, max_retries=3, default_retry_delay=10
# ---------------------------------------------------------------------------


def test_reembed_corpus_acks_late():
    """reembed_corpus must have acks_late=True and correct retry settings (CLAUDE.md rule 5)."""
    from app.worker.tasks.pipeline.reembed import reembed_corpus

    assert reembed_corpus.acks_late is True, (
        "acks_late must be True (CLAUDE.md rule 5 — mandatory on every Celery task)"
    )
    assert reembed_corpus.max_retries == 3, (
        f"max_retries must be 3 but got {reembed_corpus.max_retries}"
    )
    assert reembed_corpus.default_retry_delay == 10, (
        f"default_retry_delay must be 10 but got {reembed_corpus.default_retry_delay}"
    )


# ---------------------------------------------------------------------------
# Test 2 (case a): Migration path — chunks are re-embedded and upserted
# ---------------------------------------------------------------------------


def test_reembed_corpus_migrates_chunks(monkeypatch):
    """Migration path: upserts with ON CONFLICT (chunk_id) DO UPDATE and the active Bedrock model."""
    mock_agent = _make_mock_agent()
    mock_db = _make_mock_db(mock_agent)

    dml_cursor = _StatefulCursor(first_fetch_rows=[("c1", "text1"), ("c2", "text2")])
    mock_dml_conn = _make_dml_conn(dml_cursor)

    reindex_cursor = _SimpleCursor()
    mock_reindex_conn = _make_reindex_conn(reindex_cursor)

    mock_svc = _make_mock_bedrock_svc(vectors=[[0.1] * 1024, [0.2] * 1024])

    result, _ = _run_task(monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_svc)

    # embed_texts called once with the chunk texts and "document" input type
    mock_svc.embed_texts.assert_called_once_with(["text1", "text2"], "document")

    # Return value carries correct totals
    assert result["total_reembedded"] == 2, (
        f"Expected total_reembedded=2 but got {result['total_reembedded']}"
    )
    assert result["model"] == _TARGET_MODEL, (
        f"Expected model={_TARGET_MODEL!r} but got {result['model']!r}"
    )
    assert result["agent_id"] == _AGENT_ID

    # ON CONFLICT (chunk_id) DO UPDATE upsert SQL was executed
    all_sqls = [sql for sql, _ in dml_cursor.executed_sqls]
    upsert_sqls = [
        s for s in all_sqls
        if "INSERT INTO embeddings" in s and "ON CONFLICT (chunk_id) DO UPDATE" in s
    ]
    assert len(upsert_sqls) >= 1, (
        "Expected INSERT INTO embeddings ... ON CONFLICT (chunk_id) DO UPDATE SQL "
        f"but none found. All SQLs:\n" + "\n".join(all_sqls)
    )

    # Model bound in the INSERT params equals the target model
    insert_params = [
        params
        for sql, params in dml_cursor.executed_sqls
        if "INSERT INTO embeddings" in sql
    ]
    assert any(params[1] == _TARGET_MODEL for params in insert_params), (
        f"Expected INSERT params to include model={_TARGET_MODEL!r}. "
        f"INSERT params: {insert_params}"
    )


# ---------------------------------------------------------------------------
# Test 3 (case b): Idempotent path — zero embed_texts when all rows migrated
# ---------------------------------------------------------------------------


def test_reembed_corpus_idempotent_when_fully_migrated(monkeypatch):
    """When all chunks already carry the target model, embed_texts is never called."""
    mock_agent = _make_mock_agent()
    mock_db = _make_mock_db(mock_agent)

    # fetchall returns [] immediately — no chunks need migration
    dml_cursor = _StatefulCursor(first_fetch_rows=[])
    mock_dml_conn = _make_dml_conn(dml_cursor)

    reindex_cursor = _SimpleCursor()
    mock_reindex_conn = _make_reindex_conn(reindex_cursor)

    mock_svc = _make_mock_bedrock_svc()

    result, _ = _run_task(monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_svc)

    # embed_texts must not be called at all
    assert mock_svc.embed_texts.call_count == 0, (
        f"embed_texts should not be called when all chunks are already migrated, "
        f"but was called {mock_svc.embed_texts.call_count} time(s)"
    )

    # Zero rows upserted
    assert result["total_reembedded"] == 0, (
        f"Expected total_reembedded=0 but got {result['total_reembedded']}"
    )

    # No INSERT SQL executed on the DML cursor
    insert_sqls = [
        sql for sql, _ in dml_cursor.executed_sqls if "INSERT INTO embeddings" in sql
    ]
    assert len(insert_sqls) == 0, (
        f"Expected no INSERT SQL in idempotent path but found {len(insert_sqls)}: {insert_sqls}"
    )


# ---------------------------------------------------------------------------
# Test 4 (case c): Tenant isolation — only one agent's conn strings decrypted
# ---------------------------------------------------------------------------


def test_reembed_corpus_tenant_isolation(monkeypatch):
    """Task decrypts only the single agent's two connection strings — no tenant loop."""
    mock_agent = _make_mock_agent()
    mock_db = _make_mock_db(mock_agent)

    dml_cursor = _StatefulCursor(first_fetch_rows=[])
    mock_dml_conn = _make_dml_conn(dml_cursor)
    reindex_cursor = _SimpleCursor()
    mock_reindex_conn = _make_reindex_conn(reindex_cursor)
    mock_svc = _make_mock_bedrock_svc()

    decrypt_calls = []

    def tracking_decrypt(encrypted):
        decrypt_calls.append(encrypted)
        return _mock_fernet_decrypt(encrypted)

    connect_calls = []

    def tracking_connect(conn_str_arg):
        connect_calls.append(conn_str_arg)
        if conn_str_arg == _POOLED_CONN:
            return mock_dml_conn
        if conn_str_arg == _DIRECT_CONN:
            return mock_reindex_conn
        raise ValueError(f"Unexpected conn_str: {conn_str_arg!r}")

    monkeypatch.setattr(
        "app.worker.tasks.pipeline.reembed.get_sync_db",
        _make_sync_db_context(mock_db),
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.reembed.fernet_decrypt",
        tracking_decrypt,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.reembed.psycopg2.connect",
        tracking_connect,
    )
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.reembed.bedrock_embedding_service",
        mock_svc,
    )

    from app.worker.tasks.pipeline.reembed import reembed_corpus

    reembed_corpus.run(_AGENT_ID)

    # db.get(Agent, ...) called exactly once — no loop over all tenants
    assert mock_db.get.call_count == 1, (
        f"Expected db.get called exactly once (no tenant loop) "
        f"but was called {mock_db.get.call_count} time(s)"
    )

    # Exactly 2 fernet_decrypt calls: pooled + direct for the ONE agent
    assert len(decrypt_calls) == 2, (
        f"Expected exactly 2 fernet_decrypt calls (pooled + direct) "
        f"but got {len(decrypt_calls)}: {decrypt_calls}"
    )
    assert _ENCRYPTED_POOLED in decrypt_calls, (
        f"Expected fernet_decrypt({_ENCRYPTED_POOLED!r}) to be called. Calls: {decrypt_calls}"
    )
    assert _ENCRYPTED_DIRECT in decrypt_calls, (
        f"Expected fernet_decrypt({_ENCRYPTED_DIRECT!r}) to be called. Calls: {decrypt_calls}"
    )

    # Exactly 2 psycopg2.connect calls — no extra tenant connections opened
    assert len(connect_calls) == 2, (
        f"Expected exactly 2 psycopg2.connect calls (DML + REINDEX) "
        f"but got {len(connect_calls)}: {connect_calls}"
    )


# ---------------------------------------------------------------------------
# Test 5 (case d): REINDEX uses the direct connection string in AUTOCOMMIT
# ---------------------------------------------------------------------------


def test_reembed_corpus_reindex_uses_direct_connection(monkeypatch):
    """REINDEX INDEX CONCURRENTLY uses neon_direct_connection_string in ISOLATION_LEVEL_AUTOCOMMIT."""
    import psycopg2.extensions

    mock_agent = _make_mock_agent()
    mock_db = _make_mock_db(mock_agent)

    dml_cursor = _StatefulCursor(first_fetch_rows=[])
    mock_dml_conn = _make_dml_conn(dml_cursor)

    reindex_cursor = _SimpleCursor()
    mock_reindex_conn = _make_reindex_conn(reindex_cursor)

    # Track operation order on the REINDEX connection
    reindex_ops = []

    def _track_isolation(level):
        reindex_ops.append(("set_isolation_level", level))

    mock_reindex_conn.set_isolation_level = _track_isolation

    original_execute = reindex_cursor.execute

    def _track_execute(sql, params=None):
        reindex_ops.append(("execute", str(sql)))
        original_execute(sql, params)

    reindex_cursor.execute = _track_execute

    mock_svc = _make_mock_bedrock_svc()

    _, connect_calls = _run_task(
        monkeypatch, mock_db, mock_dml_conn, mock_reindex_conn, mock_svc
    )

    # Second psycopg2.connect call must use the DIRECT (non-pooled) connection string
    assert len(connect_calls) >= 2, (
        f"Expected at least 2 psycopg2.connect calls but got {len(connect_calls)}: {connect_calls}"
    )
    assert connect_calls[1] == _DIRECT_CONN, (
        f"REINDEX connection must use the direct endpoint ({_DIRECT_CONN!r}) "
        f"but psycopg2.connect was called with {connect_calls[1]!r}. "
        f"All connect calls: {connect_calls}"
    )

    # set_isolation_level must be called BEFORE REINDEX execute on the REINDEX connection
    isolation_ops = [
        i for i, (op, _) in enumerate(reindex_ops) if op == "set_isolation_level"
    ]
    reindex_exec_ops = [
        i
        for i, (op, sql) in enumerate(reindex_ops)
        if op == "execute" and "REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx" in sql
    ]

    assert isolation_ops, (
        "set_isolation_level was never called on the REINDEX connection. "
        "REINDEX CONCURRENTLY requires AUTOCOMMIT isolation (Pitfall 7)."
    )
    assert reindex_exec_ops, (
        "REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx was not executed "
        "on the REINDEX connection."
    )
    assert isolation_ops[0] < reindex_exec_ops[0], (
        f"set_isolation_level must be called BEFORE REINDEX execute. "
        f"Got isolation at op-index {isolation_ops[0]}, REINDEX at op-index {reindex_exec_ops[0]}. "
        f"Full reindex_ops: {reindex_ops}"
    )

    # Verify ISOLATION_LEVEL_AUTOCOMMIT value was passed
    autocommit_ops = [
        level
        for op, level in reindex_ops
        if op == "set_isolation_level"
        and level == psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT
    ]
    assert autocommit_ops, (
        "set_isolation_level was called but NOT with ISOLATION_LEVEL_AUTOCOMMIT. "
        f"Values passed: {[level for op, level in reindex_ops if op == 'set_isolation_level']}"
    )
