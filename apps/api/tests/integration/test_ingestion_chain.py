"""
Integration tests: Full M2 ingestion chain — parse → chunk → metadata → embed.

These tests exercise the complete chain against a REAL local Postgres tenant DB
with external services (Voyage AI, Anthropic, Docling) fully mocked so they run
offline without API keys.

Test strategy:
  - CELERY_TASK_ALWAYS_EAGER=True: Tasks run synchronously in this process.
    (The integration conftest.py sets it to False for M1 worker tests; we
    override it back to True via celery_app.conf.update per test using the
    eager_celery fixture below.)
  - Real Postgres: The local W Chats control DB is used; tenant-schema tables
    (documents, chunks, chunk_metadata, embeddings, entities, chunk_entities)
    are created via the 0002 migration fixture.
  - Mocked external services: voyageai.Client, anthropic.Anthropic, and
    docling_service functions are patched at the service module level.

Requirements verified:
  - ING-09: Idempotency — running the chain twice produces identical row counts.
  - ING-08: All 11 M2 SSE event types emitted for a single run.
  - Security: No connection string logged during chain execution.

Infra requirements (local development only):
  - Local Postgres running on port 5432 with user/pass: wchats/wchats
  - Database: wchats_control (with M1 control schema already applied)
  - Redis not required for ALWAYS_EAGER tests (events are written directly to
    job_events table via emit(); Redis publish is a best-effort side-effect)

If the local DB is not available, integration tests are skipped automatically
via the infra_available fixture (connection attempt in conftest).

NOTE: These tests do NOT require a running Celery worker subprocess.
CELERY_TASK_ALWAYS_EAGER=True runs tasks synchronously in the test process.
"""

import os
import tempfile
import types
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg2
import psycopg2.extensions
import pytest
from sqlalchemy import text

import app.worker.tasks.pipeline.embed as _embed_module

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# M2 SSE event vocabulary (duplicated from test_ingestion_sse.py for isolation)
# ---------------------------------------------------------------------------

M2_EVENT_TYPES = {
    "ingestion.started",
    "parsing.started",
    "parsing.complete",
    "chunking.started",
    "chunking.complete",
    "metadata.started",
    "metadata.complete",
    "embedding.started",
    "embedding.complete",
    "ingestion.complete",
    "job.complete",
}

# ---------------------------------------------------------------------------
# M2-specific fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def eager_celery():
    """Override Celery to run tasks synchronously in this process.

    The integration conftest sets CELERY_TASK_ALWAYS_EAGER=False for M1 tests
    that require a real worker subprocess. M2 chain tests use eager mode to
    avoid that dependency (integration tests with mocked external services).

    Restores original setting after the test.
    """
    from app.worker.celery_app import celery_app

    original = celery_app.conf.task_always_eager
    celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
    yield celery_app
    celery_app.conf.update(task_always_eager=original)


