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

    One ledger row per invoke_model call (#265). Titan takes one inputText per
        request, so a 500-chunk ingest makes 500 requests and leaves 500 rows,
        which is what keeps `calls` meaning provider requests in every rollup.
        The cost is one psycopg2 connection per row, the same cost every chat
        purpose already pays. A body carrying no `inputTextTokenCount` leaves no
        row at all rather than one claiming zero tokens.

    active_embedding_model(): Returns the effective model id for the current
        provider. Used to populate embeddings.model so the corpus records
        which model produced each vector.

Security (T-13-02-02): boto3 uses the IAM task role — no static AWS key in
    code or env. Only AWS_REGION is configured here; credentials come from the
    ECS task role at runtime.
"""

import json

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.model_client import LedgerContext
from app.services.embedding_ledger import BEDROCK_PROVIDER, purpose_for, record_embedding

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
def _invoke_one(text: str) -> dict:
    """Invoke Bedrock Titan v2 for a single input text string.

    Retries on any Exception (same policy as _embed_batch in embedding_service.py):
    transient network errors, throttling, and service unavailable responses all
    benefit from exponential backoff. Returns the parsed body without dimension
    validation. The guard lives in embed_texts().

    The whole body rather than the vector alone, because the ledger row needs
    `inputTextTokenCount` and it arrives on the same response (#265).

    Args:
        text: A single text string to embed.

    Returns:
        The parsed response body: `embedding` (length should equal EMBED_DIM) and
        `inputTextTokenCount`.
    """
    response = _get_bedrock().invoke_model(
        modelId=settings.BEDROCK_EMBED_MODEL_ID,
        body=json.dumps({
            "inputText": text,
            "dimensions": EMBED_DIM,
            "normalize": True,
        }),
    )
    return json.loads(response["body"].read())


def _invoke_and_record(text: str, purpose: str, ledger: LedgerContext) -> list[float]:
    """One text to one vector, leaving one ledger row for the request it made.

    The row is written BEFORE the dimension guard, because the request was made
    and paid for whether or not the vector is usable. Recording after the guard
    would drop the spend of exactly the call that went wrong.

    Raises:
        RuntimeError: Bedrock returned a vector whose length is not EMBED_DIM.
    """
    body = _invoke_one(text)
    record_embedding(
        ledger,
        purpose=purpose,
        provider=BEDROCK_PROVIDER,
        model=settings.BEDROCK_EMBED_MODEL_ID,
        input_tokens=body.get("inputTextTokenCount"),
    )
    vector = body["embedding"]
    if len(vector) != EMBED_DIM:
        raise RuntimeError(
            f"bedrock embedding dim mismatch: got {len(vector)}, expected {EMBED_DIM}"
        )
    return vector


def embed_texts(texts: list[str], input_type: str, ledger: LedgerContext) -> list[list[float]]:
    """Embed a list of text strings using Bedrock Titan Text Embeddings v2.

    Loops one Titan invoke_model call per text (Titan v2 accepts a single
    inputText per call, with no batching API). input_type picks the ledger purpose
    (`embed_query` or `embed_document`) and is logged at debug; Titan v2 has no
    document/query prompt distinction so it does not affect the call body.

    ONE LEDGER ROW PER invoke_model, NOT PER embed_texts CALL (#265). Each loop
    iteration is one provider request, and `calls` in every rollup counts provider
    requests for every other purpose, so a Titan row that covered a whole batch
    would make `calls` mean two different things in one table. Recording inside
    the loop also means the dimension guard below cannot lose spend: the texts
    already invoked are already recorded when it raises.

    Dimension guard: after each _invoke_one() call, asserts len(vector) == EMBED_DIM.
    If Bedrock returns an unexpected dimension (e.g., 512 from a model config
    mismatch), RuntimeError is raised before the vector is appended. This prevents
    silent space corruption in the VECTOR(1024) HNSW index.

    Args:
        texts:      List of text strings to embed.
        input_type: "document" or "query". Picks the ledger purpose.
        ledger:     who this spend is billed to and where each row goes. Required,
                    because a call that records nothing is issue #265.

    Returns:
        List of EMBED_DIM-dimensional float vectors, one per input text.
        Returns [] immediately for empty input (no Bedrock call made).

    Raises:
        RuntimeError: If Bedrock returns a vector with length != EMBED_DIM.
        ValueError:   input_type is neither "query" nor "document".
    """
    purpose = purpose_for(input_type)
    if not texts:
        return []

    log.debug(
        "bedrock_embedding_service.embed_texts",
        count=len(texts),
        input_type=input_type,
    )

    return [_invoke_and_record(text, purpose, ledger) for text in texts]


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
