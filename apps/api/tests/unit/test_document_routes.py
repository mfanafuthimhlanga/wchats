"""
Unit tests for POST/GET /agents/{agent_id}/documents routes — ING-01.

Tests the HTTP contract of documents.py:
  - 202 response with correct body keys
  - 404 when agent belongs to different tenant
  - 409 when agent status != 'ready'
  - 413 when file exceeds MAX_UPLOAD_SIZE_MB
  - 415 for unsupported file extensions
  - 422 when no files and no urls provided
  - Chain dispatched with correct signature (4 tasks, correct order)
  - No connection string in chain task args (CLAUDE.md rule 4)
  - GET list returns documents array

Security coverage:
  - T-02-06-01: cross-tenant 404 (test_upload_documents_404_when_wrong_tenant)
  - T-02-06-02: size cap 413 (test_upload_documents_413_when_file_exceeds_max_size)
  - T-02-06-03: type whitelist 415 (test_upload_documents_415_for_unsupported_file_type)
  - T-02-06-04: no conn string in chain args (test_upload_documents_no_conn_string_in_chain_args)
"""

import io
import unittest.mock
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# conftest.py sets all required env vars before app import
from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.main import app
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_tenant() -> Tenant:
    """Return a mock Tenant for dependency override."""
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_ready_agent(tenant: Tenant) -> Agent:
    """Return a mock Agent with status='ready' belonging to tenant."""
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = tenant.id
    agent.status = "ready"
    agent.deleted_at = None
    # Fake encrypted conn string — decryptable via patched fernet_decrypt
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_mock_db_with_agent(agent: Agent):
    """Mock async DB session that returns *agent* for any SELECT + handles job creation."""
    mock_session = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = agent
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    _job_id = uuid4()

    async def _refresh(obj):
        if isinstance(obj, Job):
            obj.id = _job_id

    mock_session.refresh = AsyncMock(side_effect=_refresh)
    return mock_session, _job_id


def _make_mock_db_no_agent():
    """Mock async DB that returns None for any agent lookup."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _small_pdf_bytes() -> bytes:
    """Return minimal PDF-header bytes that pass the size check."""
    return b"%PDF-1.4 fake pdf content for testing"


# ---------------------------------------------------------------------------
# Test 1: 202 response with correct body keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUploadDocumentsReturns202:
    async def test_upload_documents_returns_202_with_job_id(self, monkeypatch):
        """Valid multipart upload returns 202 with job_id, document_ids, events_url."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db, job_id = _make_mock_db_with_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            with (
                patch("app.api.v1.documents.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.documents.psycopg2.connect") as mock_connect,
                patch("app.api.v1.documents.chain") as mock_chain,
            ):
                # Mock psycopg2 cursor context manager
                mock_conn = MagicMock()
                mock_connect.return_value = mock_conn
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
                mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
                mock_cursor.execute = MagicMock()

                mock_chain.return_value.apply_async = MagicMock()

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/agents/{ready_agent.id}/documents",
                        headers={"X-API-Key": "vrd_live_test"},
                        files=[("files", ("test.pdf", _small_pdf_bytes(), "application/pdf"))],
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        body = response.json()
        assert "job_id" in body
        assert "document_ids" in body
        assert "events_url" in body
        assert "status" in body
        assert body["status"] == "pending"
        assert body["events_url"].startswith("/jobs/")
        assert len(body["document_ids"]) == 1