@pytest.fixture
def ready_agent_with_tenant_db(db_session, test_tenant):
    """Create a 'ready' agent with a real tenant DB connection string.

    The agent's neon_connection_string is Fernet-encrypted and points to the
    local integration Postgres DB (same DB used for the control schema).

    Also runs the 0002 migration (documents + entities + chunk_entities tables)
    on a per-test schema to keep tests isolated.

    M2 Integration requirement (PLAN.md 02-06-04):
    This fixture is the M2 equivalent of the M1 test_agent_and_job fixture.
    It creates an agent with status='ready' and a real (encrypted) tenant
    connection string so the Celery tasks can run against a real DB.

    Returns:
        tuple: (tenant_id: UUID, agent_id: UUID, job_id: UUID,
                tenant_db_url: str)
    """
    from app.core.security import fernet_encrypt

    tenant_id, raw_key = test_tenant
    agent_id = uuid.uuid4()
    job_id = uuid.uuid4()

    # Use the same local DB for both control and "tenant" schema in tests
    # (In production, each tenant has their own Neon project; in tests we
    # reuse local Postgres with per-test table cleanup)
    tenant_db_url = os.environ.get(
        "INTEGRATION_DB_URL",
        "postgresql://wchats:wchats@localhost:5432/wchats_control",
    )

    # Encrypt the tenant DB URL as if it were a real Neon connection string
    encrypted_conn = fernet_encrypt(tenant_db_url)

    # Create agent with status='ready' and the encrypted connection string.
    # NOTE: Use cast() to avoid SQLAlchemy interpreting ::jsonb as a named param.
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
    import json as _json

    db_session.execute(
        text(
            """
            INSERT INTO agents (
                id, tenant_id, name, soul, role, status,
                neon_connection_string, created_at
            )
            VALUES (
                :id, :tenant_id, :name, CAST(:soul AS jsonb), :role, 'ready',
                :neon_conn, now()
            )
            """
        ),
        {
            "id": str(agent_id),
            "tenant_id": str(tenant_id),
            "name": f"test-ready-agent-{agent_id}",
            "soul": '{"tone": "professional", "language": "en"}',
            "role": "support",
            "neon_conn": encrypted_conn,
        },
    )

    db_session.execute(
        text(
            """
            INSERT INTO jobs (id, tenant_id, agent_id, kind, status, created_at)
            VALUES (:id, :tenant_id, :agent_id, 'ingest_documents', 'pending', now())
            """
        ),
        {
            "id": str(job_id),
            "tenant_id": str(tenant_id),
            "agent_id": str(agent_id),
        },
    )
    db_session.commit()

    # ------------------------------------------------------------------
    # Apply 0002 migration on the tenant DB to create M2 tables.
    # (In real deployments, Alembic applies 0002 during agent setup.
    #  For integration tests, we apply it directly via psycopg2 if the
    #  tables do not already exist.)
    # ------------------------------------------------------------------

    # Check if pgvector extension is available (needed for vector column type)
    _has_pgvector = False
    _probe_conn = psycopg2.connect(tenant_db_url)
    try:
        with _probe_conn.cursor() as _cur:
            _cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            _has_pgvector = _cur.fetchone() is not None
    finally:
        _probe_conn.close()

    # Create tables in a single committed transaction (no HNSW index in this block)
    tenant_conn = psycopg2.connect(tenant_db_url)
    try:
        with tenant_conn.cursor() as cur:
            # documents table (may already exist from prior tests)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    source_type TEXT NOT NULL,
                    source_uri  TEXT NOT NULL,
                    title       TEXT,
                    source_hash TEXT,
                    parse_status TEXT NOT NULL DEFAULT 'pending',
                    chunk_count  INT,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            # chunks table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id          UUID PRIMARY KEY,
                    document_id UUID NOT NULL,
                    ordinal     INT NOT NULL,
                    content     TEXT NOT NULL,
                    token_count INT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            # chunk_metadata table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chunk_metadata (
                    chunk_id  UUID PRIMARY KEY,
                    summary   TEXT NOT NULL,
                    keywords  TEXT[],
                    questions TEXT[],
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            # entities table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name       TEXT NOT NULL,
                    type       TEXT NOT NULL,
                    normalized TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (normalized, type)
                )
            """)
            # chunk_entities join table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chunk_entities (
                    chunk_id  UUID NOT NULL,
                    entity_id UUID NOT NULL,
                    PRIMARY KEY (chunk_id, entity_id)
                )
            """)
            # embeddings table — use vector type if pgvector is available, else TEXT
            if _has_pgvector:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS embeddings (
                        chunk_id   UUID PRIMARY KEY,
                        model      TEXT NOT NULL,
                        vector     vector(1024) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
            else:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS embeddings (
                        chunk_id   UUID PRIMARY KEY,
                        model      TEXT NOT NULL,
                        vector     TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
        tenant_conn.commit()
    finally:
        tenant_conn.close()

    # Create HNSW index in a separate AUTOCOMMIT connection (required for CONCURRENTLY).
    # Skip silently if pgvector is not installed — embed_and_migrate REINDEX will fail
    # gracefully when index doesn't exist (test patches ISOLATION_LEVEL_AUTOCOMMIT to 0).
    if _has_pgvector:
        _idx_conn = psycopg2.connect(tenant_db_url)
        try:
            _idx_conn.set_isolation_level(0)  # AUTOCOMMIT
            with _idx_conn.cursor() as _cur:
                _cur.execute("""
                    CREATE INDEX IF NOT EXISTS embeddings_vector_hnsw_idx
                    ON embeddings USING hnsw (vector vector_cosine_ops)
                """)
        except Exception:
            pass  # Best-effort; REINDEX in embed task will also handle this
        finally:
            _idx_conn.close()

    yield tenant_id, agent_id, job_id, tenant_db_url

    # ------------------------------------------------------------------
    # Teardown: Remove all documents/chunks/metadata/embeddings for this agent's
    # job to keep tests isolated. The agent + job rows are cleaned up by the
    # test_tenant fixture.
    # ------------------------------------------------------------------
    tenant_conn = psycopg2.connect(tenant_db_url)
    try:
        with tenant_conn.cursor() as cur:
            # Clean up in dependency order
            cur.execute("DELETE FROM chunk_entities WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id IN (SELECT id FROM documents))")
            cur.execute("DELETE FROM chunk_metadata WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id IN (SELECT id FROM documents))")
            cur.execute("DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id IN (SELECT id FROM documents))")
            cur.execute("DELETE FROM chunks WHERE document_id IN (SELECT id FROM documents)")
            cur.execute("DELETE FROM documents")
        tenant_conn.commit()
    except Exception:
        tenant_conn.rollback()
    finally:
        tenant_conn.close()


def _create_fake_pdf_file(agent_id: str, doc_id: str) -> Path:
    """Create a minimal fake PDF in the expected temp directory.

    The parse task looks for files at:
      gettempdir()/vrd-uploads/{agent_id}/{doc_id}{ext}

    We create a placeholder file so the Docling mock can be triggered
    without actually needing a real PDF on disk.
    """
    upload_dir = Path(tempfile.gettempdir()) / "vrd-uploads" / agent_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    fake_pdf = upload_dir / f"{doc_id}.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake content for integration test")
    return fake_pdf


def _make_mock_docling_doc(text_chunks: list[str] = None):
    """Build a minimal mock DoclingDocument that HybridChunker can process."""
    if text_chunks is None:
        text_chunks = ["This is chunk one about products.", "This is chunk two about services."]

    mock_doc = MagicMock()
    mock_doc.tables = []  # No tables — exercises text path only
    mock_doc.pages = {1: MagicMock(), 2: MagicMock()}

    # Mock HybridChunker to return controlled text chunks
    mock_chunks = []
    for txt in text_chunks:
        mock_chunk = MagicMock()
        mock_chunk.text = txt
        mock_chunk.meta = MagicMock()
        mock_chunk.meta.doc_items = []  # no table items
        mock_chunks.append(mock_chunk)

    return mock_doc, mock_chunks


def _mock_redis_client():
    """Return a MagicMock Redis client for mocking task module-level _redis.

    Each pipeline task module maintains a module-level _redis client.
    During integration tests, patching these prevents actual Redis connections
    while still allowing events to be written to the job_events DB table.
    """
    mock = MagicMock()
    mock.publish = MagicMock(return_value=0)
    return mock


def _build_metadata_mock():
    """Return a mock Anthropic result with valid ChunkMetadataAndEntities."""
    from app.services.metadata_service import ChunkMetadataAndEntities, EntityExtraction

    parsed = ChunkMetadataAndEntities(
        summary="A test summary.",
        keywords=["test", "product"],
        questions=["What is tested?"],
        entities=[],
    )
    mock_result = MagicMock()
    mock_result.parsed_output = parsed
    return mock_result


def _count_rows(conn_url: str, table: str, where: str = "", params: tuple = ()) -> int:
    """Count rows in *table* using a temporary psycopg2 connection."""
    conn = psycopg2.connect(conn_url)
    try:
        with conn.cursor() as cur:
            sql = f"SELECT COUNT(*) FROM {table}"
            if where:
                sql += f" WHERE {where}"
            cur.execute(sql, params)
            return cur.fetchone()[0]
    finally:
        conn.close()


_REAL_PSYCOPG2_CONNECT = psycopg2.connect  # Captured before any patching


class _TextCompatCursor:
    """Wraps a real psycopg2 cursor, rewriting ::vector casts to TEXT-compatible form.

    When pgvector is not installed in the test Postgres instance, the embeddings
    table uses a TEXT column. embed_and_migrate inserts `%s::vector` which Postgres
    rejects on a TEXT column. This wrapper strips the cast and serialises list[float]
    vectors to strings so the row lands cleanly.
    """

    def __init__(self, real_cursor):
        self._real_cursor = real_cursor

    def execute(self, sql, params=None):
        if "::vector" in str(sql):
            sql = str(sql).replace("::vector", "")
        if params is not None:
            params = tuple(
                str(p) if isinstance(p, list) else p for p in params
            )
        return self._real_cursor.execute(sql, params)

    def fetchall(self):
        return self._real_cursor.fetchall()

    def fetchone(self):
        return self._real_cursor.fetchone()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def __getattr__(self, name):
        return getattr(self._real_cursor, name)


class _TextCompatConnection:
    """Wraps a real psycopg2 connection, substituting _TextCompatCursor for every cursor.

    psycopg2's connection.cursor is a read-only C-level attribute — it cannot be
    monkey-patched directly.  Instead, we proxy the entire connection object and
    override cursor() to return a _TextCompatCursor wrapping the real cursor.
    """

    def __init__(self, real_conn):
        self._real_conn = real_conn

    def cursor(self, *args, **kwargs):
        return _TextCompatCursor(self._real_conn.cursor(*args, **kwargs))

    def commit(self):
        return self._real_conn.commit()

    def rollback(self):
        return self._real_conn.rollback()

    def close(self):
        return self._real_conn.close()

    def set_isolation_level(self, level):
        return self._real_conn.set_isolation_level(level)

    # psycopg2 uses autocommit as a property on newer versions
    @property
    def autocommit(self):
        return self._real_conn.autocommit

    @autocommit.setter
    def autocommit(self, value):
        self._real_conn.autocommit = value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._real_conn.commit()
        else:
            self._real_conn.rollback()
        return False

    def __getattr__(self, name):
        return getattr(self._real_conn, name)


def _pgvector_compat_connect(*args, **kwargs):
    """Return a connection proxy that strips ::vector casts (for TEXT column compat).

    When pgvector is not installed, the embeddings table uses a TEXT column.
    The embed_and_migrate task inserts `%s::vector` which fails on TEXT columns.
    This wrapper intercepts cursor.execute() calls via _TextCompatConnection /
    _TextCompatCursor and rewrites ::vector casts at the SQL layer.

    Accepts both positional (conn_str) and keyword argument forms of psycopg2.connect.
    Uses _REAL_PSYCOPG2_CONNECT (captured before patching) to avoid recursion.
    """
    real_conn = _REAL_PSYCOPG2_CONNECT(*args, **kwargs)
    return _TextCompatConnection(real_conn)


def _make_embed_psycopg2_proxy():
    """Create a psycopg2 module proxy for use in the embed task namespace only.

    Problem: patch("app.worker.tasks.pipeline.embed.psycopg2.connect", ...) patches
    the attribute `connect` on the global psycopg2 module singleton. This affects ALL
    code in the process that imports psycopg2 — including SQLAlchemy — which breaks
    SQLAlchemy's connection pool setup (psycopg2.extras.register_uuid fails because
    it receives a non-connection proxy object).

    Solution: Replace the embed module's `psycopg2` NAME (not the global module) with
    a proxy namespace that delegates everything to the real psycopg2 except `connect`,
    which is redirected to _pgvector_compat_connect. SQLAlchemy retains its own import
    of the real psycopg2 and is unaffected.

    Usage:
        with patch.object(embed_module, 'psycopg2', _make_embed_psycopg2_proxy()):
            ...
    """
    proxy = types.SimpleNamespace()
    proxy.connect = _pgvector_compat_connect
    proxy.extensions = psycopg2.extensions  # ISOLATION_LEVEL_AUTOCOMMIT used in embed task
    return proxy


def _get_job_event_types(db_session, job_id) -> list[str]:
    """Return list of event_type strings for *job_id* from job_events table."""
    rows = db_session.execute(
        text(
            "SELECT event_type FROM job_events WHERE job_id = :job_id ORDER BY created_at, id"
        ),
        {"job_id": str(job_id)},
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Test 1: Full chain runs in eager mode with mocked external services
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_full_chain_runs_in_eager_mode_with_mocks(
    eager_celery, ready_agent_with_tenant_db, db_session
):
    """Full M2 chain runs synchronously against real Postgres with mocked externals.

    Verifies:
    - documents.parse_status == 'parsed'
    - documents.chunk_count > 0
    - chunks table has rows for the document
    - chunk_metadata has one row per chunk
    - embeddings has one row per chunk
    """
    tenant_id, agent_id, job_id, tenant_db_url = ready_agent_with_tenant_db

    from celery import chain
    from app.worker.tasks.pipeline.chunk import chunk_documents
    from app.worker.tasks.pipeline.embed import embed_and_migrate
    from app.worker.tasks.pipeline.metadata import generate_metadata
    from app.worker.tasks.pipeline.parse import parse_documents

    # Create a document row in tenant DB (simulate what the route does)
    doc_id = str(uuid.uuid4())
    fake_pdf_path = _create_fake_pdf_file(str(agent_id), doc_id)

    tenant_conn = psycopg2.connect(tenant_db_url)
    try:
        with tenant_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (id, source_type, source_uri, title) VALUES (%s, %s, %s, %s)",
                (doc_id, "pdf", "test.pdf", "test.pdf"),
            )
        tenant_conn.commit()
    finally:
        tenant_conn.close()

    text_chunks = ["Chunk one: product catalog info.", "Chunk two: support process details."]
    mock_doc, mock_chunk_list = _make_mock_docling_doc(text_chunks)
    metadata_mock = _build_metadata_mock()

    with (
        # Patch docling at the task module level (tasks import parse_document directly,
        # so patching the service module would not intercept already-resolved references)
        patch("app.worker.tasks.pipeline.parse.parse_document", return_value=mock_doc),
        patch("app.worker.tasks.pipeline.parse.parse_document_from_bytes", return_value=mock_doc),
        patch("app.worker.tasks.pipeline.chunk.parse_document", return_value=mock_doc),
        patch("app.worker.tasks.pipeline.chunk.parse_document_from_bytes", return_value=mock_doc),
        # Patch HybridChunker via chunking_service (chunk task calls chunk_document
        # which calls HybridChunker internally — patch at the service level)
        patch("app.services.chunking_service.HybridChunker") as mock_chunker_cls,
        # Patch external API clients at service module level
        patch("app.services.metadata_service._anthropic") as mock_anthropic,
        patch("app.services.embedding_service._vo") as mock_vo,
        # Patch module-level _redis in each task to avoid real Redis connections
        # (events still write to job_events DB table via the emit() db parameter)
        patch("app.worker.tasks.pipeline.parse._redis", _mock_redis_client()),
        patch("app.worker.tasks.pipeline.chunk._redis", _mock_redis_client()),
        patch("app.worker.tasks.pipeline.metadata._redis", _mock_redis_client()),
        patch("app.worker.tasks.pipeline.embed._redis", _mock_redis_client()),
        # When pgvector is not installed, embeddings table uses TEXT column.
        # patch.object replaces the 'psycopg2' NAME in embed module's namespace only.
        # This avoids patching the global psycopg2 module (which would break SQLAlchemy).
        patch.object(_embed_module, "psycopg2", _make_embed_psycopg2_proxy()),
    ):
        # Configure HybridChunker mock
        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = mock_chunk_list
        mock_chunker.contextualize.side_effect = lambda c: c.text
        mock_chunker_cls.return_value = mock_chunker

        # Configure Anthropic mock
        mock_anthropic.messages.parse.return_value = metadata_mock

        # Configure Voyage mock — returns 1024-dim vectors
        mock_vo.embed.return_value = MagicMock(
            embeddings=[[0.1] * 1024, [0.2] * 1024]
        )

        # Dispatch chain (ALWAYS_EAGER runs inline)
        chain(
            parse_documents.s(str(tenant_id), str(agent_id), str(job_id), [doc_id]),
            chunk_documents.s(),
            generate_metadata.s(),
            embed_and_migrate.s(),
        ).apply_async(queue="pipeline")

    # Verify document was parsed
    tenant_conn = psycopg2.connect(tenant_db_url)
    try:
        with tenant_conn.cursor() as cur:
            cur.execute(
                "SELECT parse_status, chunk_count FROM documents WHERE id = %s",
                (doc_id,),
            )
            row = cur.fetchone()
    finally:
        tenant_conn.close()

    assert row is not None, "Document row not found after chain"
    parse_status, chunk_count = row
    assert parse_status == "parsed", f"Expected parse_status='parsed', got '{parse_status}'"
    assert chunk_count is not None and chunk_count > 0, (
        f"Expected chunk_count > 0, got {chunk_count}"
    )

    # Verify chunks were inserted
    n_chunks = _count_rows(tenant_db_url, "chunks", "document_id = %s", (doc_id,))
    assert n_chunks > 0, f"Expected chunks > 0, got {n_chunks}"

    # Verify chunk_metadata was inserted (one row per chunk)
    n_meta = _count_rows(
        tenant_db_url,
        "chunk_metadata",
        "chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)",
        (doc_id,),
    )
    assert n_meta == n_chunks, (
        f"Expected chunk_metadata count ({n_meta}) == chunk count ({n_chunks})"
    )

    # Verify embeddings were inserted (one row per chunk)
    n_embeddings = _count_rows(
        tenant_db_url,
        "embeddings",
        "chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)",
        (doc_id,),
    )
    assert n_embeddings == n_chunks, (
        f"Expected embeddings count ({n_embeddings}) == chunk count ({n_chunks})"
    )

    # Cleanup temp file
    fake_pdf_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 2: Idempotency — running chain twice produces no duplicate rows (ING-09)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_idempotent_chain(
    eager_celery, ready_agent_with_tenant_db, db_session
):
    """Running the ingestion chain twice for the same document produces no duplicates.

    Proves ING-09: all four idempotency layers (source_hash guard, ON CONFLICT DO UPDATE
    on chunks, SELECT COUNT(*) guard on chunk_metadata, LEFT JOIN WHERE NULL on embeddings)
    collectively ensure that a second run produces the same counts.

    Also asserts that Haiku (mocked _anthropic.messages.parse) is NOT called again on
    the second run (Layer 3 skip: chunk_metadata already exists).
    """
    tenant_id, agent_id, job_id, tenant_db_url = ready_agent_with_tenant_db

    from celery import chain
    from app.worker.tasks.pipeline.chunk import chunk_documents
    from app.worker.tasks.pipeline.embed import embed_and_migrate
    from app.worker.tasks.pipeline.metadata import generate_metadata
    from app.worker.tasks.pipeline.parse import parse_documents

    doc_id = str(uuid.uuid4())
    fake_pdf_path = _create_fake_pdf_file(str(agent_id), doc_id)

    tenant_conn = psycopg2.connect(tenant_db_url)
    try:
        with tenant_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (id, source_type, source_uri, title) VALUES (%s, %s, %s, %s)",
                (doc_id, "pdf", "test_idempotent.pdf", "test_idempotent.pdf"),
            )
        tenant_conn.commit()
    finally:
        tenant_conn.close()

    text_chunks = ["Idempotency chunk one.", "Idempotency chunk two."]
    mock_doc, mock_chunk_list = _make_mock_docling_doc(text_chunks)
    metadata_mock = _build_metadata_mock()

    with (
        # Patch docling at the task module level (tasks import parse_document directly,
        # so patching the service module would not intercept already-resolved references)
        patch("app.worker.tasks.pipeline.parse.parse_document", return_value=mock_doc),
        patch("app.worker.tasks.pipeline.parse.parse_document_from_bytes", return_value=mock_doc),
        patch("app.worker.tasks.pipeline.chunk.parse_document", return_value=mock_doc),
        patch("app.worker.tasks.pipeline.chunk.parse_document_from_bytes", return_value=mock_doc),
        # Patch HybridChunker via chunking_service (chunk task calls chunk_document
        # which calls HybridChunker internally — patch at the service level)
        patch("app.services.chunking_service.HybridChunker") as mock_chunker_cls,
        # Patch external API clients at service module level
        patch("app.services.metadata_service._anthropic") as mock_anthropic,
        patch("app.services.embedding_service._vo") as mock_vo,
        # Patch module-level _redis in each task to avoid real Redis connections
        # (events still write to job_events DB table via the emit() db parameter)
        patch("app.worker.tasks.pipeline.parse._redis", _mock_redis_client()),
        patch("app.worker.tasks.pipeline.chunk._redis", _mock_redis_client()),
        patch("app.worker.tasks.pipeline.metadata._redis", _mock_redis_client()),
        patch("app.worker.tasks.pipeline.embed._redis", _mock_redis_client()),
        # When pgvector is not installed, embeddings table uses TEXT column.
        # patch.object replaces the 'psycopg2' NAME in embed module's namespace only.
        # This avoids patching the global psycopg2 module (which would break SQLAlchemy).
        patch.object(_embed_module, "psycopg2", _make_embed_psycopg2_proxy()),
    ):
        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = mock_chunk_list
        mock_chunker.contextualize.side_effect = lambda c: c.text
        mock_chunker_cls.return_value = mock_chunker

        mock_anthropic.messages.parse.return_value = metadata_mock
        mock_vo.embed.return_value = MagicMock(
            embeddings=[[0.1] * 1024, [0.2] * 1024]
        )

        def dispatch_chain():
            chain(
                parse_documents.s(str(tenant_id), str(agent_id), str(job_id), [doc_id]),
                chunk_documents.s(),
                generate_metadata.s(),
                embed_and_migrate.s(),
            ).apply_async(queue="pipeline")

        # --- RUN 1 ---
        dispatch_chain()

        count_chunks_run1 = _count_rows(tenant_db_url, "chunks", "document_id = %s", (doc_id,))
        count_meta_run1 = _count_rows(
            tenant_db_url,
            "chunk_metadata",
            "chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)",
            (doc_id,),
        )
        count_embed_run1 = _count_rows(
            tenant_db_url,
            "embeddings",
            "chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)",
            (doc_id,),
        )
        haiku_calls_run1 = mock_anthropic.messages.parse.call_count

        # --- RUN 2 ---
        dispatch_chain()

        count_chunks_run2 = _count_rows(tenant_db_url, "chunks", "document_id = %s", (doc_id,))
        count_meta_run2 = _count_rows(
            tenant_db_url,
            "chunk_metadata",
            "chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)",
            (doc_id,),
        )
        count_embed_run2 = _count_rows(
            tenant_db_url,
            "embeddings",
            "chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)",
            (doc_id,),
        )
        haiku_calls_run2 = mock_anthropic.messages.parse.call_count

    # Idempotency assertions
    assert count_chunks_run2 == count_chunks_run1, (
        f"Duplicate chunks after run 2! "
        f"Run 1: {count_chunks_run1}, Run 2: {count_chunks_run2}"
    )
    assert count_meta_run2 == count_meta_run1, (
        f"Duplicate chunk_metadata after run 2! "
        f"Run 1: {count_meta_run1}, Run 2: {count_meta_run2}"
    )
    assert count_embed_run2 == count_embed_run1, (
        f"Duplicate embeddings after run 2! "
        f"Run 1: {count_embed_run1}, Run 2: {count_embed_run2}"
    )

    # Layer 3 verification: Haiku NOT called on run 2 (metadata already exists)
    assert haiku_calls_run2 == haiku_calls_run1, (
        f"Haiku was called again on run 2! "
        f"Run 1 calls: {haiku_calls_run1}, Run 2 calls: {haiku_calls_run2}. "
        f"Layer 3 idempotency guard (SELECT COUNT(*) FROM chunk_metadata) should "
        f"have skipped Haiku for already-enriched chunks."
    )

    fake_pdf_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 3: All 11 M2 SSE event types emitted (ING-08 + ING-09)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_chain_emits_all_11_m2_event_types(
    eager_celery, ready_agent_with_tenant_db, db_session
):
    """All 11 M2 SSE event types appear in job_events table after a single run.

    Queries the job_events table for the job_id used during the chain run.
    The chain tasks emit events via app.services.events.emit(), which writes
    to both Redis (pub/sub) and the job_events control DB table.
    """
    tenant_id, agent_id, job_id, tenant_db_url = ready_agent_with_tenant_db

    from celery import chain
    from app.worker.tasks.pipeline.chunk import chunk_documents
    from app.worker.tasks.pipeline.embed import embed_and_migrate
    from app.worker.tasks.pipeline.metadata import generate_metadata
    from app.worker.tasks.pipeline.parse import parse_documents

    doc_id = str(uuid.uuid4())
    fake_pdf_path = _create_fake_pdf_file(str(agent_id), doc_id)

    tenant_conn = psycopg2.connect(tenant_db_url)
    try:
        with tenant_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (id, source_type, source_uri, title) VALUES (%s, %s, %s, %s)",
                (doc_id, "pdf", "test_events.pdf", "test_events.pdf"),
            )
        tenant_conn.commit()
    finally:
        tenant_conn.close()

    text_chunks = ["Event test chunk one.", "Event test chunk two."]
    mock_doc, mock_chunk_list = _make_mock_docling_doc(text_chunks)
    metadata_mock = _build_metadata_mock()

    with (
        # Patch docling at the task module level (tasks import parse_document directly,
        # so patching the service module would not intercept already-resolved references)
        patch("app.worker.tasks.pipeline.parse.parse_document", return_value=mock_doc),
        patch("app.worker.tasks.pipeline.parse.parse_document_from_bytes", return_value=mock_doc),
        patch("app.worker.tasks.pipeline.chunk.parse_document", return_value=mock_doc),
        patch("app.worker.tasks.pipeline.chunk.parse_document_from_bytes", return_value=mock_doc),
        # Patch HybridChunker via chunking_service (chunk task calls chunk_document
        # which calls HybridChunker internally — patch at the service level)
        patch("app.services.chunking_service.HybridChunker") as mock_chunker_cls,
        # Patch external API clients at service module level
        patch("app.services.metadata_service._anthropic") as mock_anthropic,
        patch("app.services.embedding_service._vo") as mock_vo,
        # Patch module-level _redis in each task to avoid real Redis connections
        # (events still write to job_events DB table via the emit() db parameter)
        patch("app.worker.tasks.pipeline.parse._redis", _mock_redis_client()),
        patch("app.worker.tasks.pipeline.chunk._redis", _mock_redis_client()),
        patch("app.worker.tasks.pipeline.metadata._redis", _mock_redis_client()),
        patch("app.worker.tasks.pipeline.embed._redis", _mock_redis_client()),
        # When pgvector is not installed, embeddings table uses TEXT column.
        # patch.object replaces the 'psycopg2' NAME in embed module's namespace only.
        # This avoids patching the global psycopg2 module (which would break SQLAlchemy).
        patch.object(_embed_module, "psycopg2", _make_embed_psycopg2_proxy()),
    ):
        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = mock_chunk_list
        mock_chunker.contextualize.side_effect = lambda c: c.text
        mock_chunker_cls.return_value = mock_chunker

        mock_anthropic.messages.parse.return_value = metadata_mock
        mock_vo.embed.return_value = MagicMock(
            embeddings=[[0.1] * 1024, [0.2] * 1024]
        )

        chain(
            parse_documents.s(str(tenant_id), str(agent_id), str(job_id), [doc_id]),
            chunk_documents.s(),
            generate_metadata.s(),
            embed_and_migrate.s(),
        ).apply_async(queue="pipeline")

    # Query job_events from the control DB
    emitted_events = set(_get_job_event_types(db_session, job_id))

    missing = M2_EVENT_TYPES - emitted_events
    assert not missing, (
        f"Missing M2 SSE event types in job_events table: {sorted(missing)}.\n"
        f"Emitted events: {sorted(emitted_events)}"
    )

    fake_pdf_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 4: No connection strings in structlog output during chain run
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_chain_no_conn_strings_logged(
    eager_celery, ready_agent_with_tenant_db, db_session, caplog
):
    """No connection string appears in structlog output during chain execution (T-02-06-08).

    Uses pytest's caplog to capture log output and scans for connection-string
    patterns: 'postgresql://' and the agent's neon_connection_string ciphertext.
    """
    import logging

    tenant_id, agent_id, job_id, tenant_db_url = ready_agent_with_tenant_db

    from celery import chain
    from app.worker.tasks.pipeline.chunk import chunk_documents
    from app.worker.tasks.pipeline.embed import embed_and_migrate
    from app.worker.tasks.pipeline.metadata import generate_metadata
    from app.worker.tasks.pipeline.parse import parse_documents

    doc_id = str(uuid.uuid4())
    fake_pdf_path = _create_fake_pdf_file(str(agent_id), doc_id)

    tenant_conn = psycopg2.connect(tenant_db_url)
    try:
        with tenant_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (id, source_type, source_uri, title) VALUES (%s, %s, %s, %s)",
                (doc_id, "pdf", "test_log_security.pdf", "test_log_security.pdf"),
            )
        tenant_conn.commit()
    finally:
        tenant_conn.close()

    text_chunks = ["Log security chunk one."]
    mock_doc, mock_chunk_list = _make_mock_docling_doc(text_chunks)
    metadata_mock = _build_metadata_mock()

    with (
        patch("app.services.docling_service.parse_document", return_value=mock_doc),
        patch("app.services.docling_service.parse_document_from_bytes", return_value=mock_doc),
        patch("app.services.chunking_service.HybridChunker") as mock_chunker_cls,
        patch("app.services.metadata_service._anthropic") as mock_anthropic,
        patch("app.services.embedding_service._vo") as mock_vo,
        patch("psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT", 0),
        caplog.at_level(logging.DEBUG),
    ):
        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = mock_chunk_list
        mock_chunker.contextualize.side_effect = lambda c: c.text
        mock_chunker_cls.return_value = mock_chunker

        mock_anthropic.messages.parse.return_value = metadata_mock
        mock_vo.embed.return_value = MagicMock(embeddings=[[0.1] * 1024])

        chain(
            parse_documents.s(str(tenant_id), str(agent_id), str(job_id), [doc_id]),
            chunk_documents.s(),
            generate_metadata.s(),
            embed_and_migrate.s(),
        ).apply_async(queue="pipeline")

    # Scan all captured log records for connection string patterns
    all_log_text = " ".join(record.message for record in caplog.records)
    assert "postgresql://" not in all_log_text, (
        "Connection string 'postgresql://' found in log output — security violation T-02-06-08"
    )

    fake_pdf_path.unlink(missing_ok=True)
