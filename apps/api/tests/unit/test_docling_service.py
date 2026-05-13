"""
Unit tests for app.services.docling_service — Wave 2 (ING-01, ING-02).

Tests:
  1. parse_document raises RuntimeError when ConversionStatus != SUCCESS
  2. parse_document returns result.document on SUCCESS
  3. parse_document_from_bytes wraps content in DocumentStream correctly

All tests patch `app.services.docling_service._converter` (the module-level
singleton) rather than the import path of DocumentConverter itself — this ensures
the patch targets the object that the module functions actually call.

Environment setup must precede any `from app` import.
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
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage")

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from docling.datamodel.base_models import ConversionStatus


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parse_document_raises_on_failure(monkeypatch):
    """parse_document raises RuntimeError when ConversionStatus is not SUCCESS."""
    mock_error = MagicMock()
    mock_error.error_message = "bad pdf"

    mock_result = MagicMock()
    mock_result.status = ConversionStatus.FAILURE
    mock_result.errors = [mock_error]

    mock_conv = MagicMock()
    mock_conv.convert.return_value = mock_result

    monkeypatch.setattr("app.services.docling_service._converter", mock_conv)

    from app.services.docling_service import parse_document

    with pytest.raises(RuntimeError) as exc_info:
        parse_document(Path("/fake.pdf"))

    assert "bad pdf" in str(exc_info.value)


def test_parse_document_returns_document_on_success(monkeypatch):
    """parse_document returns result.document when ConversionStatus is SUCCESS."""
    sentinel_doc = object()

    mock_result = MagicMock()
    mock_result.status = ConversionStatus.SUCCESS
    mock_result.document = sentinel_doc

    mock_conv = MagicMock()
    mock_conv.convert.return_value = mock_result

    monkeypatch.setattr("app.services.docling_service._converter", mock_conv)

    from app.services.docling_service import parse_document

    result = parse_document(Path("/some/document.pdf"))
    assert result is sentinel_doc


def test_parse_document_from_bytes_uses_document_stream(monkeypatch):
    """parse_document_from_bytes wraps content in DocumentStream with name=filename."""
    sentinel_doc = object()

    mock_result = MagicMock()
    mock_result.status = ConversionStatus.SUCCESS
    mock_result.document = sentinel_doc

    mock_conv = MagicMock()
    mock_conv.convert.return_value = mock_result

    monkeypatch.setattr("app.services.docling_service._converter", mock_conv)

    captured_streams = []

    original_ds_cls = None

    # Patch DocumentStream to capture its construction arguments
    with patch("app.services.docling_service.DocumentStream") as mock_ds_cls:
        mock_stream_instance = MagicMock()
        mock_ds_cls.return_value = mock_stream_instance

        from app.services.docling_service import parse_document_from_bytes

        result = parse_document_from_bytes(b"fake-pdf-bytes", "name.pdf")

    # Assert DocumentStream was called with name="name.pdf"
    mock_ds_cls.assert_called_once()
    call_kwargs = mock_ds_cls.call_args
    # Accept both positional and keyword args
    if call_kwargs.kwargs:
        assert call_kwargs.kwargs.get("name") == "name.pdf"
    else:
        assert call_kwargs.args[0] == "name.pdf"

    # Assert the converter received the DocumentStream instance
    mock_conv.convert.assert_called_once_with(mock_stream_instance, raises_on_error=False)

    # Assert the returned document is the sentinel
    assert result is sentinel_doc
