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

Design decisions:
  - psycopg2 connections use try/finally/close pattern (NOT context manager `with`) to match
    embed.py precedent and avoid implicit transaction wrapping on the connection object.
  - k=60 is a SQL literal, NOT a parameter — locked per CONTEXT.md.
  - Cohere import is lazy (inside _cohere_rerank body) — cohere is a fallback dependency only.
  - rrf_fuse returns a dict with three keys: "fused", "vector_candidates", "bm25_candidates"
    so Plan 03 (retrieve_and_rank task) can include all three in the trace without re-querying.

Uses only native Postgres tsvector + ts_rank_cd for BM25 (no deprecated Neon extensions).
"""

from __future__ import annotations

from typing import Optional

import psycopg2
import structlog
from pydantic import BaseModel, ConfigDict

from app.services.embedding_service import _get_vo
from app.core.config import settings

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
    query_expansion: bool = False  # deferred to M9; always False in M3
    metadata_filters: list[dict] = []  # entity-based filters (M4+); empty in M3


# ---------------------------------------------------------------------------
# Query embedding
# ---------------------------------------------------------------------------

def embed_query(query_text: str) -> list[float]:
    """Embed a user query with Voyage voyage-3, input_type="query".

    CRITICAL: input_type="query" (NOT "document"). The Voyage model prepends a
    different prompt for each type. Using "document" here would silently degrade
    retrieval quality because all ingestion embeddings use "document".

    Args:
        query_text: The raw user query string.

    Returns:
        1024-dimensional float vector (matches embeddings.vector VECTOR(1024) column).
    """
    return _get_vo().embed([query_text], model="voyage-3", input_type="query").embeddings[0]


# ---------------------------------------------------------------------------
# Vector search (pgvector HNSW)
# ---------------------------------------------------------------------------

def vector_search(conn_str: str, query_vector: list[float], vector_k: int) -> list[dict]:
    """Execute pgvector HNSW cosine search against tenant embeddings.

    Uses the HNSW index `embeddings_vector_hnsw_idx` created in
    0001_tenant_v1_schema.py. The query_vector is stringified for psycopg2 →
    pgvector casting (same pattern as embed.py line 235 `str(vec)`).

    Args:
        conn_str: Decrypted tenant DB connection string.
        query_vector: 1024-dim float vector from embed_query().
        vector_k: Number of top candidates to return.

    Returns:
        List of dicts: chunk_id (str), content, document_id (str), cosine_score, rank.
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

    return [
        {
            "chunk_id": str(row[0]),
            "content": row[1],
            "document_id": str(row[2]),
            "cosine_score": row[3],
            "rank": row[4],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# BM25 search (native tsvector + ts_rank_cd only — no deprecated Neon extensions)
# ---------------------------------------------------------------------------

def bm25_search(conn_str: str, query_text: str, bm25_k: int) -> list[dict]:
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
        List of dicts: chunk_id (str), content, document_id (str), bm25_score, rank.
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

    return [
        {
            "chunk_id": str(row[0]),
            "content": row[1],
            "document_id": str(row[2]),
            "bm25_score": row[3],
            "rank": row[4],
        }
        for row in rows
    ]


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


def rrf_fuse(
    conn_str: str,
    query_vector: list[float],
    query_text: str,
    strategy: RetrievalStrategy,
) -> dict:
    """Execute the full RRF CTE as a single query and return fused + individual candidates.

    Also calls vector_search() and bm25_search() separately so the retrieve_and_rank
    task (Plan 03) can include full candidate lists in the query.complete trace without
    re-executing the individual searches.

    k=60 is a SQL literal in _RRF_SQL — it is NOT passed as a parameter (locked per
    CONTEXT.md). The FULL OUTER JOIN + COALESCE pattern handles chunks that appear in
    one search but not the other.

    Args:
        conn_str: Decrypted tenant DB connection string.
        query_vector: 1024-dim float vector from embed_query().
        query_text: The raw user query string.
        strategy: Per-tenant retrieval config (vector_k, bm25_k, final_k).

    Returns:
        Dict with three keys:
          "fused"             — list[dict] from the CTE (top final_k by RRF score)
          "vector_candidates" — list[dict] from vector_search (top vector_k by cosine)
          "bm25_candidates"   — list[dict] from bm25_search (top bm25_k by ts_rank_cd)
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

    fused_rows = [
        {
            "chunk_id": str(row[0]),
            "content": row[1],
            "document_id": str(row[2]),
            "rrf_score": row[3],
            "cosine_score": row[4],
            "bm25_score": row[5],
            "vector_rank": row[6],
            "bm25_rank": row[7],
        }
        for row in rows
    ]

    # Fetch individual candidate lists separately for trace inclusion
    vector_cands = vector_search(conn_str, query_vector, strategy.vector_k)
    bm25_cands = bm25_search(conn_str, query_text, strategy.bm25_k)

    return {
        "fused": fused_rows,
        "vector_candidates": vector_cands,
        "bm25_candidates": bm25_cands,
    }


# ---------------------------------------------------------------------------
# Rerank — Voyage primary, Cohere fallback
# ---------------------------------------------------------------------------

def _cohere_rerank(
    query_text: str,
    candidates: list[dict],
    strategy: RetrievalStrategy,
) -> list[dict]:
    """Cohere rerank fallback. Only called when Voyage rerank raises.

    Lazy import of `cohere` keeps it optional at module load time — cohere is
    only a fallback dependency and should not prevent the module from loading
    if the package is absent.

    Args:
        query_text: The raw user query string.
        candidates: Fused candidates list (dicts with at least "content" key).
        strategy: Per-tenant retrieval config (final_k, rerank_threshold).

    Returns:
        List of candidate dicts with "rerank_score" key added, sorted descending.

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
        documents=[c["content"] for c in candidates],
        top_n=strategy.final_k,
    )

    results: list[dict] = []
    for r in response.results:
        score = r.relevance_score
        if score >= strategy.rerank_threshold:
            chunk = dict(candidates[r.index])
            chunk["rerank_score"] = score
            results.append(chunk)

    results.sort(key=lambda x: x["rerank_score"], reverse=True)
    return results


def rerank(
    query_text: str,
    candidates: list[dict],
    strategy: RetrievalStrategy,
) -> list[dict]:
    """Rerank candidates with Voyage rerank-2; fall back to Cohere on exception.

    Primary (Voyage):
      - model="rerank-2", top_k=strategy.final_k, truncation=True
      - Filters results below strategy.rerank_threshold
      - Returns sorted list with "rerank_score" key added to each dict

    Fallback (Cohere):
      - Triggered on any Voyage exception
      - Logs warning with error_type before delegating to _cohere_rerank

    Args:
        query_text: The raw user query string.
        candidates: Fused candidates (typically rrf_fuse()["fused"]).
        strategy: Per-tenant retrieval config.

    Returns:
        List of candidate dicts with "rerank_score" key, sorted descending by score.
        Results below rerank_threshold are excluded.
    """
    try:
        reranking = _get_vo().rerank(
            query=query_text,
            documents=[c["content"] for c in candidates],
            model="rerank-2",
            top_k=strategy.final_k,
            truncation=True,
        )

        results: list[dict] = []
        for r in reranking.results:
            score = r.relevance_score
            if score >= strategy.rerank_threshold:
                chunk = dict(candidates[r.index])
                chunk["rerank_score"] = score
                results.append(chunk)

        results.sort(key=lambda x: x["rerank_score"], reverse=True)
        return results

    except Exception as exc:
        log.warning("rerank.voyage_failed_falling_back", error_type=type(exc).__name__)
        return _cohere_rerank(query_text, candidates, strategy)


# ---------------------------------------------------------------------------
# Trace builder
# ---------------------------------------------------------------------------

def build_trace(
    vector_candidates: list[dict],
    bm25_candidates: list[dict],
    fused_candidates: list[dict],
    reranked_candidates: list[dict],
    max_content: int = 200,
) -> dict:
    """Build the retrieval trace dict for the query.complete SSE payload.

    Truncates `content` to max_content chars in all trace copies to keep the
    SSE payload compact. Full content lives in the top-level `results` field
    of the query.complete payload (assembled by the Celery task).

    Args:
        vector_candidates: Top vector_k from HNSW search.
        bm25_candidates: Top bm25_k from ts_rank_cd search.
        fused_candidates: Top final_k from RRF CTE.
        reranked_candidates: Final results after Voyage/Cohere rerank.
        max_content: Maximum content characters to include in trace (default 200).

    Returns:
        Dict with keys: vector_candidates, bm25_candidates, fused_candidates,
        reranked_candidates — each a list of dicts with truncated content.
    """
    def _truncate(candidates: list[dict]) -> list[dict]:
        result = []
        for c in candidates:
            copy = dict(c)
            if copy.get("content") and len(copy["content"]) > max_content:
                copy["content"] = copy["content"][:max_content]
            result.append(copy)
        return result

    return {
        "vector_candidates": _truncate(vector_candidates),
        "bm25_candidates": _truncate(bm25_candidates),
        "fused_candidates": _truncate(fused_candidates),
        "reranked_candidates": _truncate(reranked_candidates),
    }
