"""
M9 Strategy service: corpus-signal-driven retrieval strategy synthesizer (direct API).

Architecture notes:
- Corpus signals are collected synchronously (psycopg2) BEFORE the model call.
- One chat completion with a forced generate_strategy tool call, read for its arguments.
- Synchronous — no asyncio bridge needed; safe in Celery pipeline tasks.
- Connection strings NEVER logged (CTL-08).
- The client comes from a `LedgerContext` (ticket #46, issue #76), so every
  response leaves one model_calls row in the tenant database and the model comes
  off the purpose's route. Recording is fail open, so a ledger failure logs and
  the strategy call still returns.
"""
from __future__ import annotations

import psycopg2
import structlog

from app.core.log_bounds import log_failure
from app.core.model_client import LedgerContext, ledger_recorder, route_for
from app.domain.ingestion_job import IngestionJob
from app.services.tool_loop import forced_tool_arguments

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Corpus signal collection (sync psycopg2 — safe in Celery tasks)
# CTL-08: conn_str is NEVER passed to log statements — only agent_id.
# ---------------------------------------------------------------------------


def _fetch_corpus_signals_sync(agent_id: str, conn_str: str) -> dict:
    """Fetch corpus-level signals from the tenant DB for strategy synthesis.

    Runs four queries to characterise the corpus:
      1. chunk_count, doc_count, avg_chunk_len, max_chunk_len
      2. table_ratio  (fraction of chunks that contain pipe characters — proxy for tables)
      3. entity_count (rows in entities table)
      4. doc_types    (distribution of documents.source_type values)

    Returns:
        Dict with keys: chunk_count, doc_count, avg_chunk_len, max_chunk_len,
        table_ratio, entity_count, doc_types.

    Note: AVG/SUM on empty sets return None in Postgres — defensive coercion
    (int(x or 0) / float(x or 0)) applied to every aggregate.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        # Query 1: chunk volume and size metrics
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), COUNT(DISTINCT document_id), "
                "AVG(LENGTH(content)), MAX(LENGTH(content)) FROM chunks"
            )
            row = cur.fetchone()
            chunk_count = int(row[0] or 0)
            doc_count = int(row[1] or 0)
            avg_chunk_len = float(row[2] or 0)
            max_chunk_len = int(row[3] or 0)

        # Query 2: table-content ratio (pipe character as table proxy)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT SUM(CASE WHEN content LIKE '%|%' THEN 1 ELSE 0 END), COUNT(*) "
                "FROM chunks"
            )
            row2 = cur.fetchone()
            table_chunks = int(row2[0] or 0)
            total_chunks = int(row2[1] or 0)
            table_ratio = round(table_chunks / max(total_chunks, 1), 3)

        # Query 3: entity count
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM entities")
            row3 = cur.fetchone()
            entity_count = int(row3[0] or 0)

        # Query 4: document type distribution
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_type, COUNT(*) FROM documents GROUP BY source_type"
            )
            doc_types = {row[0]: int(row[1]) for row in cur.fetchall()}

    finally:
        conn.close()

    log.info(
        "strategy_service.corpus_signals",
        agent_id=agent_id,
        chunk_count=chunk_count,
        doc_count=doc_count,
        entity_count=entity_count,
    )

    return {
        "chunk_count": chunk_count,
        "doc_count": doc_count,
        "avg_chunk_len": avg_chunk_len,
        "max_chunk_len": max_chunk_len,
        "table_ratio": table_ratio,
        "entity_count": entity_count,
        "doc_types": doc_types,
    }


# ---------------------------------------------------------------------------
# Strategist system prompt
# ---------------------------------------------------------------------------

_STRATEGIST_SYSTEM_PROMPT = """\
You are the retrieval strategy synthesizer for a customer-service AI platform.
Given corpus signals, you select optimized retrieval parameters by applying these heuristics:

VECTOR_K and BM25_K selection (based on chunk_count):
- chunk_count > 5000  → vector_k=30, bm25_k=25
- chunk_count 1000–5000 → vector_k=20, bm25_k=20
- chunk_count < 1000  → vector_k=15, bm25_k=15

