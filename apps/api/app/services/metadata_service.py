"""
metadata_service — structured-output metadata + entity extraction.

One model call returning summary + keywords + questions + entities via
client.chat.completions.parse(response_format=ChunkMetadataAndEntities). Entity
extraction runs in the same call as metadata enrichment — NOT a separate request
(CONTEXT.md non-negotiable: same API call, single cost unit).

Tenacity retry contract:
    Retries ONLY on the provider's rate-limit and timeout errors, which
    `app.core.model_client.TRANSIENT_ERRORS` names for both SDKs.
    Authentication errors, validation errors, and content-policy errors are fatal —
    do not retry (same wall, burns budget).

Client construction:
    Per call, through `app.core.model_client` (ticket #47), so every enrichment
    leaves a `model_calls` row under the `metadata_enrichment` purpose and the
    api key is resolved from Settings rather than from `os.environ`. That last
    part is why the module-level client existed. A Celery worker started without
    inheriting `.env` has the key nowhere else. `resolve_credentials` reads
    Settings, so the gap is closed in one place for every site instead of here.

Threat mitigations (T-02-04):
    T-02-04-01: The api key is never in task args. The factory resolves it
                from Settings at construction.
    T-02-04-02: client.chat.completions.parse(response_format=...) enforces Pydantic
                schema validation; malformed responses raise ValidationError.
                Literal["product","person","place","policy","process"] prevents
                arbitrary entity type injection.
    T-02-04-04: retry_if_exception_type restricts retries to transient errors only;
                wait_exponential(min=2, max=30) and stop_after_attempt(5) cap
                total retries and backoff ceiling.
    T-02-04-05: The system prompt explicitly instructs "Return entities only when
                explicit; do not invent" — guides the model against in-content
                injections.
    T-02-04-06: log calls reference chunk_id and document_id ONLY — never content.
"""

from typing import Literal

import structlog
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.model_client import TRANSIENT_ERRORS, LedgerContext, route_for
from app.services.tool_loop import first_choice

log = structlog.get_logger(__name__)

#: The routing-table key both enrichment calls bill under. `route_for(PURPOSE)`
#: carries the model, so no alias is pinned here: a second literal is how an
#: enrichment ends up billed under one model and served by another.
PURPOSE = "metadata_enrichment"

# System prompt for structured metadata + entity extraction
METADATA_SYSTEM_PROMPT = (
    "You are a metadata extractor for a RAG system. For the given text chunk produce: "
    "(1) a 1-2 sentence summary, "
    "(2) 5-10 keywords as noun phrases with no stop words, "
    "(3) 3-5 hypothetical questions a user might ask that this chunk would answer, "
    "and (4) all named entities present, classified as exactly one of: "
    "product, person, place, policy, process. "
    "For each entity, return: name (raw form in text), type, normalized (lowercase canonical form). "
    "Return entities only when explicit; do not invent."
)


class EntityExtraction(BaseModel):
    """A single named entity extracted from chunk text.

    Fields:
        name:       Raw form of the entity as it appears in the text.
        type:       One of five canonical entity types (Literal — enforced by Pydantic).
        normalized: Lowercase canonical form used for cross-chunk deduplication.
                    Mapped to UNIQUE(normalized, type) in the entities table.
    """

    name: str
    type: Literal["product", "person", "place", "policy", "process"]
    normalized: str


class ChunkMetadataAndEntities(BaseModel):
    """Structured output returned by a single enrichment call.

    All four fields are returned in one API call — never split into separate requests
    (CONTEXT.md non-negotiable). entity extraction costs nothing extra because the
    model processes the text once.

    Fields:
        summary:   1-2 sentence summary of the chunk.
        keywords:  5-10 noun-phrase keywords without stop words.
        questions: 3-5 hypothetical questions this chunk answers.
        entities:  Named entities extracted and classified by type.
    """

    summary: str
    keywords: list[str]
    questions: list[str]
    entities: list[EntityExtraction]


