"""
retrieval_service — Core retrieval primitives for M3 Hybrid Retrieval.

Implements:
  - RetrievalStrategy: Pydantic config model (fetched from agents.retrieval_strategy JSONB)
  - embed_query: Voyage query embedding (input_type="query" — critical distinction from "document")
  - vector_search: pgvector HNSW cosine search against tenant embeddings
  - bm25_search: native tsvector + ts_rank_cd only (deprecated Neon extensions not used)
  - rrf_fuse: Single SQL CTE with FULL OUTER JOIN + RRF formula (k=60 hardcoded SQL literal)
  - rerank: Voyage rerank-2 primary; Cohere rerank-english-v3.0 fallback
  - build_trace: Assemble truncated candidate trace for query.complete payload

What every search here returns (ticket #44, issue #7):
  RetrievedContext. Four engines run in this module and each names its number
  differently: cosine_score, bm25_score, rrf_score, rerank_score, with
  vector_rank and bm25_rank alongside. Those names stay inside these functions
  and inside the SQL. A caller receives one score and one rank per chunk, under
  the `strategy` that says which engine ranked them.

Design decisions:
  - psycopg2 connections use try/finally/close pattern (NOT context manager `with`) to match
    embed.py precedent and avoid implicit transaction wrapping on the connection object.
  - k=60 is a SQL literal, NOT a parameter — locked per CONTEXT.md.
  - Cohere import is lazy (inside _cohere_rerank body) — cohere is a fallback dependency only.
  - rrf_fuse returns an RrfFusion, one RetrievedContext under each of three named
    fields (fused, vector_candidates, bm25_candidates), so the retrieve_and_rank
    task traces all three without re-querying.

Uses only native Postgres tsvector + ts_rank_cd for BM25 (no deprecated Neon extensions).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

import psycopg2
import structlog
from pydantic import BaseModel, ConfigDict

from app.core.config import settings
from app.domain.retrieved_context import RetrievedChunk, RetrievedContext
from app.services.embedding_service import _get_vo

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Retrieval strategy model
# ---------------------------------------------------------------------------

class RetrievalStrategy(BaseModel):
    """Per-tenant retrieval strategy stored as JSONB on agents.retrieval_strategy.

    All fields are optional with sensible defaults. Parsed at task runtime via
    RetrievalStrategy.model_validate(agent.retrieval_strategy or {}).
    Unknown fields are silently ignored (extra="ignore") to allow forward-compat
    additions without breaking existing tasks.
    """

    model_config = ConfigDict(extra="ignore")

    vector_k: int = 20           # candidates from HNSW search
    bm25_k: int = 20             # candidates from BM25 ts_rank_cd search
    final_k: int = 5             # results after rerank
    rerank_threshold: float = 0.0  # minimum rerank score to include (0.0 = include all)
    query_expansion: bool = False
    metadata_filters: list[dict] = []  # entity-based filters (M4+); empty in M3


# ---------------------------------------------------------------------------
# Query embedding
# ---------------------------------------------------------------------------

def embed_query(query_text: str) -> list[float]:
    """Embed a user query using the configured EMBEDDING_PROVIDER.

    When EMBEDDING_PROVIDER=bedrock (default): delegates to
    bedrock_embedding_service.embed_texts([query_text], "query")[0].
    Titan v2 has no document/query distinction but input_type is passed for
    interface parity and future provider flexibility.

    When EMBEDDING_PROVIDER=voyage (fallback): calls Voyage with
    input_type="query" (CRITICAL — the Voyage model prepends a different prompt
    for "query" vs "document"; using "document" here silently degrades retrieval).

    INVARIANT: The provider used here MUST match the provider used in
    embed_chunks() (embedding_service). Mixed-space vectors make cosine
    similarity meaningless (T-13-02-01).

    Args:
        query_text: The raw user query string.

    Returns:
        1024-dimensional float vector (matches embeddings.vector VECTOR(1024) column).
    """
    if settings.EMBEDDING_PROVIDER == "bedrock":
        # Lazy import — keeps this module importable without boto3/AWS creds
        import app.services.bedrock_embedding_service as _bedrock_svc  # noqa: PLC0415
        return _bedrock_svc.embed_texts([query_text], "query")[0]
    return _get_vo().embed([query_text], model="voyage-3", input_type="query").embeddings[0]


# ---------------------------------------------------------------------------
# verified_qa cache lookup (D-24: BEFORE hybrid search)
# ---------------------------------------------------------------------------

def verified_qa_lookup(
    conn_str: str,
    query_vector: list[float],
    threshold: float,
) -> Optional[dict]:
    """Check verified_qa for a cached answer matching the query via cosine similarity.

    Called BEFORE hybrid search (D-24). A hit updates last_used_at and use_count
    (D-26) and returns a dict. A miss returns None (D-27). A dict rather than a
    RetrievedContext, because a hit is one verified answer a human already
    approved, not a ranking of passages. Connections follow the psycopg2
    try/finally/close pattern, matching vector_search and bm25_search here.

    Args:
        conn_str:     Decrypted tenant DB connection string.
        query_vector: 1024-dim float vector from embed_query() (input_type="query").
        threshold:    Cosine similarity threshold (settings.VERIFIED_QA_HIT_THRESHOLD = 0.93).

    Returns:
        Dict with keys: answer (str), citations (list), similarity (float),
        source (str="verified_qa_cache") on cache hit; None on miss.
    """
    sql_lookup = """
        SELECT id, answer, citations,
               1 - (question_vector <=> %(qv)s::vector) AS similarity
        FROM verified_qa
        WHERE invalidated_at IS NULL
          AND 1 - (question_vector <=> %(qv)s::vector) >= %(threshold)s
        ORDER BY similarity DESC
        LIMIT 1
    """
    sql_update = """
        UPDATE verified_qa
        SET last_used_at = NOW(), use_count = use_count + 1
        WHERE id = %(row_id)s
    """

    conn = psycopg2.connect(conn_str)
    try:
        with conn.cursor() as cur:
            cur.execute(sql_lookup, {
                "qv": str(query_vector),
                "threshold": threshold,
            })
            row = cur.fetchone()
            if row is None:
                log.debug("verified_qa_lookup.miss")
                return None
            row_id, answer, citations, similarity = row
            cur.execute(sql_update, {"row_id": row_id})
        conn.commit()
    finally:
        conn.close()

    log.debug("verified_qa_lookup.hit", similarity=float(similarity))
    return {
        "answer": answer,
        "citations": citations,  # JSONB — psycopg2 returns as Python dict/list
        "similarity": float(similarity),
        "source": "verified_qa_cache",
    }


# ---------------------------------------------------------------------------
# Row readers
# ---------------------------------------------------------------------------

def _chunk_from_row(row) -> RetrievedChunk:
    """Read one candidate row: (chunk_id, content, document_id, score, rank).

    vector_search and bm25_search select those five columns in that order, so
    one reader serves both and the two cannot drift apart. The score column is
    read with `float(...)` and no fallback. Both queries compute it arithmetically
    over rows they matched, so a NULL there is a defect worth raising rather than
    a zero worth ranking.
    """
    return RetrievedChunk(
        chunk_id=str(row[0]),
        document_id=str(row[2]),
        content=row[1],
        score=float(row[3]),
        rank=int(row[4]),
    )


def _empty_context(query_text: str, strategy: str) -> RetrievedContext:
    """A context that ran and matched nothing, which is a whole answer."""
    return RetrievedContext(query=query_text, chunks=(), strategy=strategy)


def _fused_chunk(row, position: int) -> RetrievedChunk:
    """Read one fused CTE row at its position in the fused order.

    The CTE selects cosine_score, bm25_score, vector_rank and bm25_rank
    alongside the RRF score. Those four belong to the two engines that fed the
    fusion and are read off their own contexts, so the fused chunk carries the
    RRF score and where the fusion put it.
    """
    return RetrievedChunk(
        chunk_id=str(row[0]),
        document_id=str(row[2]),
        content=row[1],
        score=float(row[3]),
        rank=position,
    )


# ---------------------------------------------------------------------------
# Vector search (pgvector HNSW)
# ---------------------------------------------------------------------------

def vector_search(
    conn_str: str,
    query_vector: list[float],
    vector_k: int,
    query_text: str,
) -> RetrievedContext:
    """Execute pgvector HNSW cosine search against tenant embeddings.

    Uses the HNSW index `embeddings_vector_hnsw_idx` created in
    0001_tenant_v1_schema.py. The query_vector is stringified for psycopg2 →
    pgvector casting (same pattern as embed.py line 235 `str(vec)`).

    Args:
        conn_str: Decrypted tenant DB connection string.
        query_vector: 1024-dim float vector from embed_query().
        vector_k: Number of top candidates to return.
        query_text: The raw user query, carried onto the context. The vector
            alone cannot say what was asked, and every reader of the context
            needs the question the chunks answer.

    Returns:
        RetrievedContext with strategy "vector". Each chunk carries the cosine
        similarity as `score` and its HNSW position as `rank`.
    """
    log.debug("vector_search.start", vector_k=vector_k)

    sql = """
        SELECT e.chunk_id, c.content, c.document_id,
               1 - (e.vector <=> %(query_vector)s::vector) AS cosine_score,
               ROW_NUMBER() OVER (ORDER BY e.vector <=> %(query_vector)s::vector) AS rank
        FROM embeddings e JOIN chunks c ON c.id = e.chunk_id
        ORDER BY e.vector <=> %(query_vector)s::vector
        LIMIT %(vector_k)s
    """

    conn = psycopg2.connect(conn_str)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "query_vector": str(query_vector),
                "vector_k": vector_k,
            })
            rows = cur.fetchall()
    finally:
        conn.close()

    return RetrievedContext(
        query=query_text,
        chunks=tuple(_chunk_from_row(row) for row in rows),
        strategy="vector",
    )


# ---------------------------------------------------------------------------
# BM25 search (native tsvector + ts_rank_cd only — no deprecated Neon extensions)
# ---------------------------------------------------------------------------

def bm25_search(conn_str: str, query_text: str, bm25_k: int) -> RetrievedContext:
    """Execute native PostgreSQL BM25 search using tsvector + ts_rank_cd.

    Uses the GIN index `chunks_content_tsv_idx ON chunks USING GIN
    (to_tsvector('english', content))` created in 0001_tenant_v1_schema.py.

    Only native Postgres tsvector + ts_rank_cd is used. Deprecated Neon extensions
    are not used here.

    Args:
        conn_str: Decrypted tenant DB connection string.
        query_text: The raw user query string.
        bm25_k: Number of top candidates to return.

    Returns:
        RetrievedContext with strategy "bm25". Each chunk carries the
        ts_rank_cd score as `score` and its position in that ranking as `rank`.
    """
    log.debug("bm25_search.start", bm25_k=bm25_k)

    sql = """
        SELECT c.id AS chunk_id, c.content, c.document_id,
               ts_rank_cd(to_tsvector('english', c.content), plainto_tsquery('english', %(query)s)) AS bm25_score,
               ROW_NUMBER() OVER (ORDER BY ts_rank_cd(to_tsvector('english', c.content), plainto_tsquery('english', %(query)s)) DESC) AS rank
        FROM chunks c
        WHERE to_tsvector('english', c.content) @@ plainto_tsquery('english', %(query)s)
        ORDER BY bm25_score DESC
        LIMIT %(bm25_k)s
    """

    conn = psycopg2.connect(conn_str)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "query": query_text,
                "bm25_k": bm25_k,
            })
            rows = cur.fetchall()
    finally:
        conn.close()

    return RetrievedContext(
        query=query_text,
        chunks=tuple(_chunk_from_row(row) for row in rows),
        strategy="bm25",
    )


# ---------------------------------------------------------------------------
# RRF fusion (single SQL CTE — k=60 is a SQL literal, NOT a parameter)
# ---------------------------------------------------------------------------

_RRF_SQL = """
WITH vector_ranked AS (
    SELECT e.chunk_id, c.content, c.document_id,
           1 - (e.vector <=> %(query_vector)s::vector) AS cosine_score,
           ROW_NUMBER() OVER (ORDER BY e.vector <=> %(query_vector)s::vector) AS rank
    FROM embeddings e JOIN chunks c ON c.id = e.chunk_id
    ORDER BY e.vector <=> %(query_vector)s::vector
    LIMIT %(vector_k)s
),
bm25_ranked AS (
    SELECT c.id AS chunk_id, c.content, c.document_id,
           ts_rank_cd(to_tsvector('english', c.content), plainto_tsquery('english', %(query)s)) AS bm25_score,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(to_tsvector('english', c.content), plainto_tsquery('english', %(query)s)) DESC) AS rank
    FROM chunks c
    WHERE to_tsvector('english', c.content) @@ plainto_tsquery('english', %(query)s)
    ORDER BY bm25_score DESC
    LIMIT %(bm25_k)s
),
fused AS (
    SELECT
        COALESCE(v.chunk_id, b.chunk_id) AS chunk_id,
        COALESCE(v.content, b.content) AS content,
        COALESCE(v.document_id, b.document_id) AS document_id,
        COALESCE(1.0 / (60.0 + v.rank), 0.0) + COALESCE(1.0 / (60.0 + b.rank), 0.0) AS rrf_score,
        v.cosine_score,
        b.bm25_score,
        v.rank AS vector_rank,
        b.rank AS bm25_rank
    FROM vector_ranked v
    FULL OUTER JOIN bm25_ranked b ON v.chunk_id = b.chunk_id
)
SELECT chunk_id, content, document_id, rrf_score,
       cosine_score, bm25_score, vector_rank, bm25_rank
