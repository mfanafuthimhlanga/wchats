"""Metrics routes for W Chats Phase 21 (OPS-03, OPS-07/OPS-08).

Queries tenant DB (turn_metrics, message_feedback) for the "Live" region's
headline KPIs: containment, deflection, escalation rate, CSAT, thumbs-down
rate, p95 latency, and cost-per-session over a window. All computation lives
in app/services/metrics_service.py; this route only owns IDOR + conn_str
resolution + the asyncio.to_thread boundary.

Routes:
    GET /agents/{agent_id}/metrics           — aggregate KPIs over a window (OPS-03)
    GET /agents/{agent_id}/retrieval-health  — RAG-health vitals + index staleness (21-04)

Architecture:
    - turn_metrics/message_feedback/retrieval_metrics live in the TENANT DB
      (per-Neon-project), not the control DB — same tier as
      eval_runs/eval_results (evals.py).
    - Route fetches agent from control DB (get_async_db) for IDOR check only.
    - Tenant DB aggregation goes through psycopg2 with asyncio.to_thread() to
      avoid blocking the FastAPI event loop (D-30 pattern — same as evals.py).
    - GET /retrieval-health reads stored retrieval_metrics rows via
      retrieval_metrics_service.read_retrieval_health (21-03) AND computes a
      LIVE index-staleness summary via
      app.worker.tasks.pipeline.staleness.compute_index_staleness_summary
      (21-04, OPS-08) — a plain function shared with the check_index_staleness
      Celery task, not a cached/stored row (no staleness table exists by
      design — see staleness.py's module docstring).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import psycopg2
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.core.log_bounds import log_failure
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services.metrics_service import compute_agent_metrics
from app.services.retrieval_metrics_service import read_retrieval_health
from app.services.usage_service import read_agent_usage
from app.worker.tasks.pipeline.staleness import compute_index_staleness_summary

router = APIRouter(tags=["metrics"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Route: GET /agents/{agent_id}/metrics — aggregate KPIs over a window
# ---------------------------------------------------------------------------


async def _ledger_turns(conn_str: str, agent_id: str, window_days: int) -> dict:
    """The ledger's whole-turn figure as a `turns` key, or an empty dict.

    The KPIs this decorates are computed from `turn_metrics` and are the
    headline of the console's Live region. The ledger is a second table in the
    same tenant database, read for a figure that enriches those KPIs rather than
    one they depend on, so a ledger that cannot be read costs the reader the
    `turns` key and nothing else. Returning 500 for the whole region because the
    cost breakdown failed would be the enrichment taking the headline down with
    it.

    `UndefinedTable` is the narrow case of a tenant database that predates
    tenant migration 0019 and has no `model_calls` at all, the same tolerance
    shape `read_run_ledger` applies. Everything else is logged through
    `log_failure`, which bounds what the exception may say: psycopg2 renders a
    connection failure with the DSN in it, and `conn_str` never reaches a log
    line here or anywhere else.
    """
    try:
        usage = await asyncio.to_thread(read_agent_usage, conn_str, agent_id, window_days)
    except psycopg2.errors.UndefinedTable:
        log.warning(
            "agent_metrics.ledger_absent",
            agent_id=agent_id,
            window_days=window_days,
            detail="tenant DB predates alembic_tenant 0019, so no turn cost is reported",
        )
        return {}
    except Exception as exc:
        log_failure(
            log, "agent_metrics.ledger_failed", exc, level="error",
            agent_id=agent_id,
            window_days=window_days,
            detail="the whole-turn cost is omitted rather than reported as zero",
        )
        return {}
    return {"turns": usage["turns"]}


@router.get("/agents/{agent_id}/metrics")
async def get_agent_metrics(
    agent_id: UUID,
    window_days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return containment/deflection/escalation/CSAT/thumbs/p95/cost KPIs for an agent.

    Security:
        Fetches agent from control DB and checks agent.tenant_id == tenant.id
        (IDOR prevention, T-21-02-01). Returns 404 for unknown agents or agents
        belonging to a different tenant — never 403 (no existence leak).

    Response shape:
        {containment, deflection, escalation_rate, csat_avg, thumbs_down_rate,
         p95_latency_ms, cost_per_session, sample_size, window_days}
        plus `turns`, the ledger's whole-turn cost, which is absent when the
        ledger could not be read (see `_ledger_turns`).
        Every ratio/average/percentile field is either a float or the literal
        string "not_tracked" when zero underlying rows exist in the window
        (honest-empty-state discipline — never a fabricated 0.0).

        `turns.count` and `sample_size` count different things and may differ.
        `sample_size` is `turn_metrics` rows in the window, every agent in the
        tenant database included. `turns.count` is turn jobs the `model_calls`
        ledger holds rows for, for this agent alone, over whole CAT days. A turn
        whose ledger writes failed is in the first and not the second.
    """
    # 1. Fetch agent from control DB (only metadata — not tenant DB)
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Guard: agent must have a tenant DB configured
    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    # 4. Decrypt connection string at runtime — never stored, never logged (T-02-01)
    conn_str = fernet_decrypt(agent.neon_connection_string)

    # 5. Compute aggregates in a thread pool to avoid blocking the event loop
    metrics = await asyncio.to_thread(compute_agent_metrics, conn_str, window_days)
    # cost_per_session above is the agent loop alone (turn_metrics.cost_usd is
    # written before the judges run). `turns` is the whole figure, priced from
    # the ledger with every judge call included; see usage_service.
    metrics.update(await _ledger_turns(conn_str, str(agent_id), window_days))

    log.info(
        "agent_metrics.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        window_days=window_days,
        sample_size=metrics.get("sample_size"),
    )
    return metrics


