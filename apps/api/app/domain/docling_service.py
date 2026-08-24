"""
Docling document parsing service — thin wrapper around DocumentConverter.

This module holds the module-level DocumentConverter singleton and exposes two
pure functions for parsing documents: one for local file paths and one for
URL-fetched bytes.

Design decisions:
  - DocumentConverter is initialised ONCE at module import (see _converter below).
    Loading DocLayNet + TableFormer ML models takes ~10-15 seconds and consumes
    ~1-2GB of RAM. Initialising inside the task function would pay this cost on
    every task invocation. Module-level init amortises the load across all task
    calls in the same worker process.
  - This mirrors the _redis module-level pattern from provision.py.
  - No class — plain functions only, following the events.py service pattern.

Threat context (T-02-02-02):
  Docling runs in the same worker process. A malicious PDF could in theory exploit
  the ML model pipeline. Risk accepted; future hardening (separate parser subprocess)
  deferred. RuntimeError on ConversionStatus.FAILURE prevents a single bad PDF
  from breaking the chain. PARTIAL_SUCCESS is treated as success (with a warning
  log) — pdfium bad_alloc on individual pages is a transient resource issue and
  does not invalidate the extracted content.
"""

from io import BytesIO
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# Lazy init — docling only available in the pipeline worker image.
# _get_converter() initialises DocumentConverter once per process on first call.
_converter = None


def _get_converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter  # noqa: PLC0415
        _converter = DocumentConverter()
    return _converter


def parse_document(file_path: Path) -> object:
    """Convert a local document file to a DoclingDocument.

    Args:
        file_path: Path to the document file (PDF, image).

    Returns:
        DoclingDocument on success or partial success (typed as object to avoid
        importing DoclingDocument at module level — runtime use only).

    Raises:
        RuntimeError: If Docling reports ConversionStatus.FAILURE (hard failure).
            PARTIAL_SUCCESS is accepted with a warning — pdfium bad_alloc on
            individual pages is a transient resource issue, not a fatal error.
    """
    from docling.datamodel.base_models import ConversionStatus  # noqa: PLC0415
    result = _get_converter().convert(str(file_path), raises_on_error=False)
    if result.status == ConversionStatus.PARTIAL_SUCCESS:
        log.warning(
            "docling.partial_success",
            file_path=str(file_path),
            error_count=len(result.errors),
        )
        return result.document
    if result.status != ConversionStatus.SUCCESS:
        messages = []
        for err in result.errors:
            log.error("docling.conversion_error", message=err.error_message)
            messages.append(err.error_message)
        raise RuntimeError(
            f"Docling conversion failed for {file_path}: {'; '.join(messages)}"
        )
    return result.document


def parse_document_from_bytes(content: bytes, filename: str) -> object:
    """Convert URL-fetched document bytes to a DoclingDocument.

    Wraps content in a DocumentStream so Docling can infer the format from the
    filename extension rather than from a filesystem path.

    Args:
        content:  Raw bytes of the document (e.g. from httpx.get(...).content).
        filename: Name hint used by Docling to determine file format (e.g. the
                  source URL path or a filename with extension).

    Returns:
        DoclingDocument on success or partial success (typed as object — runtime use only).

    Raises:
        RuntimeError: If Docling reports ConversionStatus.FAILURE (hard failure).
            PARTIAL_SUCCESS is accepted with a warning.
    """
    from docling.datamodel.base_models import ConversionStatus, DocumentStream  # noqa: PLC0415
    stream = DocumentStream(name=filename, stream=BytesIO(content))
    result = _get_converter().convert(stream, raises_on_error=False)
    if result.status == ConversionStatus.PARTIAL_SUCCESS:
        log.warning(
            "docling.partial_success",
            filename=filename,
            error_count=len(result.errors),
        )
        return result.document
    if result.status != ConversionStatus.SUCCESS:
        messages = []
        for err in result.errors:
            log.error("docling.conversion_error", message=err.error_message)
            messages.append(err.error_message)
        raise RuntimeError(
            f"Docling conversion failed for {filename}: {'; '.join(messages)}"
        )
    return result.document
