"""
Unit tests for S3 upload storage (PROD-12, PROD-13) — storage_service + route integration.

Task 1 tests (storage_service + upload route):
  1. test_upload_key_format              — "{agent_id}/{doc_id}{ext}" tenant-scoped key
  2. test_put_bytes_calls_put_object     — put_object(Bucket, Key, Body) called correctly
  3. test_get_bytes_calls_get_object     — get_object returns Body bytes
  4. test_upload_documents_writes_to_s3  — route calls put_bytes; no local disk write

Task 2 tests (parse reads from S3; embed.py cleanup fixed):
  5. test_parse_task_reads_from_s3       — file-source branch calls get_bytes + parse_document_from_bytes
  6. test_embed_py_no_vrd_uploads_literal — embed.py source must not contain hardcoded /vrd-uploads

Patch targets mirror the importing module pattern:
  - app.services.storage_service._s3     (module-level lazy-init client)
  - app.services.storage_service.put_bytes / get_bytes (canonical function locations)
  - app.worker.tasks.pipeline.parse.parse_document_from_bytes (imported into parse)
"""

import base64
import os
import pathlib

# ---------------------------------------------------------------------------
# Environment setup — MUST run before any `from app` import (pydantic-settings)
# conftest.py covers the base vars; add S3-specific overrides here.
# ---------------------------------------------------------------------------
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("NEON_API_KEY", "test_neon")
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb")
os.environ.setdefault("ADMIN_KEY", "test_admin")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")
# S3_UPLOADS_BUCKET must be set so settings validation passes and tests can
# assert put_object is called with the correct Bucket value.
os.environ.setdefault("S3_UPLOADS_BUCKET", "test-uploads-bucket")

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# ===========================================================================
# Task 1 — storage_service unit tests
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 1: upload_key format
# ---------------------------------------------------------------------------


def test_upload_key_format():
    """upload_key(agent_id, doc_id, ext) returns '{agent_id}/{doc_id}{ext}'.

    The UUIDv4 agent_id prefix provides ~122-bit cross-tenant isolation without
    a separate tenant prefix (T-13-06-01: key-guessing mitigated by entropy).
    """
    from app.services.storage_service import upload_key

    agent_id = str(uuid4())
    doc_id = str(uuid4())

    assert upload_key(agent_id, doc_id, ".pdf") == f"{agent_id}/{doc_id}.pdf"
    assert upload_key(agent_id, doc_id, ".png") == f"{agent_id}/{doc_id}.png"
    assert upload_key(agent_id, doc_id, ".md") == f"{agent_id}/{doc_id}.md"


# ---------------------------------------------------------------------------
# Test 2: put_bytes calls put_object with correct Bucket / Key / Body
# ---------------------------------------------------------------------------


def test_put_bytes_calls_put_object():
    """put_bytes(key, data) calls S3 put_object(Bucket=S3_UPLOADS_BUCKET, Key=key, Body=data).

    Patch target: app.services.storage_service._s3 (module-level lazy client)
    so no real boto3 network call is made (mirrors bedrock test pattern).
    """
    # BACKLOG 1.29: the module-scope os.environ.setdefault("S3_UPLOADS_BUCKET")
    # at line 39 lands AFTER app.core.config has already built its Settings
    # singleton in a full-suite run, so settings.S3_UPLOADS_BUCKET was "" here
    # and the assertion below compared "" to "" -- vacuously true. Pin it on
    # the settings object instead, which does not depend on import order.
    from app.core.config import settings

    with patch("app.services.storage_service._s3") as mock_s3,             patch.object(settings, "S3_UPLOADS_BUCKET", "test-uploads-bucket"):
        from app.services.storage_service import put_bytes

        test_key = "agent-abc/doc-xyz.pdf"
        test_data = b"%PDF-1.4 test content"

        put_bytes(test_key, test_data)

        mock_s3.put_object.assert_called_once_with(
            Bucket=settings.S3_UPLOADS_BUCKET,
            Key=test_key,
            Body=test_data,
        )


# ---------------------------------------------------------------------------
# Test 3: get_bytes calls get_object and returns the body bytes
# ---------------------------------------------------------------------------


