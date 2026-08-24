"""
Unit tests for app.domain.docling_service — Wave 2 (ING-01, ING-02).

Tests:
  1. parse_document raises RuntimeError when ConversionStatus != SUCCESS
  2. parse_document returns result.document on SUCCESS
  3. parse_document_from_bytes wraps content in DocumentStream correctly

Tests 1 and 2 patch `app.domain.docling_service._converter` (the module-level
singleton) rather than the import path of DocumentConverter itself — this ensures
the patch targets the object that the module functions actually call.

Test 3 patches `docling.datamodel.base_models.DocumentStream`, not
`app.domain.docling_service.DocumentStream`. The service imports DocumentStream
inside parse_document_from_bytes (docling_service.py:98) because docling ships only
in the pipeline worker, so the service module has no such attribute and patching it
raises AttributeError. Because that import runs at call time, patching the name on
its source module is what the service actually picks up.
tests/unit/test_pipeline_patch_targets.py enforces that correspondence on every run.

Environment setup must precede any `from app` import.
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

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# docling ships only in the optional `pipeline` extra. CI installs `[dev]` only, and
# the documented local gate has no docling either — without this guard the whole
# module is a collection ERROR that aborts the run before any other test executes.
#
# Read the skip line literally: it means UNEXECUTED, not "would pass". No job in
# ci.yml installs the `pipeline` extra, so nothing in this repo has ever run these
# three tests, and a green CI summary is no evidence about them whatsoever. What IS
# checked on every run is narrower and stated plainly:
# tests/unit/test_pipeline_patch_targets.py asserts each patch target below still
# corresponds to a real attribute or import in the service under test — the failure
# mode that made test 3 error the moment docling was present.
pytest.importorskip("docling", reason="docling is in the optional `pipeline` extra")

from docling.datamodel.base_models import ConversionStatus  # noqa: E402  (must follow importorskip)

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

    monkeypatch.setattr("app.domain.docling_service._converter", mock_conv)

    from app.domain.docling_service import parse_document

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

    monkeypatch.setattr("app.domain.docling_service._converter", mock_conv)

    from app.domain.docling_service import parse_document

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

    monkeypatch.setattr("app.domain.docling_service._converter", mock_conv)

    # Patch DocumentStream on its source module: the service does a call-time
    # `from docling.datamodel.base_models import DocumentStream`, so the service
    # module itself has no such attribute to patch.
    with patch("docling.datamodel.base_models.DocumentStream") as mock_ds_cls:
        mock_stream_instance = MagicMock()
        mock_ds_cls.return_value = mock_stream_instance

        from app.domain.docling_service import parse_document_from_bytes

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