def _why(choice) -> str:
    """The model's own reason for an empty `parsed`, or nothing when it gave none.

    `chat.completions.parse` leaves `parsed` at None for two different events. The
    model declined, and it wrote the reason into `message.refusal`; or something
    else went wrong and there is no reason to read. That refusal string is the
    only record the body carries, so an error raised over the top of it turns a
    content-policy refusal, which is a prompt or a corpus problem, into an
    unexplained empty result, which reads as a provider problem. The chunk is then
    re-enriched on the next run and refused again.
    """
    refusal = None if choice is None else getattr(choice.message, "refusal", None)
    return f": the model refused, saying {refusal!r}" if refusal else ""


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(TRANSIENT_ERRORS),
)
def enrich_chunk(text: str, ledger: LedgerContext) -> ChunkMetadataAndEntities:
    """One model call returning summary + keywords + questions + entities.

    Uses client.chat.completions.parse() with a Pydantic response_format for
    validated structured output. The
    Literal["product","person","place","policy","process"] type constraint on
    EntityExtraction prevents arbitrary entity type injection (T-02-04-02).

    Tenacity retries ONLY on rate-limit/timeout. Authentication errors,
    validation errors, and content-policy errors are fatal — do not retry
    (we'd just burn budget hitting the same wall).

    Args:
        text: Chunk content (sanitized by sanitize_chunk_text before storage —
              Wave 3 precondition; prompt injection markers already stripped).
        ledger: the ids this call is billed to and where its row goes.

    Returns:
        ChunkMetadataAndEntities with summary, keywords, questions, and entities.

    Raises:
        RateLimitError: Re-raised after max retries exhausted.
        APITimeoutError: Re-raised after max retries exhausted.
        AuthenticationError: Fatal. Raised immediately, with no retry.
        pydantic.ValidationError: Fatal — the response does not match the schema.
    """
    result = ledger.client(PURPOSE).chat.completions.parse(
        model=route_for(PURPOSE).model,
        messages=[
            {"role": "system", "content": METADATA_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        # 512 for summary/keywords/questions + room for entity list
        max_completion_tokens=1024,
        response_format=ChunkMetadataAndEntities,
    )
    choice = first_choice(result)
    parsed = None if choice is None else choice.message.parsed
    if parsed is None:
        raise ValueError(f"Metadata extraction returned no parsed output{_why(choice)}")
    return parsed


# ---------------------------------------------------------------------------
# Batched extraction — multiple chunks per model call (cost reduction).
#
# enrich_chunk() makes one model call per chunk: 50 chunks = 50 calls (~$0.08/doc).
# enrich_chunks_batch() packs BATCH_SIZE chunks into a single call: 50 chunks =
# 5 calls (~$0.008/doc), a ~10x cost reduction at the same per-chunk quality.
#
# Both coexist: enrich_chunk() remains the fallback and is exercised by tests.
# ---------------------------------------------------------------------------

# Chunks packed into one model call. The max_completion_tokens budget scales with
# this value:
# ~400 tokens output per chunk × 10 = ~4000, hence max_tokens=4096 below.
BATCH_SIZE = 10


class BatchResult(BaseModel):
    """Structured output for a single batched model call.

    Wraps a list of per-chunk results returned in the SAME order the chunks were
    submitted. The task zips results back to chunk_ids by position — never by ID,
    since the model is not given chunk IDs.
    """

    chunks: list[ChunkMetadataAndEntities]


# System prompt for batched metadata + entity extraction. Mirrors
# METADATA_SYSTEM_PROMPT but instructs the model to process multiple
# index-tagged chunks and return per-chunk results in submission order.
BATCH_SYSTEM_PROMPT = (
    "You are a metadata extractor for a RAG system. "
    "You will receive multiple text chunks, each wrapped in <chunk index=\"N\"> tags. "
    "For EACH chunk produce: "
    "(1) a 1-2 sentence summary, "
    "(2) 5-10 keywords as noun phrases with no stop words, "
    "(3) 3-5 hypothetical questions a user might ask that this chunk would answer, "
    "and (4) all named entities present, classified as exactly one of: "
    "product, person, place, policy, process. "
    "For each entity, return: name (raw form in text), type, normalized (lowercase canonical form). "
    "Return entities only when explicit; do not invent. "
    "Return results for ALL chunks in the same order they were given."
)


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(TRANSIENT_ERRORS),
)
def enrich_chunks_batch(
    texts: list[str], ledger: LedgerContext
) -> list[ChunkMetadataAndEntities]:
    """One model call extracting metadata for up to BATCH_SIZE chunks at once.

    Chunks are numbered by position in the input list. Returns results in the
    same order — zip by index, not by ID.

    The same tenacity contract as enrich_chunk applies: retry ONLY on rate-limit
    and timeout; authentication errors and validation errors are fatal and raised
    immediately (T-02-04-04). The max_tokens budget (4096) scales with BATCH_SIZE
    to leave headroom for ~10 chunks of structured output.

    Args:
        texts: Chunk contents (sanitized by sanitize_chunk_text before storage).
               Up to BATCH_SIZE entries — callers slice the pending list.
        ledger: the ids this call is billed to and where its row goes.

    Returns:
        list[ChunkMetadataAndEntities] in the same order as `texts`.

    Raises:
        ValueError: Batch size mismatch — model returned a different count than sent.
        pydantic.ValidationError: Fatal — the response doesn't match the BatchResult schema.
        AuthenticationError: Fatal. Raised immediately, with no retry.
        RateLimitError: Re-raised after max retries exhausted.
        APITimeoutError: Re-raised after max retries exhausted.
    """
    prompt = "\n\n".join(
        f'<chunk index="{i}">\n{text}\n</chunk>'
        for i, text in enumerate(texts)
    )
    result = ledger.client(PURPOSE).chat.completions.parse(
        model=route_for(PURPOSE).model,
        messages=[
            {"role": "system", "content": BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        # ~80-400 tokens output per chunk × 10 chunks + headroom
        max_completion_tokens=4096,
        response_format=BatchResult,
    )
    choice = first_choice(result)
    batch = None if choice is None else choice.message.parsed
    if batch is None:
        raise ValueError(
            f"Batch metadata extraction returned no parsed output{_why(choice)}"
        )
    if len(batch.chunks) != len(texts):
        raise ValueError(
            f"Batch size mismatch: sent {len(texts)} chunks, got {len(batch.chunks)} results"
        )
    return batch.chunks