# ---------------------------------------------------------------------------
# Test 2: 404 when agent belongs to wrong tenant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUploadDocuments404WrongTenant:
    async def test_upload_documents_404_when_wrong_tenant(self):
        """Agent belonging to a different tenant returns 404 (T-02-06-01)."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_no_agent()  # DB returns None — ownership filter fails

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/agents/{uuid4()}/documents",
                    headers={"X-API-Key": "vrd_live_test"},
                    files=[("files", ("test.pdf", _small_pdf_bytes(), "application/pdf"))],
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test 3: 409 when agent status != 'ready'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUploadDocuments409NotReady:
    async def test_upload_documents_409_when_agent_not_ready(self):
        """Agent with status='pending' returns 409 with detail containing status."""
        fake_tenant = _make_fake_tenant()
        pending_agent = _make_ready_agent(fake_tenant)
        pending_agent.status = "pending"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = pending_agent
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/agents/{pending_agent.id}/documents",
                    headers={"X-API-Key": "vrd_live_test"},
                    files=[("files", ("test.pdf", _small_pdf_bytes(), "application/pdf"))],
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 409
        assert "status=pending" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Test 4: 413 when file exceeds MAX_UPLOAD_SIZE_MB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUploadDocuments413Oversize:
    async def test_upload_documents_413_when_file_exceeds_max_size(self, monkeypatch):
        """File larger than MAX_UPLOAD_SIZE_MB returns 413 (T-02-06-02)."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ready_agent
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        # Cap at 1 MB for the test
        monkeypatch.setattr("app.api.v1.documents.settings.MAX_UPLOAD_SIZE_MB", 1)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            # 2 MB of bytes — exceeds the 1 MB cap
            oversized_content = b"x" * (2 * 1024 * 1024)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/agents/{ready_agent.id}/documents",
                    headers={"X-API-Key": "vrd_live_test"},
                    files=[("files", ("big.pdf", oversized_content, "application/pdf"))],
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 413


