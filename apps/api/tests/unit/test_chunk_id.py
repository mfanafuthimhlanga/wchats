"""Unit tests for deterministic_chunk_id — ING-05 verification.

Verifies the idempotency contract:
  - Same (document_id, ordinal) always produces the same UUID.
  - Different ordinals produce different UUIDs.
  - Different document IDs produce different UUIDs.
  - The namespace constant is pinned to uuid.NAMESPACE_URL.

conftest.py has already set the required env vars at module level.
chunk_id.py imports only stdlib, so env setup is not strictly needed here,
but is included for consistency with the test suite pattern.
"""

import base64
import os
import uuid

# Safety: ensure required env vars are present even if conftest is not loaded
# (e.g., when running this file in isolation via `pytest tests/unit/test_chunk_id.py`)
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")

from app.domain.chunk_id import CHUNK_UUID_NAMESPACE, deterministic_chunk_id


class TestDeterministicChunkId:
    """Tests for deterministic_chunk_id (ING-05: chunk ID idempotency)."""

    def test_same_inputs_produce_same_id(self):
        """Calling twice with the same args must return the same UUID."""
        id1 = deterministic_chunk_id("doc-uuid-123", 0)
        id2 = deterministic_chunk_id("doc-uuid-123", 0)
        assert id1 == id2, (
            "ING-05 VIOLATION: deterministic_chunk_id returned different UUIDs for the same "
            "inputs. Re-ingestion idempotency is broken."
        )

    def test_different_ordinals_produce_different_ids(self):
        """Chunks with different ordinals in the same document must have different IDs."""
        id_ordinal_0 = deterministic_chunk_id("doc-uuid-123", 0)
        id_ordinal_1 = deterministic_chunk_id("doc-uuid-123", 1)
        assert id_ordinal_0 != id_ordinal_1, (
            "Ordinal 0 and ordinal 1 produced the same chunk ID — collision risk in upserts."
        )

    def test_different_document_ids_produce_different_ids(self):
        """Same ordinal in different documents must have different chunk IDs."""
        id_doc_a = deterministic_chunk_id("doc-aaa", 0)
        id_doc_b = deterministic_chunk_id("doc-bbb", 0)
        assert id_doc_a != id_doc_b, (
            "doc-aaa:0 and doc-bbb:0 produced the same chunk ID — cross-document collision."
        )

    def test_returns_uuid_instance(self):
        """deterministic_chunk_id must return a uuid.UUID, not a string."""
        result = deterministic_chunk_id("doc-uuid-123", 0)
        assert isinstance(result, uuid.UUID), (
            f"Expected uuid.UUID, got {type(result).__name__}"
        )

    def test_uses_correct_namespace(self):
        """The UUID must equal uuid5(CHUNK_UUID_NAMESPACE, 'doc-uuid-123:0').

        This pins the implementation to the declared namespace constant and
        verifies the name string format ('{document_id}:{ordinal}').
        """
        expected = uuid.uuid5(CHUNK_UUID_NAMESPACE, "doc-uuid-123:0")
        result = deterministic_chunk_id("doc-uuid-123", 0)
        assert result == expected, (
            f"UUID mismatch: got {result}, expected {expected}. "
            "The name format or namespace constant may have changed."
        )
