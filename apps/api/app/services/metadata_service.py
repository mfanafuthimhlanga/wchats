"""
metadata_service — Claude Haiku structured-output metadata + entity extraction.

Single Haiku call returning summary + keywords + questions + entities via
client.messages.parse(output_format=ChunkMetadataAndEntities). Entity extraction
runs in the same call as metadata enrichment — NOT a separate request (CONTEXT.md
non-negotiable: same API call, single cost unit).

Tenacity retry contract:
    Retries ONLY on anthropic.RateLimitError and anthropic.APITimeoutError.
    Authentication errors, validation errors, and content-policy errors are fatal —
    do not retry (same wall, burns budget).

Module-level client init:
    _anthropic = anthropic.Anthropic() reads ANTHROPIC_API_KEY from env at import
    time. Fail-fast at import is preferred so misconfigured workers crash immediately
    rather than silently failing on first real request.

Threat mitigations (T-02-04):
    T-02-04-01: ANTHROPIC_API_KEY never in task args — read from env at module init.
    T-02-04-02: client.messages.parse(output_format=...) enforces Pydantic schema
                validation; malformed responses raise ValidationError.
                Literal["product","person","place","policy","process"] prevents
                arbitrary entity type injection.
    T-02-04-04: retry_if_exception_type restricts retries to transient errors only;
                wait_exponential(min=2, max=30) and stop_after_attempt(5) cap
                total retries and backoff ceiling.
    T-02-04-05: The system prompt explicitly instructs "Return entities only when
                explicit; do not invent" — guides Haiku against in-content injections.
    T-02-04-06: log calls reference chunk_id and document_id ONLY — never content.
"""

import structlog
from typing import Literal

import anthropic
from pydantic import BaseModel
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from app.core.config import settings

log = structlog.get_logger(__name__)

# Pinned model string — verify via anthropic.Anthropic().models.list() before deploy
# (RESEARCH.md Open Question 2). Do NOT use "claude-haiku-latest" — embedding drift risk.
HAIKU_MODEL = "claude-haiku-4-5"

# Module-level client — explicit api_key bypasses os.environ gap when Celery
# is started without inheriting the .env file (e.g., via Start-Process on Windows).
# pydantic Settings loads .env via _find_env_file(); os.environ is not populated.
_anthropic = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

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
    """Structured output returned by a single Haiku enrichment call.

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


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APITimeoutError)),
)
def enrich_chunk(text: str) -> ChunkMetadataAndEntities:
    """Single Haiku call returning summary + keywords + questions + entities.

    Uses client.messages.parse() with Pydantic output_format for validated structured
    output. The Literal["product","person","place","policy","process"] type constraint
    on EntityExtraction prevents arbitrary entity type injection (T-02-04-02).

    Tenacity retries ONLY on rate-limit/timeout. Authentication errors,
    validation errors, and content-policy errors are fatal — do not retry
    (we'd just burn budget hitting the same wall).

    Args:
        text: Chunk content (sanitized by sanitize_chunk_text before storage —
              Wave 3 precondition; prompt injection markers already stripped).

    Returns:
        ChunkMetadataAndEntities with summary, keywords, questions, and entities.

    Raises:
        anthropic.RateLimitError: Re-raised after max retries exhausted.
        anthropic.APITimeoutError: Re-raised after max retries exhausted.
        anthropic.AuthenticationError: Fatal — raised immediately, no retry.
        pydantic.ValidationError: Fatal — Haiku response does not match schema.
    """
    result = _anthropic.messages.parse(
        model=HAIKU_MODEL,
        system=METADATA_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
        max_tokens=1024,  # 512 for summary/keywords/questions + room for entity list
        output_format=ChunkMetadataAndEntities,
    )
    return result.parsed_output


# ---------------------------------------------------------------------------
# Batched extraction — multiple chunks per Haiku call (cost reduction).
#
# enrich_chunk() makes one Haiku call per chunk: 50 chunks = 50 calls (~$0.08/doc).
# enrich_chunks_batch() packs BATCH_SIZE chunks into a single call: 50 chunks =
# 5 calls (~$0.008/doc), a ~10x cost reduction at the same per-chunk quality.
#
# Both coexist: enrich_chunk() remains the fallback and is exercised by tests.
# ---------------------------------------------------------------------------

# Chunks packed into one Haiku call. max_tokens budget scales with this value:
# ~400 tokens output per chunk × 10 = ~4000, hence max_tokens=4096 below.
BATCH_SIZE = 10


class BatchResult(BaseModel):
    """Structured output for a single batched Haiku call.

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
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APITimeoutError)),
)
def enrich_chunks_batch(texts: list[str]) -> list[ChunkMetadataAndEntities]:
    """Single Haiku call extracting metadata for up to BATCH_SIZE chunks at once.

    Chunks are numbered by position in the input list. Returns results in the
    same order — zip by index, not by ID.

    The same tenacity contract as enrich_chunk applies: retry ONLY on rate-limit
    and timeout; authentication errors and validation errors are fatal and raised
    immediately (T-02-04-04). The max_tokens budget (4096) scales with BATCH_SIZE
    to leave headroom for ~10 chunks of structured output.

    Args:
        texts: Chunk contents (sanitized by sanitize_chunk_text before storage).
               Up to BATCH_SIZE entries — callers slice the pending list.

    Returns:
        list[ChunkMetadataAndEntities] in the same order as `texts`.

    Raises:
        ValueError: Batch size mismatch — model returned a different count than sent.
        pydantic.ValidationError: Fatal — Haiku response doesn't match BatchResult schema.
        anthropic.AuthenticationError: Fatal — raised immediately, no retry.
        anthropic.RateLimitError: Re-raised after max retries exhausted.
        anthropic.APITimeoutError: Re-raised after max retries exhausted.
    """
    prompt = "\n\n".join(
        f'<chunk index="{i}">\n{text}\n</chunk>'
        for i, text in enumerate(texts)
    )
    result = _anthropic.messages.parse(
        model=HAIKU_MODEL,
        system=BATCH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,  # ~80-400 tokens output per chunk × 10 chunks + headroom
        output_format=BatchResult,
    )
    batch = result.parsed_output
    if len(batch.chunks) != len(texts):
        raise ValueError(
            f"Batch size mismatch: sent {len(texts)} chunks, got {len(batch.chunks)} results"
        )
    return batch.chunks