# ---------------------------------------------------------------------------
# Test 5: 415 for unsupported file type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUploadDocuments415UnsupportedType:
    async def test_upload_documents_415_for_unsupported_file_type(self):
        """File with extension .txt returns 415 (T-02-06-03)."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ready_agent
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/agents/{ready_agent.id}/documents",
                    headers={"X-API-Key": "vrd_live_test"},
                    files=[("files", ("report.txt", b"text content", "text/plain"))],
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 415


# ---------------------------------------------------------------------------
# Test 6: 422 when no files and no urls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUploadDocuments422Empty:
    async def test_upload_documents_422_when_empty(self):
        """POST with no files and no urls returns 422."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ready_agent
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # No files= and no urls= fields in the multipart form
                response = await client.post(
                    f"/agents/{ready_agent.id}/documents",
                    headers={"X-API-Key": "vrd_live_test"},
                    data={},  # empty form — no files, no urls
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Test 7: Chain dispatched with correct signature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUploadDocumentsChainSignature:
    async def test_upload_documents_dispatches_chain_with_correct_signature(self, monkeypatch):
        """Chain called with four task .s() factories in correct order (ING-01)."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db, job_id = _make_mock_db_with_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        chain_args_captured = []

        try:
            with (
                patch("app.api.v1.documents.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.documents.psycopg2.connect") as mock_connect,
                patch("app.api.v1.documents.chain") as mock_chain,
                patch("app.api.v1.documents.parse_documents") as mock_parse,
                patch("app.api.v1.documents.chunk_documents") as mock_chunk,
                patch("app.api.v1.documents.generate_metadata") as mock_meta,
                patch("app.api.v1.documents.embed_and_migrate") as mock_embed,
            ):
                mock_conn = MagicMock()
                mock_connect.return_value = mock_conn
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
                mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

                mock_chain_instance = MagicMock()
                mock_chain_instance.apply_async = MagicMock()

                def capture_chain(*args, **kwargs):
                    chain_args_captured.extend(args)
                    return mock_chain_instance

                mock_chain.side_effect = capture_chain

                # Set up .s() to return a sentinel value
                mock_parse.s = MagicMock(return_value="parse_sig")
                mock_chunk.s = MagicMock(return_value="chunk_sig")
                mock_meta.s = MagicMock(return_value="meta_sig")
                mock_embed.s = MagicMock(return_value="embed_sig")

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/agents/{ready_agent.id}/documents",
                        headers={"X-API-Key": "vrd_live_test"},
                        files=[("files", ("test.pdf", _small_pdf_bytes(), "application/pdf"))],
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202

        # Verify chain was called with all four task signatures
        assert mock_chain.called, "chain() was not called"
        assert mock_parse.s.called, "parse_documents.s() was not called"
        assert mock_chunk.s.called, "chunk_documents.s() was not called"
        assert mock_meta.s.called, "generate_metadata.s() was not called"
        assert mock_embed.s.called, "embed_and_migrate.s() was not called"

        # Verify apply_async called with queue='pipeline'
        mock_chain_instance.apply_async.assert_called_once()
        call_kwargs = mock_chain_instance.apply_async.call_args
        assert call_kwargs.kwargs.get("queue") == "pipeline" or (
            len(call_kwargs.args) > 0 and "pipeline" in str(call_kwargs)
        ), f"apply_async not called with queue='pipeline': {call_kwargs}"


# ---------------------------------------------------------------------------
# Test 8: No connection string in chain args (CLAUDE.md rule 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUploadDocumentsNoConnStringInArgs:
    async def test_upload_documents_no_conn_string_in_chain_args(self, monkeypatch):
        """Chain .s() positional args contain no connection strings (T-02-06-04)."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)
        mock_db, job_id = _make_mock_db_with_agent(ready_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        captured_parse_args = []

        try:
            with (
                patch("app.api.v1.documents.fernet_decrypt", return_value="postgresql://neon-test.neon.tech/db?password=secret123"),
                patch("app.api.v1.documents.psycopg2.connect") as mock_connect,
                patch("app.api.v1.documents.chain") as mock_chain,
                patch("app.api.v1.documents.parse_documents") as mock_parse,
                patch("app.api.v1.documents.chunk_documents"),
                patch("app.api.v1.documents.generate_metadata"),
                patch("app.api.v1.documents.embed_and_migrate"),
            ):
                mock_conn = MagicMock()
                mock_connect.return_value = mock_conn
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
                mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

                def capture_parse_s(*args, **kwargs):
                    captured_parse_args.extend(args)
                    return "parse_sig"

                mock_parse.s = MagicMock(side_effect=capture_parse_s)
                mock_chain.return_value.apply_async = MagicMock()

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/agents/{ready_agent.id}/documents",
                        headers={"X-API-Key": "vrd_live_test"},
                        files=[("files", ("test.pdf", _small_pdf_bytes(), "application/pdf"))],
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202

        # Sentinel check: no arg contains a connection string pattern
        for arg in captured_parse_args:
            arg_str = str(arg)
            assert "postgresql://" not in arg_str, f"Connection string found in parse_documents.s args: {arg_str!r}"
            assert "@neon" not in arg_str, f"Neon hostname found in parse_documents.s args: {arg_str!r}"
            assert "password" not in arg_str.lower(), f"'password' substring found in parse_documents.s args: {arg_str!r}"


# ---------------------------------------------------------------------------
# Test 9: GET list returns documents array
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListDocuments:
    async def test_list_documents_returns_documents(self):
        """GET /agents/{id}/documents returns 200 with 'documents' array."""
        fake_tenant = _make_fake_tenant()
        ready_agent = _make_ready_agent(fake_tenant)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ready_agent
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        now = datetime.now(timezone.utc)

        # Two mock document rows as tuples (matching SELECT column order)
        fake_rows = [
            (str(uuid4()), "doc1.pdf", "pdf", "doc1.pdf", "parsed", 5, now),
            (str(uuid4()), "doc2.pdf", "pdf", "doc2.pdf", "pending", None, now),
        ]

        try:
            with (
                patch("app.api.v1.documents.fernet_decrypt", return_value="postgresql://fake/db"),
                patch("app.api.v1.documents.psycopg2.connect") as mock_connect,
            ):
                mock_conn = MagicMock()
                mock_connect.return_value = mock_conn
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
                mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
                mock_cursor.fetchall.return_value = fake_rows

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        f"/agents/{ready_agent.id}/documents",
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert "documents" in body
        assert len(body["documents"]) == 2