RERANK_THRESHOLD and QUERY_EXPANSION (based on avg_chunk_len):
- avg_chunk_len > 400  → rerank_threshold=0.3, query_expansion=false (chunks are rich; rerank aggressively)
- avg_chunk_len < 150  → rerank_threshold=0.0, query_expansion=true  (chunks are terse; expand for coverage)
- avg_chunk_len 150–400 → rerank_threshold=0.1, query_expansion=false (balanced; light rerank)

ADJUSTMENTS:
- table_ratio > 0.20  → add 5 to bm25_k (table content benefits from keyword search)
- entity_count > 500  → set metadata_filters to a non-empty hint list, e.g. [{"type": "entity_boost"}]

FINAL_K:
- final_k = min(5, vector_k // 4)

After applying all heuristics and adjustments, call generate_strategy exactly once
with the optimized values. Do not call it more than once.
"""


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

_TOOL_GENERATE_STRATEGY = {
    "type": "function",
    "function": {
        "name": "generate_strategy",
        "description": (
            "Submit the optimized retrieval strategy parameters derived from corpus "
            "signals."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "vector_k": {
                    "type": "integer",
                    "description": "Number of candidates from HNSW vector search.",
                },
                "bm25_k": {
                    "type": "integer",
                    "description": "Number of candidates from BM25 ts_rank_cd search.",
                },
                "final_k": {
                    "type": "integer",
                    "description": "Number of results after reranking (min(5, vector_k // 4)).",
                },
                "rerank_threshold": {
                    "type": "number",
                    "description": "Minimum rerank score to include a result (0.0 = include all).",
                },
                "query_expansion": {
                    "type": "boolean",
                    "description": "Whether to expand the query with alternative phrasings before retrieval.",
                },
                "metadata_filters": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Entity-based metadata filter hints (empty list when not applicable).",
                },
            },
            "required": [
                "vector_k",
                "bm25_k",
                "final_k",
                "rerank_threshold",
                "query_expansion",
                "metadata_filters",
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Direct-API strategist (synchronous — safe in Celery pipeline tasks)
# ---------------------------------------------------------------------------


def run_strategist(
    signals_json: str,
    result_container: dict,
    job: IngestionJob,
    tenant_dsn: str,
) -> None:
    """Call the routed model directly to synthesize a retrieval strategy.

    Uses a tool call to reliably extract structured parameters. Logs and swallows
    exceptions so the Celery task falls back to RetrievalStrategy() defaults.

    Args:
        signals_json:     the corpus signals the Strategist reasons over.
        result_container: where the parsed strategy is left for the caller.
        job:              the three ids every ledger row from this call carries.
                          The job type holds no connection string and has no field
                          for one (project rule 1), which is why the dsn below is a
                          separate argument.
        tenant_dsn:       the tenant database each ledger row is written to.
    """
    try:
        ledger = LedgerContext(
            tenant_id=job.tenant_id,
            agent_id=job.agent_id,
            job_id=job.job_id,
            recorder=ledger_recorder(tenant_dsn),
        )
        response = ledger.client("retrieval_strategist").chat.completions.create(
            # BACKLOG 8.2a. Judgement is the one task that wants no creativity, and
            # every judge in this system sampled at the provider default until now.
            # Some verdict variance survives temperature 0 anyway, from batching and
            # hardware nondeterminism, which is why a high-stakes verdict eventually
            # wants more than one sample. (An earlier version of this comment put
            # that at "3-8%". That number is QUOTED from a talk and has never been
            # measured in this system, and CLAUDE.md's own rule is to test a
            # constraint rather than repeat it. BACKLOG 8.11 measures it.)
            temperature=0,
            model=route_for("retrieval_strategist").model,
            max_completion_tokens=500,
            tools=[_TOOL_GENERATE_STRATEGY],  # type: ignore[list-item]  # same dict schema
            messages=[
                {"role": "system", "content": _STRATEGIST_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Corpus signals:\n\n{signals_json}\n\nCall generate_strategy.",
                },
            ],
        )
        arguments = forced_tool_arguments(response, "generate_strategy")
        if arguments is not None:
            result_container["strategy"] = arguments
            return
    except Exception as exc:
        # The type as well as the text. This site swallows so the task falls back to
        # RetrievalStrategy() defaults, so the log is the only place the failure is
        # named, and `ForcedToolCallTruncated` there means the 500-token ceiling is
        # too low for the strategy JSON rather than that the model refused.
        log_failure(log, "run_strategist.failed", exc)
