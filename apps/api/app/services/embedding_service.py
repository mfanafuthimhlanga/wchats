"""
embedding_service — Voyage AI batch embedding with pinned voyage-3 model.

Model pinning decision:
    EMBEDDING_MODEL = "voyage-3" is pinned permanently. DO NOT change to
    any floating alias (PITFALLS.md §3). The tenant DB schema
    stores embeddings in a VECTOR(1024) column — voyage-3 produces exactly
    1024-dimensional vectors. If the model were changed to a different dimension,
    all existing embeddings would become incompatible and retrieval would silently
    return wrong results until schema migration.

128-item batch limit:
    The Voyage API hard limit is 128 items per embed() request (RESEARCH.md §7).
    BATCH_SIZE = 128 is the maximum allowed. Sending more than 128 items in a
    single request raises a Voyage API error. The embed_chunks() function splits
    any input list into batches of exactly BATCH_SIZE before calling the API.

Count-mismatch guard:
    After collecting all batches, embed_chunks() asserts
    len(all_embeddings) == len(texts). If the Voyage API returns a different
    number of vectors than requested (a contract violation), RuntimeError is raised
    immediately so the caller (embed_and_migrate task) can fail cleanly rather than
    silently writing mismatched chunk_id ↔ vector pairs.

Tenacity retry:
    _embed_batch wraps the Voyage call in a retry decorator. The Voyage SDK
    exception hierarchy is not exhaustively documented; tenacity is configured
    to retry on any Exception. Authentication errors will burn 5 retries and then
    fail the task — this is acceptable for M2 since misconfiguration should be
    loud. M3 may narrow to specific transient errors.
"""

import structlog
import voyageai
from tenacity import retry, wait_exponential, stop_after_attempt

log = structlog.get_logger(__name__)

# Voyage API hard limit per request (RESEARCH.md §7)
BATCH_SIZE = 128

# PINNED — DO NOT change to voyage-latest (PITFALLS.md §3).
# Schema embeddings.vector is VECTOR(1024) which matches voyage-3.
# Changing this constant requires a schema migration and full re-embedding.
EMBEDDING_MODEL = "voyage-3"

# Module-level client — reads VOYAGE_API_KEY from env at import time.
# Fail-fast at import: misconfigured workers crash at startup, not on first task.
_vo = voyageai.Client()


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
)
def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed one batch of up to BATCH_SIZE texts.

    Retries on any voyageai exception — Voyage SDK exception hierarchy
    is not exhaustively documented; tenacity defaults to "retry on any
    Exception". For the purposes of M2 this is acceptable: authentication
    errors will burn 5 retries and then fail the task, which is
    observable in logs. M3 may narrow this.

    Args:
        texts: List of text strings, len(texts) <= BATCH_SIZE.

    Returns:
        List of 1024-dimensional float vectors, one per input text.
    """
    result = _vo.embed(texts, model=EMBEDDING_MODEL, input_type="document")
    return result.embeddings


def embed_chunks(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts in 128-item batches.

    Splits texts into BATCH_SIZE-item batches, calls _embed_batch for each,
    and concatenates the results. The count-mismatch guard at the end ensures
    len(return_value) == len(texts) always — any API contract violation is
    caught here rather than propagating as a silent mismatch.

    Args:
        texts: List of pre-sanitized chunk text strings.

    Returns:
        List of 1024-dimensional float vectors, same order as input texts.
        len(return_value) == len(texts) always.

    Raises:
        RuntimeError: If the total number of embeddings returned does not
                      match len(texts). This indicates a Voyage API contract
                      violation and must not be silently ignored.
    """
    if not texts:
        return []

    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        log.debug("embedding_service.batch", batch_start=i, batch_size=len(batch))
        all_embeddings.extend(_embed_batch(batch))

    if len(all_embeddings) != len(texts):
        raise RuntimeError(
            f"embedding count mismatch: got {len(all_embeddings)}, expected {len(texts)}"
        )

    return all_embeddings
