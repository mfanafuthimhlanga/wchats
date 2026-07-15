"""
metrics_service — OPS-03 aggregation over turn_metrics/message_feedback/conversations.

Provides compute_agent_metrics(conn_str, window_days) — a synchronous, blocking
function (mirrors evals.py's `_query_tenant_db_sync` idiom) that the route wraps
in `asyncio.to_thread()` to avoid blocking the FastAPI event loop.

Honest-empty-state discipline (21-DOMAIN-NOTES.md §6): every metric here is
computed from a stored row. When the underlying row count for a metric is zero,
that metric returns the NOT_TRACKED sentinel string rather than a fabricated
0.0/0/null value that would misrepresent "no data yet" as "verified zero".

Metric definitions:
    containment       = 1 - escalation_rate (share of conversations with no
                         escalated turn in the window)
    escalation_rate    = conversations with >=1 escalated turn / total
                         conversations with any turn in the window
    deflection          = mirrors containment. This schema (migration 0009 +
                         0003 conversations) has no independent human-handoff
                         signal distinct from the `escalate_to_human` tool-use
                         flag already captured as `turn_metrics.escalated`
                         (see app/services/escalation.py — escalation is only
                         ever raised from that ToolUseBlock, never inferred).
                         Fabricating a second, differently-computed number here
                         would violate the honest-empty-state discipline just
                         as much as fabricating a fake row would — so deflection
                         is reported as identical to containment until a
                         genuinely distinct signal exists in the schema.
    p95_latency_ms      = percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                         over turn_metrics.latency_ms in the window
    cost_per_session    = SUM(turn_metrics.cost_usd) / COUNT(DISTINCT conversation_id)
                         over turns in the window that have a recorded cost
    csat_avg            = AVG(message_feedback.csat_score) in the window
    thumbs_down_rate    = COUNT(rating='down') / COUNT(*) over message_feedback
                         in the window
    sample_size         = total turn_metrics row count in the window (a literal
                         count, always reported even when 0 — "zero turns
                         happened" is itself an honest fact, not a fabrication)

Architecture:
    - No SQLAlchemy ORM — raw psycopg2, exactly like every other tenant-DB
      read/write in this codebase (retrieval_service.py, scenario_service.py,
      evals.py's _query_tenant_db_sync).
    - Two queries per call: one aggregate over turn_metrics (+ per-conversation
      escalation rollup via a CTE), one aggregate over message_feedback. Kept
      separate (not a cross-table JOIN) because the two tables have no shared
      key — conversation_id linkage is per-metric, not required for either
      aggregate.
"""

from __future__ import annotations

import psycopg2
import structlog

log = structlog.get_logger(__name__)

# Honest-empty-state sentinel — returned instead of a fabricated 0.0/0/null
# whenever the underlying row count for a given metric is zero.
NOT_TRACKED = "not_tracked"

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_TURN_METRICS_SQL = """
    WITH window_turns AS (
        SELECT conversation_id, escalated, latency_ms, cost_usd
        FROM turn_metrics
        WHERE created_at >= NOW() - (%(window_days)s || ' days')::interval
    ),
    conv_escalation AS (
        SELECT conversation_id, BOOL_OR(escalated) AS any_escalated
        FROM window_turns
        WHERE conversation_id IS NOT NULL
        GROUP BY conversation_id
    )
    SELECT
        COUNT(*) AS total_turns,
        COUNT(DISTINCT wt.conversation_id) AS total_conversations,
        (SELECT COUNT(*) FROM conv_escalation WHERE any_escalated) AS escalated_conversations,
        (SELECT COUNT(*) FROM conv_escalation) AS conv_with_turns,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p95_latency_ms,
        COUNT(latency_ms) AS latency_sample_count,
        SUM(cost_usd) FILTER (WHERE cost_usd IS NOT NULL) AS total_cost,
        COUNT(DISTINCT wt.conversation_id) FILTER (WHERE cost_usd IS NOT NULL) AS cost_conversations
    FROM window_turns wt
"""

