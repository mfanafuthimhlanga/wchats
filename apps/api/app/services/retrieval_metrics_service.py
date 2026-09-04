"""
retrieval_metrics_service — write + read helpers for the `retrieval_metrics` table (OPS-05/06).

Design decisions:
    - `write_retrieval_metrics` is called from inside `agent_tools.retrieve_tool`'s
      executor closure (never from `agent.py` — the rank/score data does not cross
      back into the SDK loop, see 21-RESEARCH.md Pattern 1 / Anti-Patterns).
    - The write is wrapped in try/except so an INSERT failure only logs a warning —
      it never raises into the caller and never fails the retrieve call or the turn
      (T-21-03-02: Availability mitigation).
    - Uses the same psycopg2 connect/try/finally/close idiom as every other
      tenant-DB write in this codebase (retrieval_service.py, agent_tools.py).
    - `read_retrieval_health` returns honest "not tracked yet" sentinels when zero
      rows exist in the window, or when citation_coverage/faithfulness are still
      entirely NULL (they are only populated by the sampled 21-04 faithfulness task) —
      never fabricates a metric (DOMAIN-NOTES §6, honest-empty-state discipline).
    - The faithfulness average covers one instrument (issue #120): rows scored
      against one of the two pre-280ff05 context proxies carry no
      `context_source` and are left out, with a count saying how many.
"""

from __future__ import annotations

import uuid

import psycopg2
import structlog

from app.domain.eval_result import CONTEXT_PROXY_VERSION

log = structlog.get_logger(__name__)

_INSERT_SQL = """
    INSERT INTO retrieval_metrics
        (id, job_id, conversation_id, bm25_top_score, vector_top_score,
         rrf_top_score, rerank_top_score, reranker_lift, recall_at_k,
         ndcg_at_10, mrr, cited_chunk_rank, retrieved_tokens,
         ctx_window_utilization, carried_never_cited_tokens,
         compaction_ratio, created_at)
    VALUES
        (%(id)s, %(job_id)s, %(conversation_id)s, %(bm25_top_score)s,
         %(vector_top_score)s, %(rrf_top_score)s, %(rerank_top_score)s,
         %(reranker_lift)s, %(recall_at_k)s, %(ndcg_at_10)s, %(mrr)s,
         %(cited_chunk_rank)s, %(retrieved_tokens)s, %(ctx_window_utilization)s,
         %(carried_never_cited_tokens)s, %(compaction_ratio)s, NOW())
"""

_REQUIRED_ROW_KEYS = (
    "job_id",
    "conversation_id",
    "bm25_top_score",
    "vector_top_score",
    "rrf_top_score",
    "rerank_top_score",
    "reranker_lift",
    "recall_at_k",
    "ndcg_at_10",
    "mrr",
    "cited_chunk_rank",
    "retrieved_tokens",
    "ctx_window_utilization",
    "carried_never_cited_tokens",
    "compaction_ratio",
)


def write_retrieval_metrics(conn_str: str, row: dict) -> None:
    """Insert one retrieval_metrics row (OPS-05/06).

    Args:
        conn_str: Decrypted tenant DB connection string.
        row: Dict with keys job_id, conversation_id, bm25_top_score,
             vector_top_score, rrf_top_score, rerank_top_score, reranker_lift,
             recall_at_k, ndcg_at_10, mrr, cited_chunk_rank, retrieved_tokens,
             ctx_window_utilization, carried_never_cited_tokens, compaction_ratio.
             citation_coverage/faithfulness are NOT accepted here — they stay
             NULL and are populated later by the sampled 21-04 faithfulness task.

    Never raises: any exception (bad conn_str, network error, DB outage) is
    caught, logged as a warning, and swallowed — a metrics write failure must
    never fail the retrieve call or the turn it belongs to (T-21-03-02).
    """
    params: dict[str, object] = {"id": str(uuid.uuid4())}
    for key in _REQUIRED_ROW_KEYS:
        params[key] = row.get(key)

    try:
        conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(_INSERT_SQL, params)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — metrics write must never fail the caller
        log.warning(
            "write_retrieval_metrics.failed",
            job_id=row.get("job_id"),
            error=str(exc),
        )
        return

    log.debug(
        "write_retrieval_metrics.done",
        job_id=row.get("job_id"),
        conversation_id=row.get("conversation_id"),
    )