# ---------------------------------------------------------------------------
# Route: GET /agents/{agent_id}/usage, what the ledger says the Agent cost
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/usage")
async def get_agent_usage(
    agent_id: UUID,
    window_days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Model-call cost over a window, priced from the tenant ledger at read time.

    Security: the same IDOR pattern as GET /agents/{agent_id}/metrics, 404 on an
    unknown or foreign agent, never 403.

    Response shape:
        {window_days, price_versions,
         total: {calls, cost_usd, cost_zar, unpriced_calls, unrated_calls,
                 price_gaps: [{provider, served_model, call_count}]},
         turns: {...the same money keys, count, conversations, cost_per_turn_usd},
         by_purpose: [{purpose, calls, input_tokens, output_tokens,
                       cache_read_tokens, cache_creation_tokens, cost_usd}],
         by_day: [{day, turns, ...the money keys}],
         by_conversation: [{conversation_id, turns, ...the money keys}],
         by_job: [{job_id, purposes: [str], is_turn, ...the money keys}],
         jobs: int}

        Every cost is a float or null, and null never means free. It has three
        causes and each names the counter that says so:
          - a model the price book refuses nulls both currencies, and
            `unpriced_calls` and `price_gaps` name the provider and model to fix
          - a CAT date older than every fx rate nulls the rand alone, and
            `unrated_calls` counts the calls the dollars are known for
          - zero calls in the group nulls both, because the ledger hook is fail
            open and a group nothing was recorded for is not a free one

        `by_conversation` is the fifty costliest conversations, unpriced ones
        last. `turns.conversations` is the whole count, so a reader can tell a
        truncated list from a complete one.

        `by_job` is the twenty costliest single pieces of work, one row per job
        id, with `is_turn` telling a Customer's turn from an eval or red-team run
        and `purposes` naming every model call that job made. `jobs` is how many
        jobs the window held, so a reader can tell a truncated list from a whole
        one.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")
    conn_str = fernet_decrypt(agent.neon_connection_string)
    usage = await asyncio.to_thread(read_agent_usage, conn_str, str(agent_id), window_days)
    log.info(
        "agent_usage.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        window_days=window_days,
        calls=usage["total"]["calls"],
        unpriced_calls=usage["total"]["unpriced_calls"],
    )
    return usage


# ---------------------------------------------------------------------------
# Route: GET /agents/{agent_id}/retrieval-health — RAG-health vitals + staleness
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/retrieval-health")
async def get_agent_retrieval_health(
    agent_id: UUID,
    window_days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return RAG-health vitals (OPS-05/06/07) + index staleness (OPS-08) for an agent.

    Security:
        Same IDOR pattern as GET /agents/{agent_id}/metrics — 404 (not 403)
        on unknown agent or cross-tenant access (T-21-04-04).

    Response shape:
        {sample_count, avg_bm25_top_score, avg_vector_top_score,
         avg_rrf_top_score, avg_rerank_top_score, avg_reranker_lift,
         avg_recall_at_k, avg_ndcg_at_10, avg_mrr, avg_cited_chunk_rank,
         avg_retrieved_tokens, avg_ctx_window_utilization,
         avg_carried_never_cited_tokens, avg_compaction_ratio,
         avg_citation_coverage, avg_faithfulness,
         faithfulness_sample_count, faithfulness_other_instrument_count,
         index_staleness: {stale_count, stale_document_ids, drift_detected,
                            drift_model_counts, current_embedding_model}}
        Every retrieval-health average is either a float or the literal
        string "not tracked yet" when zero underlying rows exist in the
        window, or the metric has no non-NULL values yet (honest-empty-state
        discipline — never a fabricated number, DOMAIN-NOTES §6).
        avg_faithfulness covers ONE instrument (issue #120) and the two
        faithfulness_* counts say how many rows it covered and how many it left
        out. A sample count of 0 beside a non-zero other-instrument count is a
        window scored under an earlier instrument, NOT an empty window.
        index_staleness signals independently degrade to "not_tracked" only
        on an actual scan failure — a genuinely empty/healthy corpus reports
        real zero/false values, not a sentinel (same discipline
        metrics_service.py already applies to sample_size).
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    conn_str = fernet_decrypt(agent.neon_connection_string)

    health = await asyncio.to_thread(read_retrieval_health, conn_str, window_days)
    staleness = await asyncio.to_thread(compute_index_staleness_summary, conn_str)

    log.info(
        "agent_retrieval_health.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        window_days=window_days,
        sample_count=health.get("sample_count"),
    )
    return {**health, "index_staleness": staleness}
