"""
embedding_service — Batch embedding with provider seam (Bedrock primary, Voyage fallback).

Provider seam (PROD-06 / P13-02):
    EMBEDDING_PROVIDER env var selects the embedding backend:
      "bedrock" (default) → Amazon Bedrock Titan Text Embeddings v2 via
                            bedrock_embedding_service.embed_texts()
      "voyage"            → Voyage AI voyage-3 (legacy path, retained as fallback)

    Both embed_chunks() and embed_query() (retrieval_service) dispatch through
    this seam. Query and document vectors MUST come from the same provider
    (T-13-02-01 — mixed spaces break cosine similarity silently).

Model pinning decision (Voyage fallback path):
    EMBEDDING_MODEL = "voyage-3" is pinned permanently for the Voyage path.
    DO NOT change to any floating alias (PITFALLS.md §3). The tenant DB schema
    stores embeddings in a VECTOR(1024) column — voyage-3 produces exactly
    1024-dimensional vectors. If the model were changed to a different dimension,
    all existing embeddings would become incompatible and retrieval would silently
    return wrong results until schema migration.

128-item batch limit (Voyage path):
    The Voyage API hard limit is 128 items per embed() request (RESEARCH.md §7).
    BATCH_SIZE = 128 is the maximum allowed. Sending more than 128 items in a
    single request raises a Voyage API error. The embed_chunks() function splits
    any input list into batches of exactly BATCH_SIZE before calling the API.
    (Bedrock Titan v2 loops per-text internally in bedrock_embedding_service.)

Count-mismatch guard:
    After collecting all embeddings, embed_chunks() asserts
    len(all_embeddings) == len(texts). If the provider returns a different
    number of vectors than requested (a contract violation), RuntimeError is raised
    immediately so the caller (embed_and_migrate task) can fail cleanly rather than
    silently writing mismatched chunk_id ↔ vector pairs.

Tenacity retry:
    _embed_batch wraps the Voyage call in a retry decorator. The Voyage SDK
    exception hierarchy is not exhaustively documented; tenacity is configured
    to retry on any Exception. Authentication errors will burn 5 retries and then
    fail the task — this is acceptable for M2 since misconfiguration should be
    loud. M3 may narrow to specific transient errors.

Lazy import rationale:
    voyageai.__init__ conditionally runs `import aiohttp` when pkg_resources is
    absent from sys.modules (a gunicorn workaround). In python:3.12-slim without
    setuptools, aiohttp hangs indefinitely in a multiprocessing-spawned subprocess
    (uvicorn --reload worker). Moving the import inside _get_vo() means the API
    container never triggers that path. The guard below ensures pkg_resources is
    in sys.modules before voyageai is imported, so the aiohttp path is skipped
    regardless of the calling environment.

    bedrock_embedding_service is also imported lazily inside embed_chunks() so
    this module remains importable in unit tests without boto3 or real AWS creds.
"""

import sys

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings

log = structlog.get_logger(__name__)

# Voyage API hard limit per request (RESEARCH.md §7)
BATCH_SIZE = 128

# PINNED — DO NOT change to voyage-latest (PITFALLS.md §3).
# Schema embeddings.vector is VECTOR(1024) which matches voyage-3.
# Changing this constant requires a schema migration and full re-embedding.
EMBEDDING_MODEL = "voyage-3"

# Lazily initialized — see _get_vo() below.
_vo = None


def _get_vo():
    """Return the module-level voyageai client, initializing on first call.

    Lazy init avoids importing voyageai at module level. voyageai.__init__ runs
    `import aiohttp` when pkg_resources is absent from sys.modules; aiohttp hangs
    indefinitely in a spawned subprocess (uvicorn --reload) on python:3.12-slim.
    Pre-loading pkg_resources prevents that code path.
    """
    global _vo
    if _vo is None:
        # Ensure pkg_resources is present so voyageai.__init__ skips the
        # `import aiohttp` fallback (https://github.com/benoitc/gunicorn/pull/2539).
        if "pkg_resources" not in sys.modules:
            try:
                import pkg_resources  # noqa: F401
            except ImportError:
                # python:3.12-slim may not have setuptools; inject a stub that
                # satisfies the isinstance check voyageai performs.
                sys.modules["pkg_resources"] = type(sys)("pkg_resources")  # type: ignore[assignment]
        import voyageai as _voyageai  # noqa: PLC0415
        # Explicit api_key bypasses the os.environ gap when Celery is started
        # without inheriting the .env file (e.g., via Start-Process on Windows).
        # pydantic Settings loads .env via _find_env_file(); os.environ is not
        # populated, so voyageai.Client()'s default os.environ['VOYAGE_API_KEY']
        # lookup fails with AuthenticationError. Mirrors metadata_service.py.
        _vo = _voyageai.Client(api_key=settings.VOYAGE_API_KEY)
    return _vo


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
    result = _get_vo().embed(texts, model=EMBEDDING_MODEL, input_type="document")
    return result.embeddings


def embed_chunks(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts using the configured EMBEDDING_PROVIDER.

    When EMBEDDING_PROVIDER=bedrock (default): delegates to
    bedrock_embedding_service.embed_texts(texts, "document"). Bedrock Titan v2
    loops one call per text internally; no 128-item batching needed.

    When EMBEDDING_PROVIDER=voyage (fallback): splits texts into BATCH_SIZE-item
    batches, calls _embed_batch for each, and concatenates the results.

    The count-mismatch guard at the end ensures len(return_value) == len(texts)
    always — any provider contract violation is caught here rather than
    propagating as a silent chunk_id ↔ vector mismatch.

    Args:
        texts: List of pre-sanitized chunk text strings.

    Returns:
        List of 1024-dimensional float vectors, same order as input texts.
        len(return_value) == len(texts) always.

    Raises:
        RuntimeError: If the total number of embeddings returned does not
                      match len(texts). This indicates a provider contract
                      violation and must not be silently ignored.
    """
    if not texts:
        return []

    if settings.EMBEDDING_PROVIDER == "bedrock":
        # Lazy import — keeps this module importable without boto3/AWS creds
        import app.services.bedrock_embedding_service as _bedrock_svc  # noqa: PLC0415
        all_embeddings = _bedrock_svc.embed_texts(texts, "document")
    else:
        # Voyage fallback path (unchanged from M2)
        all_embeddings = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            log.debug("embedding_service.batch", batch_start=i, batch_size=len(batch))
            all_embeddings.extend(_embed_batch(batch))

    if len(all_embeddings) != len(texts):
        raise RuntimeError(
            f"embedding count mismatch: got {len(all_embeddings)}, expected {len(texts)}"
        )

    return all_embeddings