_HEALTH_SQL = """
    SELECT
        COUNT(*) AS sample_count,
        AVG(bm25_top_score) AS avg_bm25_top_score,
        AVG(vector_top_score) AS avg_vector_top_score,
        AVG(rrf_top_score) AS avg_rrf_top_score,
        AVG(rerank_top_score) AS avg_rerank_top_score,
        AVG(reranker_lift) AS avg_reranker_lift,
        AVG(recall_at_k) AS avg_recall_at_k,
        AVG(ndcg_at_10) AS avg_ndcg_at_10,
        AVG(mrr) AS avg_mrr,
        AVG(cited_chunk_rank) AS avg_cited_chunk_rank,
        AVG(retrieved_tokens) AS avg_retrieved_tokens,
        AVG(ctx_window_utilization) AS avg_ctx_window_utilization,
        AVG(carried_never_cited_tokens) AS avg_carried_never_cited_tokens,
        AVG(compaction_ratio) AS avg_compaction_ratio,
        AVG(citation_coverage) AS avg_citation_coverage,
        AVG(faithfulness) FILTER (WHERE context_source = %(context_source)s) AS avg_faithfulness,
        COUNT(faithfulness) FILTER (WHERE context_source = %(context_source)s) AS faithfulness_sample_count,
        COUNT(faithfulness) FILTER (WHERE context_source IS DISTINCT FROM %(context_source)s) AS faithfulness_unstamped_count
    FROM retrieval_metrics
    WHERE created_at >= NOW() - (%(window_days)s || ' days')::interval
"""

_HEALTH_AVG_KEYS = (
    "avg_bm25_top_score",
    "avg_vector_top_score",
    "avg_rrf_top_score",
    "avg_rerank_top_score",
    "avg_reranker_lift",
    "avg_recall_at_k",
    "avg_ndcg_at_10",
    "avg_mrr",
    "avg_cited_chunk_rank",
    "avg_retrieved_tokens",
    "avg_ctx_window_utilization",
    "avg_carried_never_cited_tokens",
    "avg_compaction_ratio",
    "avg_citation_coverage",
    "avg_faithfulness",
)

_NOT_TRACKED = "not tracked yet"


def read_retrieval_health(conn_str: str, window_days: int = 7) -> dict:
    """Aggregate retrieval_metrics over the trailing `window_days` (21-04 read endpoint).

    Args:
        conn_str: Decrypted tenant DB connection string.
        window_days: Trailing window size in days (default 7).

    Returns:
        Dict with "sample_count" plus one "avg_*" key per retrieval_metrics
        numeric column, plus "faithfulness_sample_count" and
        "faithfulness_unstamped_count". When sample_count is 0, every "avg_*"
        value is the string "not tracked yet" rather than a fabricated number.
        Even with sample_count > 0, "avg_citation_coverage"/"avg_faithfulness"
        are independently reported as "not tracked yet" until they hold at least
        one non-NULL value (they are only populated by the sampled 21-04
        faithfulness task, not by this write path).

        THE FAITHFULNESS AVERAGE COVERS ONE INSTRUMENT (issue #120). It is taken
        over rows whose `context_source` names the current context shape, so a
        row scored against one of the two pre-280ff05 proxies is left out rather
        than averaged in with rows scored against the retrieved chunks. Mixing
        them reports an instrument change as a quality change, which is the
        sentence #84 exists to prevent. The two counts say what the average
        covered and what it left out, because a filtered average that does not
        name what it dropped is a smaller lie than an unfiltered one and is
        still one.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                _HEALTH_SQL,
                {"window_days": window_days, "context_source": CONTEXT_PROXY_VERSION},
            )
            row = cur.fetchone()
    finally:
        conn.close()

    sample_count = row[0] or 0
    averages = row[1 : 1 + len(_HEALTH_AVG_KEYS)]
    faithfulness_counts = row[1 + len(_HEALTH_AVG_KEYS) :]

    result: dict = {"sample_count": sample_count}
    result["faithfulness_sample_count"] = faithfulness_counts[0] or 0
    result["faithfulness_unstamped_count"] = faithfulness_counts[1] or 0
    if sample_count == 0:
        for key in _HEALTH_AVG_KEYS:
            result[key] = _NOT_TRACKED
        return result

    for key, value in zip(_HEALTH_AVG_KEYS, averages):
        result[key] = float(value) if value is not None else _NOT_TRACKED

    return result
