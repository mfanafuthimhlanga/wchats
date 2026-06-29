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

    Called BEFORE hybrid search (D-24). On hit: update last_used_at + use_count
    (D-26); return dict. On miss: return None (D-27).

    Uses psycopg2 try/finally/close pattern (NOT context manager `with conn:`) to
    match the existing vector_search and bm25_search patterns in this module.

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
            "cosine_score": float(row[3]) if row[3] is not None else None,
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
            "bm25_score": float(row[3]) if row[3] is not None else None,
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
            "rrf_score": float(row[3]) if row[3] is not None else None,
            "cosine_score": float(row[4]) if row[4] is not None else None,
            "bm25_score": float(row[5]) if row[5] is not None else None,
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
    variants = [
        line.strip()
        for line in msg.content[0].text.strip().split("\n")
        if line.strip()
    ]
    return [query_text] + variants[:2]


def rrf_fuse_with_expansion(
    conn_str: str,
    query_vector: list[float],
    query_text: str,
    strategy: RetrievalStrategy,
) -> dict:
    """RRF fusion with optional query expansion (M9).

    When strategy.query_expansion is False, delegates directly to rrf_fuse
    (passthrough — no performance cost).

    When True:
      1. Generate up to 2 alternative query phrasings via Claude Haiku.
      2. Batch-embed ALL variants in a single Voyage call (NOT 3 sequential calls).
      3. Run rrf_fuse for each (variant, variant_vector) pair.
      4. Merge results keeping the highest rrf_score per chunk_id.
      5. Return top final_k results in the same shape as rrf_fuse.

    Args:
        conn_str:      Decrypted tenant DB connection string.
        query_vector:  1024-dim float vector for the original query.
        query_text:    The raw user query string.
        strategy:      Per-tenant retrieval config.

    Returns:
        Dict with keys "fused", "vector_candidates", "bm25_candidates" —
        same shape as rrf_fuse.
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

    # Merge RRF results across all variants
    all_fused: dict[str, dict] = {}
    for variant_text, variant_vector in zip(variants, all_embeddings):
        result = rrf_fuse(conn_str, variant_vector, variant_text, strategy)
        for chunk in result["fused"]:
            chunk_id = chunk["chunk_id"]
            existing_score = all_fused.get(chunk_id, {}).get("rrf_score")
            new_score = chunk.get("rrf_score")
            if chunk_id not in all_fused:
                all_fused[chunk_id] = chunk
            else:
                # Keep highest rrf_score (treat None as -inf)
                existing = existing_score if existing_score is not None else float("-inf")
                incoming = new_score if new_score is not None else float("-inf")
                if incoming > existing:
                    all_fused[chunk_id] = chunk

    merged = sorted(
        all_fused.values(),
        key=lambda r: (r["rrf_score"] if r["rrf_score"] is not None else float("-inf")),
        reverse=True,
    )[: strategy.final_k]

    return {
        "fused": merged,
        "vector_candidates": [],
        "bm25_candidates": [],
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
