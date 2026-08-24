"""Deterministic chunk UUID utility — ING-05 idempotency contract.

Every chunk in the W Chats ingestion pipeline is identified by a UUID derived
deterministically from (document_id, ordinal). This ensures that:

  1. Re-ingesting the same document with the same content produces the same chunk
     UUIDs → the subsequent INSERT becomes an ON CONFLICT DO UPDATE (upsert), not a
     duplicate INSERT.
  2. After a task retry (Celery acks_late=True), the same chunk IDs are generated,
     so in-flight writes are idempotent.

Security note (T-02-01-02 — Tampering):
  CHUNK_UUID_NAMESPACE is a module-level constant holding
  6ba7b810-9dad-11d1-80b4-00c04fd430c8, which is `uuid.NAMESPACE_DNS`.
  Changing this constant across deployments would orphan every existing chunk ID
  stored in tenant DBs — all downstream reads (retrieval, evals, red team) would
  silently break. NEVER rotate or modify CHUNK_UUID_NAMESPACE after first deploy.

  These three lines said NAMESPACE_URL until 2026-08-24, when the literal UUIDs
  in tests/unit/test_chunk_type.py measured the value for the first time. Only
  the prose was wrong. Correcting the VALUE to NAMESPACE_URL
  (6ba7b811-9dad-11d1-80b4-00c04fd430c8) rekeys every chunk row that exists,
  which is what the paragraph above forbids.

PITFALLS.md §8 — Why Not uuid4:
  uuid4() is random per call. On task retry a new random UUID would be generated,
  creating a duplicate chunk row instead of safely upserting the existing one.
  uuid5 is the only correct choice here.
"""

import uuid

# Namespace for W Chats chunk IDs. This value is uuid.NAMESPACE_DNS, one of the
# RFC 4122 well-known namespaces. It is written out rather than imported so the
# bytes are visible at the point a reader would think about changing them.
# VALUE MUST NEVER CHANGE ACROSS DEPLOYMENTS — see module docstring above.
CHUNK_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def deterministic_chunk_id(document_id: str, ordinal: int) -> uuid.UUID:
    """Derive a stable UUID from document_id and chunk ordinal.

    Same document_id + same ordinal → same UUID every time, across processes,
    across task retries, and across deployments. Safe to use as an upsert key.

    Args:
        document_id: String representation of the document UUID (the primary key
                     from the tenant ``documents`` table).
        ordinal:     Zero-based position of this chunk within the document.
                     The ordinal sequence MUST be deterministic for a given document
                     parse — same document parse → same ordinal sequence → same IDs.

    Returns:
        A uuid.UUID derived via uuid5(CHUNK_UUID_NAMESPACE, "{document_id}:{ordinal}").

    Example:
        >>> chunk_id = deterministic_chunk_id("550e8400-e29b-41d4-a716-446655440000", 0)
        >>> # Re-running with the same args always returns the same value
        >>> assert chunk_id == deterministic_chunk_id("550e8400-e29b-41d4-a716-446655440000", 0)
    """
    name = f"{document_id}:{ordinal}"
    return uuid.uuid5(CHUNK_UUID_NAMESPACE, name)
