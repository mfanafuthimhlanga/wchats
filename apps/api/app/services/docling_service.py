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
  deferred. RuntimeError on ConversionStatus != SUCCESS prevents a single bad PDF
  from breaking the chain.
"""

import structlog
from io import BytesIO
from pathlib import Path

from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import DocumentStream, ConversionStatus

log = structlog.get_logger(__name__)

# Module-level init — DocumentConverter loads DocLayNet + TableFormer ML models
# (~1-2GB RAM). Must be initialized ONCE per worker process, not inside the task
# function. Mirrors the _redis = redis_lib.from_url(...) pattern from provision.py:
# each Celery worker process creates exactly one converter; no cross-process sharing.
# Initialising inside the task would pay a ~10-15s load penalty on every task call.
_converter = DocumentConverter()


def parse_document(file_path: Path) -> object:
    """Convert a local document file to a DoclingDocument.

    Args:
        file_path: Path to the document file (PDF, image).

    Returns:
        DoclingDocument on success (typed as object to avoid importing
        DoclingDocument at module level — runtime use only).

    Raises:
        RuntimeError: If Docling reports ConversionStatus != SUCCESS. The error
            message includes all conversion errors from result.errors.
    """
    result = _converter.convert(str(file_path), raises_on_error=False)
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
        DoclingDocument on success (typed as object — runtime use only).

    Raises:
        RuntimeError: If Docling reports ConversionStatus != SUCCESS.
    """
    stream = DocumentStream(name=filename, stream=BytesIO(content))
    result = _converter.convert(stream, raises_on_error=False)
    if result.status != ConversionStatus.SUCCESS:
        messages = []
        for err in result.errors:
            log.error("docling.conversion_error", message=err.error_message)
            messages.append(err.error_message)
        raise RuntimeError(
            f"Docling conversion failed for {filename}: {'; '.join(messages)}"
        )
    return result.document
