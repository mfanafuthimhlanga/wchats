"""
bedrock_embedding_service — Amazon Bedrock Titan Text Embeddings v2 client.

PROD-06 provider seam: moves BOTH the document-embedding path (embed_chunks
via embedding_service) AND the query-embedding path (embed_query via
retrieval_service) from Voyage AI to Amazon Bedrock Titan Text Embeddings v2
when EMBEDDING_PROVIDER=bedrock.

Design decisions:
    _get_bedrock(): Lazy boto3 client — mirrors _get_vo() in embedding_service.py.
        Late import avoids loading boto3 at module level so the module can be
        imported in unit tests without real AWS credentials.

    EMBED_DIM = 1024: Hard constant matching the embeddings.vector VECTOR(1024)
        schema. Titan v2 is configurable at 256/512/1024 — we always request 1024.

    embed_texts(): Loops one Titan call per text. Titan v2 accepts a single
        inputText per call (unlike Cohere Embed v3 which batches up to 96).
        input_type is accepted for interface parity with the Voyage/Cohere seam
        but has no effect on the Titan call body (Titan v2 has no document/query
        prompt distinction).

    Dimension guard: After calling _invoke_one(), embed_texts asserts
        len(vector) == EMBED_DIM. If Bedrock returns a shorter/longer vector
        (e.g., 512 from a misconfigured call), RuntimeError is raised immediately
        so the caller cannot silently write a mismatched vector into VECTOR(1024).

    Tenacity retry: _invoke_one is wrapped with the same retry pattern as
        _embed_batch in embedding_service.py (retry on any Exception, 5 attempts,
        exponential backoff 2–30s). The dimension guard is in embed_texts
        (outside the retry loop) since a consistent dim mismatch is a
        configuration error, not a transient API error.

    active_embedding_model(): Returns the effective model id for the current
        provider. Used to populate embeddings.model so the corpus records
        which model produced each vector.

Security (T-13-02-02): boto3 uses the IAM task role — no static AWS key in
    code or env. Only AWS_REGION is configured here; credentials come from the
    ECS task role at runtime.
"""

import json

import structlog
from tenacity import retry, wait_exponential, stop_after_attempt

from app.core.config import settings

log = structlog.get_logger(__name__)

# Hard constant — must match embeddings.vector VECTOR(1024) column.
# Any Bedrock response with len(embedding) != EMBED_DIM triggers a RuntimeError
# so a silent space mismatch cannot propagate into the HNSW index.
EMBED_DIM = 1024

# Lazily initialized boto3 bedrock-runtime client — see _get_bedrock() below.
_bedrock = None


def _get_bedrock():
    """Return the module-level boto3 bedrock-runtime client, initializing on first call.

    Lazy init keeps this module importable in unit tests without real AWS
    credentials (boto3 is only imported on the first embed_texts call, not at
    module load time). Mirrors _get_vo() in embedding_service.py.
    """
    global _bedrock
    if _bedrock is None:
        import boto3  # noqa: PLC0415  — lazy import by design
        _bedrock = boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)
    return _bedrock


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
)
def _invoke_one(text: str) -> list[float]:
    """Invoke Bedrock Titan v2 for a single input text string.

    Retries on any Exception (same policy as _embed_batch in embedding_service.py):
    transient network errors, throttling, and service unavailable responses all
    benefit from exponential backoff. Returns the raw embedding vector without
    dimension validation — the guard lives in embed_texts().

    Args:
        text: A single text string to embed.

    Returns:
        Float vector as returned by Bedrock (length should equal EMBED_DIM).
    """
    response = _get_bedrock().invoke_model(
        modelId=settings.BEDROCK_EMBED_MODEL_ID,
        body=json.dumps({
            "inputText": text,
            "dimensions": EMBED_DIM,
            "normalize": True,
        }),
    )
    return json.loads(response["body"].read())["embedding"]


def embed_texts(texts: list[str], input_type: str) -> list[list[float]]:
    """Embed a list of text strings using Bedrock Titan Text Embeddings v2.

    Loops one Titan invoke_model call per text (Titan v2 accepts a single
    inputText per call — no batching API). input_type is accepted for interface
    parity with the Voyage/Cohere seam and logged at debug, but Titan v2 has no
    document/query prompt distinction so it does not affect the call body.

    Dimension guard: after each _invoke_one() call, asserts len(vector) == EMBED_DIM.
    If Bedrock returns an unexpected dimension (e.g., 512 from a model config
    mismatch), RuntimeError is raised before the vector is appended. This prevents
    silent space corruption in the VECTOR(1024) HNSW index.

    Args:
        texts:      List of text strings to embed.
        input_type: "document" or "query" — accepted for interface parity.

    Returns:
        List of EMBED_DIM-dimensional float vectors, one per input text.
        Returns [] immediately for empty input (no Bedrock call made).

    Raises:
        RuntimeError: If Bedrock returns a vector with length != EMBED_DIM.
    """
    if not texts:
        return []

    log.debug(
        "bedrock_embedding_service.embed_texts",
        count=len(texts),
        input_type=input_type,
    )

    result: list[list[float]] = []
    for text in texts:
        vector = _invoke_one(text)
        if len(vector) != EMBED_DIM:
            raise RuntimeError(
                f"bedrock embedding dim mismatch: got {len(vector)}, expected {EMBED_DIM}"
            )
        result.append(vector)
    return result


def active_embedding_model() -> str:
    """Return the embedding model id currently in use for the configured provider.

    When EMBEDDING_PROVIDER=bedrock, returns BEDROCK_EMBED_MODEL_ID so that
    embeddings.model records the correct producer (required for corpus
    consistency checks and audit after the PROD-06 re-embed backfill).

    Falls back to the Voyage model constant for the voyage provider.
    """
    if settings.EMBEDDING_PROVIDER == "bedrock":
        return settings.BEDROCK_EMBED_MODEL_ID
    # Lazy import avoids circular dependency:
    # embedding_service will lazily import bedrock_embedding_service in Task 2.
    from app.services.embedding_service import EMBEDDING_MODEL  # noqa: PLC0415
    return EMBEDDING_MODEL