def test_get_bytes_calls_get_object():
    """get_bytes(key) calls S3 get_object(Bucket, Key) and returns Body.read() bytes.

    Patch target: app.services.storage_service._s3.
    No presigned URL is generated — server-side IAM-authenticated fetch only
    (T-13-06-02: no public/presigned exposure of uploads).
    """
    # BACKLOG 1.29 -- see test_put_bytes_calls_put_object.
    from app.core.config import settings

    with patch("app.services.storage_service._s3") as mock_s3,             patch.object(settings, "S3_UPLOADS_BUCKET", "test-uploads-bucket"):
        from app.services.storage_service import get_bytes

        test_key = "agent-abc/doc-xyz.pdf"
        expected_bytes = b"raw file content from s3"

        mock_body = MagicMock()
        mock_body.read.return_value = expected_bytes
        mock_s3.get_object.return_value = {"Body": mock_body}

        result = get_bytes(test_key)

        mock_s3.get_object.assert_called_once_with(
            Bucket=settings.S3_UPLOADS_BUCKET,
            Key=test_key,
        )
        assert result == expected_bytes, (
            f"Expected {expected_bytes!r} but got {result!r}"
        )


# ---------------------------------------------------------------------------
# Test 4: upload_documents route writes each file to S3 via put_bytes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_documents_writes_to_s3_not_disk():
    """POST /agents/{id}/documents writes uploaded files to S3 via storage_service.put_bytes.

    Assertions:
    - Response is 202 Accepted.
    - put_bytes is called exactly once per uploaded file.
    - The S3 key is tenant-scoped: contains the agent_id UUID prefix.
    - The S3 key has the correct file extension.
    - No local Path.write_bytes call (the upload dir / local_path no longer exists).
    """
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_current_tenant
    from app.core.database import get_async_db
    from app.main import app
    from app.models.agent import Agent
    from app.models.job import Job
    from app.models.tenant import Tenant

    # ── fixtures ──────────────────────────────────────────────────────────
    fake_tenant = MagicMock(spec=Tenant)
    fake_tenant.id = uuid4()

    ready_agent = MagicMock(spec=Agent)
    ready_agent.id = uuid4()
    ready_agent.tenant_id = fake_tenant.id
    ready_agent.status = "ready"
    ready_agent.deleted_at = None
    ready_agent.neon_connection_string = b"fake-encrypted-bytes"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = ready_agent
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    _job_id = uuid4()

    async def _refresh(obj):
        if isinstance(obj, Job):
            obj.id = _job_id

    mock_session.refresh = AsyncMock(side_effect=_refresh)

    app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
    app.dependency_overrides[get_async_db] = lambda: mock_session

    file_bytes = b"%PDF-1.4 fake pdf content for testing"
    put_bytes_calls: list[tuple] = []

    def capture_put_bytes(key: str, data: bytes) -> None:
        put_bytes_calls.append((key, data))

    try:
        with (
            patch("app.api.v1.documents.fernet_decrypt", return_value="postgresql://fake/db"),
            patch("app.api.v1.documents.psycopg2.connect") as mock_connect,
            patch("app.api.v1.documents.chain") as mock_chain,
            patch("app.services.storage_service.put_bytes", side_effect=capture_put_bytes),
        ):
            mock_conn = MagicMock()
            mock_connect.return_value = mock_conn
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_chain.return_value.apply_async = MagicMock()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{ready_agent.id}/documents",
                    headers={"X-API-Key": "vrd_live_test"},
                    files=[("files", ("report.pdf", file_bytes, "application/pdf"))],
                )
    finally:
        app.dependency_overrides.clear()

    # ── assertions ────────────────────────────────────────────────────────
    assert response.status_code == 202, (
        f"Expected 202 Accepted but got {response.status_code}: {response.text}"
    )

    # put_bytes must have been called exactly once (one file uploaded)
    assert len(put_bytes_calls) == 1, (
        f"Expected put_bytes called once but got {len(put_bytes_calls)} calls: {put_bytes_calls}"
    )

    key, body = put_bytes_calls[0]
    # Key must be tenant-scoped: "{agent_id}/{doc_id}.pdf"
    assert key.startswith(str(ready_agent.id) + "/"), (
        f"S3 key should start with agent_id '{{agent_id}}/' but got: {key!r}"
    )
    assert key.endswith(".pdf"), (
        f"S3 key should end with '.pdf' (the uploaded file's extension) but got: {key!r}"
    )
    # Body must be the raw file bytes (T-13-06-04: not logged; only asserted here)
    assert body == file_bytes, (
        f"put_bytes body mismatch. Expected {file_bytes!r} but got {body!r}"
    )


# ===========================================================================
# Task 2 — parse reads from S3; embed.py /vrd-uploads literal removed
# ===========================================================================


# ---------------------------------------------------------------------------
# Helper: sync DB context manager factory (mirrors test_parse_task.py pattern)
# ---------------------------------------------------------------------------