_MESSAGE_FEEDBACK_SQL = """
    SELECT
        COUNT(*) AS total_feedback,
        COUNT(csat_score) AS csat_sample_count,
        AVG(csat_score) FILTER (WHERE csat_score IS NOT NULL) AS csat_avg,
        COUNT(*) FILTER (WHERE rating = 'down') AS thumbs_down_count
    FROM message_feedback
    WHERE created_at >= NOW() - (%(window_days)s || ' days')::interval
"""


# ---------------------------------------------------------------------------
# Public entrypoint (sync — wrap in asyncio.to_thread from the route)
# ---------------------------------------------------------------------------


def compute_agent_metrics(conn_str: str, window_days: int = 7) -> dict:
    """Compute agent KPIs over a window from stored turn_metrics/message_feedback rows.

    Blocking psycopg2 call — the caller (GET /agents/{id}/metrics) must wrap
    this in `asyncio.to_thread()`. conn_str is decrypted at the route layer
    and never logged (T-02-01 pattern).

    Args:
        conn_str:    Decrypted tenant DB connection string.
        window_days: Lookback window in days (default 7).

    Returns:
        dict with containment, deflection, escalation_rate, csat_avg,
        thumbs_down_rate, p95_latency_ms, cost_per_session, sample_size,
        window_days. Every ratio/average/percentile field is either a float
        or the NOT_TRACKED sentinel string when zero underlying rows exist.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(_TURN_METRICS_SQL, {"window_days": window_days})
            turn_row = cur.fetchone()

            cur.execute(_MESSAGE_FEEDBACK_SQL, {"window_days": window_days})
            feedback_row = cur.fetchone()
    finally:
        conn.close()

    return _build_metrics_dict(turn_row, feedback_row, window_days)


# ---------------------------------------------------------------------------
# Pure helper — separated from the DB call for easy unit testing
# ---------------------------------------------------------------------------


def _build_metrics_dict(turn_row: tuple, feedback_row: tuple, window_days: int) -> dict:
    """Turn the two raw aggregate rows into the honest-empty-aware metrics dict."""
    (
        total_turns,
        total_conversations,
        escalated_conversations,
        conv_with_turns,
        p95_latency_ms,
        latency_sample_count,
        total_cost,
        cost_conversations,
    ) = turn_row

    (
        total_feedback,
        csat_sample_count,
        csat_avg,
        thumbs_down_count,
    ) = feedback_row

    total_turns = total_turns or 0
    conv_with_turns = conv_with_turns or 0
    escalated_conversations = escalated_conversations or 0
    latency_sample_count = latency_sample_count or 0
    cost_conversations = cost_conversations or 0
    total_feedback = total_feedback or 0
    csat_sample_count = csat_sample_count or 0
    thumbs_down_count = thumbs_down_count or 0

    # containment / escalation_rate / deflection — gated on conv_with_turns
    if conv_with_turns == 0:
        escalation_rate: float | str = NOT_TRACKED
        containment: float | str = NOT_TRACKED
        deflection: float | str = NOT_TRACKED
    else:
        escalation_rate = escalated_conversations / conv_with_turns
        containment = 1 - escalation_rate
        deflection = containment  # see module docstring — no independent signal

    # p95 latency — gated on latency_sample_count
    if latency_sample_count == 0:
        p95_out: float | str = NOT_TRACKED
    else:
        p95_out = float(p95_latency_ms) if p95_latency_ms is not None else NOT_TRACKED

    # cost per session — gated on cost_conversations
    if cost_conversations == 0:
        cost_per_session: float | str = NOT_TRACKED
    else:
        cost_per_session = float(total_cost or 0) / cost_conversations

    # csat_avg — gated on csat_sample_count
    if csat_sample_count == 0:
        csat_avg_out: float | str = NOT_TRACKED
    else:
        csat_avg_out = float(csat_avg)

    # thumbs_down_rate — gated on total_feedback
    if total_feedback == 0:
        thumbs_down_rate: float | str = NOT_TRACKED
    else:
        thumbs_down_rate = thumbs_down_count / total_feedback

    return {
        "containment": containment,
        "deflection": deflection,
        "escalation_rate": escalation_rate,
        "csat_avg": csat_avg_out,
        "thumbs_down_rate": thumbs_down_rate,
        "p95_latency_ms": p95_out,
        "cost_per_session": cost_per_session,
        "sample_size": total_turns,
        "window_days": window_days,
    }