FROM fused ORDER BY rrf_score DESC LIMIT %(final_k)s
"""


@dataclass(frozen=True)
class RrfFusion:
    """One RRF run: the fused ranking, and the two rankings that fed it.

    Named fields rather than a dict, so a caller that misspells one gets an
    AttributeError at the line that reads it. Local to this module because it
    is retrieval's own composite, not a type the rest of the platform names.

    Args:
        fused:             The CTE result, top final_k by RRF score.
        vector_candidates: vector_search, top vector_k by cosine.
        bm25_candidates:   bm25_search, top bm25_k by ts_rank_cd.
    """

    fused: RetrievedContext
    vector_candidates: RetrievedContext
    bm25_candidates: RetrievedContext


def rrf_fuse(
    conn_str: str,
    query_vector: list[float],
    query_text: str,
    strategy: RetrievalStrategy,
) -> RrfFusion:
    """Run the full RRF CTE as one query, with both candidate lists alongside.

    vector_search() and bm25_search() run separately as well, so the
    retrieve_and_rank task traces all three rankings in query.complete without
    re-executing either search.

    k=60 is a SQL literal in _RRF_SQL and is never passed as a parameter
    (locked per CONTEXT.md). The FULL OUTER JOIN plus COALESCE pattern handles
    a chunk that one search matched and the other did not.

    Args:
        conn_str: Decrypted tenant DB connection string.
        query_vector: 1024-dim float vector from embed_query().
        query_text: The raw user query string.
        strategy: Per-tenant retrieval config (vector_k, bm25_k, final_k).

    Returns:
        RrfFusion, one RetrievedContext under each of its three fields.
    """
    log.debug(
        "rrf_fuse.start",
        vector_k=strategy.vector_k,
        bm25_k=strategy.bm25_k,
        final_k=strategy.final_k,
    )

    # Execute the full RRF CTE as one query
    conn = psycopg2.connect(conn_str)
    try:
        with conn.cursor() as cur:
            cur.execute(_RRF_SQL, {
                "query_vector": str(query_vector),
                "query": query_text,
                "vector_k": strategy.vector_k,
                "bm25_k": strategy.bm25_k,
                "final_k": strategy.final_k,
            })
            rows = cur.fetchall()
    finally:
        conn.close()

    # The CTE orders by rrf_score DESC, so position in `rows` is the fused rank.
    fused = RetrievedContext(
        query=query_text,
        chunks=tuple(
            _fused_chunk(row, position)
            for position, row in enumerate(rows, start=1)
        ),
        strategy="rrf",
    )

    # Fetch individual candidate lists separately for trace inclusion
    return RrfFusion(
        fused=fused,
        vector_candidates=vector_search(
            conn_str, query_vector, strategy.vector_k, query_text
        ),
        bm25_candidates=bm25_search(conn_str, query_text, strategy.bm25_k),
    )


# ---------------------------------------------------------------------------
# Query expansion + expansion-aware RRF fusion (M9)
# ---------------------------------------------------------------------------


def _expand_query(query_text: str) -> list[str]:
    """Generate 2 alternative phrasings of the query using Claude Haiku (lazy import).

    Lazy `import anthropic` inside the function body matches the lazy `import cohere`
    pattern used in _cohere_rerank — keeps the module loadable even if the package
    is absent or the API key is not set.

    Args:
        query_text: The raw user query string.

    Returns:
        List of up to 3 queries: [original] + up to 2 generated variants.
    """
    import anthropic  # lazy import — only loaded when query_expansion=True

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": (
                    "Generate 2 alternative phrasings of this search query for a "
                    "customer service knowledge base. Return ONLY the 2 queries, "
                    f"one per line, no numbering:\n\n{query_text}"
                ),
            }
        ],
    )
    # msg.content[0] is a union of block types and only TextBlock carries .text.
    # getattr keeps a non-text first block to zero variants instead of AttributeError.
    first_block = msg.content[0]
    raw_variants = getattr(first_block, "text", "") or ""
    variants = [line.strip() for line in raw_variants.strip().split("\n") if line.strip()]
    return [query_text] + variants[:2]


def rrf_fuse_with_expansion(
    conn_str: str,
    query_vector: list[float],
    query_text: str,
    strategy: RetrievalStrategy,
) -> RrfFusion:
    """RRF fusion with optional query expansion (M9).

    When strategy.query_expansion is False, this hands straight to rrf_fuse and
    costs nothing.

    When True:
      1. Generate up to 2 alternative query phrasings via Claude Haiku.
      2. Batch-embed ALL variants in a single Voyage call (NOT 3 sequential calls).
      3. Run rrf_fuse for each (variant, variant_vector) pair.
      4. Merge the fused chunks, keeping each chunk_id at its highest score.
      5. Return the top final_k under the same three fields rrf_fuse returns.

    Args:
        conn_str:      Decrypted tenant DB connection string.
        query_vector:  1024-dim float vector for the original query.
        query_text:    The raw user query string.
        strategy:      Per-tenant retrieval config.

    Returns:
        RrfFusion, the same three fields rrf_fuse returns. Both candidate
        contexts are empty here. A merge across variants has no one per-engine
        ranking to report.
    """
    if not strategy.query_expansion:
        return rrf_fuse(conn_str, query_vector, query_text, strategy)

    # Generate alternative phrasings
    variants = _expand_query(query_text)

    # Batch-embed ALL variants through the provider seam (no direct Voyage call when bedrock)
    if settings.EMBEDDING_PROVIDER == "bedrock":
        import app.services.bedrock_embedding_service as _bedrock_svc  # noqa: PLC0415
        all_embeddings = _bedrock_svc.embed_texts(variants, "query")
    else:
        all_embeddings = _get_vo().embed(
            variants, model="voyage-3", input_type="query"
        ).embeddings

    # Merge RRF results across all variants, keeping each chunk's best score
    all_fused: dict[str, RetrievedChunk] = {}
    for variant_text, variant_vector in zip(variants, all_embeddings):
        result = rrf_fuse(conn_str, variant_vector, variant_text, strategy)
        for chunk in result.fused.chunks:
            best = all_fused.get(chunk.chunk_id)
            if best is None or chunk.score > best.score:
                all_fused[chunk.chunk_id] = chunk

    merged = sorted(all_fused.values(), key=lambda c: c.score, reverse=True)

    return RrfFusion(
        # Renumbered. The rank a chunk arrived with is one variant's ranking,
        # and the merged order is the ranking this function reports.
        fused=RetrievedContext(
            query=query_text,
            chunks=tuple(
                replace(chunk, rank=position)
                for position, chunk in enumerate(merged[: strategy.final_k], start=1)
            ),
            strategy="rrf",
        ),
        vector_candidates=_empty_context(query_text, "vector"),
        bm25_candidates=_empty_context(query_text, "bm25"),
    )


# ---------------------------------------------------------------------------
# Rerank — Voyage primary, Cohere fallback
# ---------------------------------------------------------------------------

def _reranked_context(
    candidates: RetrievedContext,
    scored: list[tuple[float, int]],
) -> RetrievedContext:
    """Build the reranked context from (score, candidate index) pairs.

    Sorted here rather than at each call site so the Voyage path and the Cohere
    path cannot disagree about the order they hand back. `rank` is the position
    in THIS ranking, so it renumbers from 1 and no longer reports where the
    chunk sat in the fusion that produced it. The query comes off `candidates`,
    which is the query the reranked context has to report.
    """
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return RetrievedContext(
        query=candidates.query,
        chunks=tuple(
            replace(candidates.chunks[index], score=score, rank=position)
            for position, (score, index) in enumerate(scored, start=1)
        ),
        strategy="rerank",
    )


def _cohere_rerank(
    query_text: str,
    candidates: RetrievedContext,
    strategy: RetrievalStrategy,
) -> RetrievedContext:
    """Cohere rerank fallback. Only called when Voyage rerank raises.

    Lazy import of `cohere` keeps it optional at module load time — cohere is
    only a fallback dependency and should not prevent the module from loading
    if the package is absent.

    Args:
        query_text: The raw user query string.
        candidates: The fused context to reorder.
        strategy: Per-tenant retrieval config (final_k, rerank_threshold).

    Returns:
        RetrievedContext with strategy "rerank", carrying the relevance score
        as `score`, sorted descending. Results below rerank_threshold are gone.

    Raises:
        RuntimeError: If COHERE_API_KEY is not set.
    """
    import cohere  # lazy import — cohere is fallback-only dependency

    if not settings.COHERE_API_KEY:
        raise RuntimeError("COHERE_API_KEY not set; cannot fall back to Cohere rerank")

    co = cohere.ClientV2(api_key=settings.COHERE_API_KEY)
    response = co.rerank(
        model="rerank-english-v3.0",
        query=query_text,
        documents=[chunk.content for chunk in candidates.chunks],
        top_n=strategy.final_k,
    )

    scored = [
        (r.relevance_score, r.index)
        for r in response.results
        if r.relevance_score >= strategy.rerank_threshold
    ]
    return _reranked_context(candidates, scored)


def rerank(
    query_text: str,
    candidates: RetrievedContext,
    strategy: RetrievalStrategy,
) -> RetrievedContext:
    """Rerank candidates with Voyage rerank-2; fall back to Cohere on exception.

    Primary (Voyage):
      - model="rerank-2", top_k=strategy.final_k, truncation=True
      - Filters results below strategy.rerank_threshold

    Fallback (Cohere):
      - Triggered on any Voyage exception
      - Logs warning with error_type before delegating to _cohere_rerank

    Args:
        query_text: The raw user query string.
        candidates: The fused context (typically rrf_fuse(...).fused).
        strategy: Per-tenant retrieval config.

    Returns:
        RetrievedContext with strategy "rerank", carrying the relevance score
        as `score` and the new position as `rank`, sorted descending. Results
        below rerank_threshold are excluded. The context passed in is frozen
        and comes back unchanged.
    """
    try:
        reranking = _get_vo().rerank(
            query=query_text,
            documents=[chunk.content for chunk in candidates.chunks],
            model="rerank-2",
            top_k=strategy.final_k,
            truncation=True,
        )

        scored = [
            (r.relevance_score, r.index)
            for r in reranking.results
            if r.relevance_score >= strategy.rerank_threshold
        ]
        return _reranked_context(candidates, scored)

    except Exception as exc:
        log.warning("rerank.voyage_failed_falling_back", error_type=type(exc).__name__)
        return _cohere_rerank(query_text, candidates, strategy)


# ---------------------------------------------------------------------------
# Trace builder
# ---------------------------------------------------------------------------

def build_trace(
    vector_candidates: RetrievedContext,
    bm25_candidates: RetrievedContext,
    fused_candidates: RetrievedContext,
    reranked_candidates: RetrievedContext,
    max_content: int = 200,
) -> dict:
    """Build the retrieval trace dict for the query.complete SSE payload.

    A dict rather than a named record, because this trace IS the diagnostic
    section of an SSE payload and its keys are the wire the admin console reads.

    Truncates `content` to max_content chars in all trace copies to keep the
    SSE payload compact. Full content lives in the top-level `results` field
    of the query.complete payload (assembled by the Celery task).

    Each section reports its own engine's number under `score`, because the
    section name says which engine produced it. `score` inside
    vector_candidates is the cosine similarity, inside bm25_candidates it is
    the ts_rank_cd value, inside fused_candidates it is the RRF score, and
    inside reranked_candidates it is the reranker's relevance score.

    Args:
        vector_candidates: Top vector_k from HNSW search.
        bm25_candidates: Top bm25_k from ts_rank_cd search.
        fused_candidates: Top final_k from RRF CTE.
        reranked_candidates: Final results after Voyage/Cohere rerank.
        max_content: Maximum content characters to include in trace (default 200).

    Returns:
        Dict with keys: vector_candidates, bm25_candidates, fused_candidates,
        reranked_candidates. Each is a list of chunk dicts with truncated
        content. The contexts read are frozen and are not changed.
    """
    def _truncate(context: RetrievedContext) -> list[dict]:
        result = []
        for chunk in context.chunks:
            row = chunk.to_json()
            row["content"] = row["content"][:max_content]
            result.append(row)
        return result

    return {
        "vector_candidates": _truncate(vector_candidates),
        "bm25_candidates": _truncate(bm25_candidates),
        "fused_candidates": _truncate(fused_candidates),
        "reranked_candidates": _truncate(reranked_candidates),
    }