def _make_sync_db_context(mock_db):
    """Return a context manager factory that yields mock_db."""
    @contextmanager
    def _ctx():
        yield mock_db
    return _ctx


# ---------------------------------------------------------------------------
# Test 5: parse_documents file-source branch reads from S3
# ---------------------------------------------------------------------------


def test_parse_task_reads_from_s3(monkeypatch):
    """parse_documents file-source branch calls storage_service.get_bytes, not local disk read.

    After the S3 migration (PROD-13):
    - storage_service.get_bytes(upload_key(agent_id, doc_id, ext)) is called.
    - The returned bytes are passed to parse_document_from_bytes(content, source_uri).
    - settings.UPLOADS_DIR is NOT read in the file path (no local disk access).
    """
    from app.worker.tasks.pipeline.parse import parse_documents

    mock_db = MagicMock()
    mock_agent = MagicMock()
    mock_agent.id = "a1"
    mock_agent.neon_connection_string = b"encrypted-conn"
    mock_db.get.return_value = mock_agent

    doc_id = "d1"
    mock_cursor = MagicMock()
    mock_cursor.fetchone.side_effect = [
        (1,),  # pre-check: 1 unparsed document
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
    monkeypatch.setattr("app.worker.tasks.pipeline.parse.emit", MagicMock())

    # Track get_bytes calls
    get_bytes_calls: list[str] = []

    def fake_get_bytes(key: str) -> bytes:
        get_bytes_calls.append(key)
        return b"%PDF-1.4 fake pdf from s3"

    monkeypatch.setattr("app.services.storage_service.get_bytes", fake_get_bytes)

    # Mock parse_document_from_bytes to return a DoclingDocument-like object
    mock_doc = MagicMock()
    mock_doc.pages = {1: MagicMock()}
    mock_parse_bytes = MagicMock(return_value=mock_doc)
    monkeypatch.setattr(
        "app.worker.tasks.pipeline.parse.parse_document_from_bytes", mock_parse_bytes
    )

    # Run the task
    result = parse_documents.run("t1", "a1", "j1", [doc_id])

    # Assert chain dict returned
    assert result == {
        "tenant_id": "t1",
        "agent_id": "a1",
        "job_id": "j1",
        "document_ids": [doc_id],
    }

    # Assert storage_service.get_bytes was called (file read from S3, not local disk)
    assert len(get_bytes_calls) == 1, (
        f"Expected storage_service.get_bytes to be called once for the file-source branch, "
        f"but it was called {len(get_bytes_calls)} times. "
        f"This means parse.py is not yet reading from S3 (PROD-13 not implemented)."
    )
    key_used = get_bytes_calls[0]
    assert "a1" in key_used, (
        f"S3 key should contain agent_id 'a1' but got: {key_used!r}"
    )
    assert doc_id in key_used, (
        f"S3 key should contain doc_id '{doc_id}' but got: {key_used!r}"
    )

    # Assert parse_document_from_bytes was called (not parse_document which needs a file path)
    mock_parse_bytes.assert_called_once()
    parse_call_args = mock_parse_bytes.call_args
    # First arg should be the bytes returned by get_bytes
    assert parse_call_args.args[0] == b"%PDF-1.4 fake pdf from s3", (
        f"parse_document_from_bytes should receive S3 bytes as first arg, "
        f"got: {parse_call_args.args[0]!r}"
    )


# ---------------------------------------------------------------------------
# Test 6: embed.py must not contain the hardcoded /vrd-uploads literal
# ---------------------------------------------------------------------------


def test_embed_py_no_vrd_uploads_literal():
    """embed.py must not reference the hardcoded /vrd-uploads path (Landmine 4 fix).

    The cleanup block in embed.py (lines 257-282 before migration) used
    Path('/vrd-uploads') — hardcoded, NOT settings.UPLOADS_DIR, and never valid
    in an S3-backed deployment. After migration, this block is removed entirely
    because S3 files are durable source bytes, not ephemeral temp files.
    """
    embed_path = (
        pathlib.Path(__file__).parent.parent.parent
        / "app" / "worker" / "tasks" / "pipeline" / "embed.py"
    )
    assert embed_path.exists(), f"embed.py not found at {embed_path}"
    source = embed_path.read_text(encoding="utf-8")
    assert "/vrd-uploads" not in source, (
        "embed.py still contains the hardcoded '/vrd-uploads' literal. "
        "Remove the temp-file cleanup block (it is a no-op after the S3 migration "
        "and the hardcoded path was never settings.UPLOADS_DIR — Landmine 4)."
    )
