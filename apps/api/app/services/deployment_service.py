"""
M8 Deployment service: pre-deployment readiness orchestrator (Claude Agent SDK Sonnet).

Architecture notes:
- Signals are collected synchronously (psycopg2) BEFORE calling the Agent SDK.
- Agent calls submit_report as a side-effect tool; runner captures ToolUseBlock
  and writes to result_container. No tool result sent back (same as report_finding pattern).
- claude-agent-sdk==0.1.81 PINNED — do not upgrade without testing.
- asyncio.run(asyncio.wait_for(..., timeout=120.0)) bridge in Celery task.
- DEP-01 latency/cost signals deferred to M10 (OPS-04). M8 reads only eval, red team,
  verified QA, and corpus stats from the DB.
"""
from __future__ import annotations

import asyncio
import json
import math
from typing import Literal

import psycopg2
import structlog
from pydantic import BaseModel
from sqlalchemy import text
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    ToolUseBlock,
)
from app.core.config import settings
from app.core.database import get_sync_db
from app.services.transactional.enforcement import _parse_rate_limit

SONNET_MODEL = "claude-sonnet-4-6"
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class DeploymentWarning(BaseModel):
    """A single warning produced by the deployment readiness orchestrator."""

    warning_id: str          # unique slug, e.g. "verified_qa_low_count"
    category: str            # "eval_quality" | "security" | "knowledge_depth" | "corpus_coverage"
    message: str             # plain-language text for the non-technical owner
    severity_level: str      # "info" | "warning"


class DeploymentReport(BaseModel):
    """Full deployment readiness report returned by the orchestrator."""

    recommendation: Literal["ship", "ship_with_warnings", "block"]
    summary: str             # 2-3 plain-language sentences for the owner
    warnings: list[DeploymentWarning]
    eval_summary: dict       # raw signal from _fetch_eval_summary_sync
    red_team_summary: dict   # raw signal from _fetch_red_team_summary_sync
    verified_qa_stats: dict  # raw signal from _fetch_verified_qa_stats_sync
    corpus_stats: dict       # raw signal from _fetch_corpus_stats_sync
    # Phase 18 BLR-01: raw signal from _fetch_blast_radius_sync. Defaults to {}
    # so any existing construction site that predates this field still validates.
    blast_radius: dict = {}


# ---------------------------------------------------------------------------
# Orchestrator system prompt
# ---------------------------------------------------------------------------

_DEPLOYMENT_SYSTEM_PROMPT = """\
You are the pre-deployment readiness orchestrator for a customer-service AI agent.
You have been given the agent's quality signals. Assess readiness and call submit_report.

Blocking conditions (always use recommendation='block'):
- red_team_summary.deployment_blocked == True
- DEP_BLOCK_ON_HIGH_RED_TEAM is True and red_team_summary.high_count > 0
- Any eval metric pass_rate < 0.70

Warning conditions (recommendation='ship_with_warnings'):
- verified_qa_stats.row_count < 50 (agent answers more from scratch on day 1)
- Any eval metric pass_rate in [0.70, 0.85)
- red_team_summary.medium_count > 2

Ship condition (recommendation='ship'):
- All eval metrics >= 0.85
- deployment_blocked=False and high_count=0
- verified_qa_stats.row_count >= 50

Financial blast-radius awareness (BLR-01, narrative only — not a blocking condition):
You have also been given a blast_radius signal with configured_max_single_action_cents,
configured_max_hourly_aggregate_cents, observed_max_single_action_cents,
observed_max_hourly_aggregate_cents, observed_window_days, warn_threshold_single_cents,
warn_threshold_hourly_cents and enabled_skill_count. You may reference the configured
ceiling and the observed maximum in your plain-language summary, but you must always
keep them as two separate claims — a configured ceiling is what the owner authorized,
an observed maximum is what has actually happened, and they must never be presented as
the same number. Do not emit a warning for blast radius: the platform derives that
warning deterministically in Python from the configured values, never from your
arithmetic comparison.

Write the summary for a non-technical business owner — no jargon, 2-3 sentences.
List each concern as a warning with a unique warning_id slug.
Call submit_report exactly once with your assessment.
"""
# Phase 18 BLR-01: the orchestrator is told to narrate the blast-radius signal but
# never to raise a warning for it. A financial gate must not depend on an LLM
# performing an arithmetic comparison (CLAUDE.md: programmatic core, agentic
# edges) — derive_blast_radius_warnings() below is the sole source of any
# blast-radius warning_id, and the Celery task de-duplicates by warning_id when
# merging it into run_obj.warnings, which is what prevents the same warning
# appearing twice if a future prompt revision starts emitting one anyway.


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

_TOOL_SUBMIT_REPORT = {
    "name": "submit_report",
    "description": "Submit the deployment readiness report with recommendation and warnings.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendation": {
                "type": "string",
                "enum": ["ship", "ship_with_warnings", "block"],
            },
            "summary": {"type": "string"},
            "warnings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "warning_id": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": [
                                "eval_quality",
                                "security",
                                "knowledge_depth",
                                "corpus_coverage",
                            ],
                        },
                        "message": {"type": "string"},
                        "severity_level": {
                            "type": "string",
                            "enum": ["info", "warning"],
                        },
                    },
                    "required": ["warning_id", "category", "message", "severity_level"],
                },
            },
        },
        "required": ["recommendation", "summary", "warnings"],
    },
}


# ---------------------------------------------------------------------------
# iframe snippet helper
# ---------------------------------------------------------------------------


def _make_iframe_snippet(agent_id: str) -> str:
    """Return the embeddable widget script tag for the given agent."""
    return (
        f'<script src="https://widget.wchats.app/widget.js" '
        f'data-agent="{agent_id}" async></script>'
    )


# ---------------------------------------------------------------------------
# Signal collection functions (sync psycopg2 — safe in Celery tasks)
# CTL-08: conn_str is NEVER passed to log statements — only agent_id.
# ---------------------------------------------------------------------------


def _fetch_eval_summary_sync(agent_id: str, conn_str: str) -> dict:
    """Fetch the most recent eval run summary from the tenant DB.

    Returns dict with keys: last_run_at, scenario_count, pass_rates, failing_scenarios.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, finished_at FROM eval_runs ORDER BY started_at DESC LIMIT 1"
            )
            run_row = cur.fetchone()
            if run_row is None:
                log.info("deployment_service.eval_summary.no_runs", agent_id=agent_id)
                return {
                    "last_run_at": None,
                    "scenario_count": 0,
                    "pass_rates": {},
                    "failing_scenarios": 0,
                }
            cur.execute(
                "SELECT metric_name, AVG(score) FROM eval_results "
                "WHERE run_id = %s GROUP BY metric_name",
                (str(run_row[0]),),
            )
            pass_rates = {row[0]: float(row[1]) for row in cur.fetchall()}
            return {
                "last_run_at": run_row[1].isoformat() if run_row[1] else None,
                "scenario_count": len(pass_rates),
                "pass_rates": pass_rates,
                "failing_scenarios": sum(1 for v in pass_rates.values() if v < 0.70),
            }
    finally:
        conn.close()


def _fetch_red_team_summary_sync(agent_id: str, conn_str: str) -> dict:
    """Fetch the live open-finding severity summary from the tenant DB.

    OPS-15 (21-08): reads the first-class red_team_findings table — WHERE
    status='open' GROUP BY severity — instead of parsing the red_team_runs
    findings JSONB blob. deployment_blocked is True iff there is at least
    one open critical finding, so a live critical finding always drives the
    deploy gate to recommendation='block' regardless of which run produced
    it (a finding stays "live" across runs until contained/closed via
    POST /red-team/findings/{id}/contain).

    last_run_at still comes from the most recent red_team_runs row —
    red_team_findings has no run timestamp of its own.

    Returns dict with keys: last_run_at, deployment_blocked, critical_count,
    high_count, medium_count, low_count.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT started_at FROM red_team_runs ORDER BY started_at DESC LIMIT 1"
            )
            run_row = cur.fetchone()
            last_run_at = run_row[0].isoformat() if run_row and run_row[0] else None
            if run_row is None:
                log.info("deployment_service.red_team_summary.no_runs", agent_id=agent_id)

            cur.execute(
                "SELECT severity, COUNT(*) FROM red_team_findings "
                "WHERE status = 'open' GROUP BY severity"
            )
            counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            for sev, cnt in cur.fetchall():
                if sev in counts:
                    counts[sev] = int(cnt)

            deployment_blocked = counts["critical"] > 0
            return {
                "last_run_at": last_run_at,
                "deployment_blocked": deployment_blocked,
                "critical_count": counts["critical"],
                "high_count": counts["high"],
                "medium_count": counts["medium"],
                "low_count": counts["low"],
            }
    finally:
        conn.close()


def _fetch_verified_qa_stats_sync(agent_id: str, conn_str: str) -> dict:
    """Fetch row count and average scores from the tenant DB verified_qa table.

    Columns are 'faithfulness' and 'relevance' (see alembic_tenant migration 0005).
    Falls back gracefully if the table doesn't exist or has no rows.

    Returns dict with keys: row_count, avg_faithfulness, avg_relevance.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT COUNT(*), "
                    "COALESCE(AVG(faithfulness), 0.0), "
                    "COALESCE(AVG(relevance), 0.0) "
                    "FROM verified_qa"
                )
                row = cur.fetchone()
                if row is None or row[0] == 0:
                    return {"row_count": 0, "avg_faithfulness": 0.0, "avg_relevance": 0.0}
                return {
                    "row_count": int(row[0]),
                    "avg_faithfulness": float(row[1]),
                    "avg_relevance": float(row[2]),
                }
            except Exception as exc:
                log.warning(
                    "deployment_service.verified_qa_stats.query_failed",
                    agent_id=agent_id,
                    error=str(exc),
                )
                return {"row_count": 0, "avg_faithfulness": 0.0, "avg_relevance": 0.0}
    finally:
        conn.close()


def _fetch_corpus_stats_sync(agent_id: str, conn_str: str) -> dict:
    """Fetch document and chunk counts from the tenant DB.

    Returns dict with keys: document_count, chunk_count, last_ingested_at.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM documents")
            doc_row = cur.fetchone()
            document_count = int(doc_row[0]) if doc_row else 0

            cur.execute("SELECT COUNT(*) FROM chunks")
            chunk_row = cur.fetchone()
            chunk_count = int(chunk_row[0]) if chunk_row else 0

            cur.execute("SELECT MAX(created_at) FROM documents")
            ts_row = cur.fetchone()
            last_ingested_at = (
                ts_row[0].isoformat() if ts_row and ts_row[0] else None
            )

            return {
                "document_count": document_count,
                "chunk_count": chunk_count,
                "last_ingested_at": last_ingested_at,
            }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Blast-radius collector (BLR-01) — the one collector that reads the
# CONTROL DB via get_sync_db() rather than the tenant DB via psycopg2.
# capability_envelopes, tool_calls_audit and tenants are all control-DB
# tables, so this collector needs no tenant connection string at all —
# _fetch_blast_radius_sync takes only agent_id (CLAUDE.md rule 4 satisfied
# by construction, not by omission).
# ---------------------------------------------------------------------------

# Safe-default fallback the Celery task substitutes when
# _fetch_blast_radius_sync raises. Every figure is None, not 0 — a fallback
# of 0 would assert "this agent's largest authorized action is nothing",
# which is a false measurement rather than an honestly missing one (OD-1).
BLAST_RADIUS_DEFAULT_SIGNAL: dict = {
    "configured_max_single_action_cents": None,
    "configured_max_hourly_aggregate_cents": None,
    "observed_max_single_action_cents": None,
    "observed_max_hourly_aggregate_cents": None,
    "observed_window_days": settings.BLAST_RADIUS_OBSERVED_WINDOW_DAYS,
    "warn_threshold_single_cents": settings.BLAST_RADIUS_WARN_SINGLE_CENTS,
    "warn_threshold_hourly_cents": settings.BLAST_RADIUS_WARN_HOURLY_CENTS,
    "enabled_skill_count": 0,
}


def _resolve_blast_radius_thresholds(agent_id: str) -> tuple[int, int]:
    """Resolve the per-tenant blast-radius warning thresholds (OD-1b).

    Joins agents to tenants on agents.tenant_id and reads the two nullable
    tenants.blast_radius_warn_*_cents columns. A NULL column (or no row at
    all — an agent with no owning tenant should never happen, but the
    fallback is honest either way) falls back to the platform default in
    settings.BLAST_RADIUS_WARN_SINGLE_CENTS / _HOURLY_CENTS. COALESCE
    semantics are applied in Python, not in SQL, so the fallback value is
    always the live settings value rather than a value baked into the
    query at authoring time.

    Returns:
        (warn_threshold_single_cents, warn_threshold_hourly_cents)
    """
    with get_sync_db() as db:
        row = db.execute(
            text(
                "SELECT t.blast_radius_warn_single_cents, "
                "t.blast_radius_warn_hourly_cents "
                "FROM agents a JOIN tenants t ON a.tenant_id = t.id "
                "WHERE a.id = :agent_id"
            ),
            {"agent_id": agent_id},
        ).first()

    if row is None:
        return (
            settings.BLAST_RADIUS_WARN_SINGLE_CENTS,
            settings.BLAST_RADIUS_WARN_HOURLY_CENTS,
        )

    single_cents = row[0] if row[0] is not None else settings.BLAST_RADIUS_WARN_SINGLE_CENTS
    hourly_cents = row[1] if row[1] is not None else settings.BLAST_RADIUS_WARN_HOURLY_CENTS
    return (single_cents, hourly_cents)


def _fetch_blast_radius_sync(agent_id: str) -> dict:
    """Fetch the financial blast-radius signal from the CONTROL DB (BLR-01).

    This is the one signal collector that reads the control DB via
    get_sync_db() rather than the tenant DB via psycopg2.connect(conn_str,
    ...), because capability_envelopes, tool_calls_audit and tenants are
    all control-DB tables — it therefore needs no tenant connection string
    at all. Signature takes only agent_id.

    Reports two independent claims as four separately-named figures, never
    merged and never coerced from None to 0 (OD-1):

    - configured_max_single_action_cents / configured_max_hourly_aggregate_cents:
      what the owner has AUTHORIZED, derived from enabled capability_envelopes
      rows. None means "no ceiling configured" — either because at least one
      enabled skill carries no bound (a partially-bounded configuration is
      honestly reported as unbounded, never as the max of only the bounded
      rows) or because a rate_limit string failed to parse.
    - observed_max_single_action_cents / observed_max_hourly_aggregate_cents:
      what has actually HAPPENED, derived from successful tool_calls_audit
      rows over the trailing observed_window_days. None means "never
      observed" — a query that returned no qualifying rows.

    A configured None means "no ceiling configured"; an observed None means
    "never observed". These are different claims and callers must not
    collapse them (UI-SPEC D3, D4).

    Returns dict with keys: configured_max_single_action_cents,
    configured_max_hourly_aggregate_cents, observed_max_single_action_cents,
    observed_max_hourly_aggregate_cents, observed_window_days,
    warn_threshold_single_cents, warn_threshold_hourly_cents,
    enabled_skill_count.
    """
    observed_window_days = settings.BLAST_RADIUS_OBSERVED_WINDOW_DAYS

    with get_sync_db() as db:
        # --- configured single-action ceiling -------------------------------
        configured_max_row = db.execute(
            text(
                "SELECT MAX((constraints->>'max_amount_cents')::int) "
                "FROM capability_envelopes "
                "WHERE agent_id = :agent_id AND enabled = true"
            ),
            {"agent_id": agent_id},
        ).scalar()
        unbounded_single_count = db.execute(
            text(
                "SELECT COUNT(*) FROM capability_envelopes "
                "WHERE agent_id = :agent_id AND enabled = true "
                "AND constraints->>'max_amount_cents' IS NULL"
            ),
            {"agent_id": agent_id},
        ).scalar()
        # A partially-bounded configuration (some enabled skills capped, one
        # not) is honestly unbounded — the ceiling is only real if EVERY
        # enabled skill carries one (UI-SPEC D4.2's "No ceiling" verdict).
        configured_max_single_action_cents = (
            None
            if (unbounded_single_count or 0) > 0 or configured_max_row is None
            else int(configured_max_row)
        )

        # --- configured hourly aggregate ceiling (per-skill ceiling x rate) --
        enabled_rows = db.execute(
            text(
                "SELECT rate_limit, constraints->>'max_amount_cents' AS max_amount_cents "
                "FROM capability_envelopes "
                "WHERE agent_id = :agent_id AND enabled = true"
            ),
            {"agent_id": agent_id},
        ).fetchall()
        enabled_skill_count = len(enabled_rows)

        hourly_total = 0.0
        hourly_unbounded = False
        for rate_limit, max_amount_cents in enabled_rows:
            if rate_limit is None or max_amount_cents is None:
                hourly_unbounded = True
                break
            parsed = _parse_rate_limit(rate_limit)
            if parsed is None:
                hourly_unbounded = True
                break
            max_calls, window_secs = parsed
            calls_per_hour = (max_calls * 3600) / window_secs
            hourly_total += int(max_amount_cents) * calls_per_hour
        # Round up so the figure never understates authorized exposure.
        configured_max_hourly_aggregate_cents = (
            None if hourly_unbounded else math.ceil(hourly_total)
        )

        # --- observed single-action maximum (history, trailing window) ------
        observed_single_row = db.execute(
            text(
                "SELECT MAX(COALESCE((arguments->>'amount_cents')::int, "
                "(arguments->>'refund_amount_cents')::int)) "
                "FROM tool_calls_audit "
                "WHERE agent_id = :agent_id AND error IS NULL "
                "AND created_at > now() - (:window_days::text || ' days')::interval"
            ),
            {"agent_id": agent_id, "window_days": observed_window_days},
        ).scalar()
        observed_max_single_action_cents = (
            int(observed_single_row) if observed_single_row is not None else None
        )

        # --- observed hourly aggregate maximum (max over hour buckets) ------
        observed_hourly_row = db.execute(
            text(
                "SELECT MAX(hourly_total) FROM ("
                "  SELECT SUM(COALESCE((arguments->>'amount_cents')::int, "
                "  (arguments->>'refund_amount_cents')::int, 0)) AS hourly_total "
                "  FROM tool_calls_audit "
                "  WHERE agent_id = :agent_id AND error IS NULL "
                "  AND created_at > now() - (:window_days::text || ' days')::interval "
                "  GROUP BY date_trunc('hour', created_at)"
                ") hourly_buckets"
            ),
            {"agent_id": agent_id, "window_days": observed_window_days},
        ).scalar()
        observed_max_hourly_aggregate_cents = (
            int(observed_hourly_row) if observed_hourly_row is not None else None
        )

    warn_threshold_single_cents, warn_threshold_hourly_cents = (
        _resolve_blast_radius_thresholds(agent_id)
    )

    return {
        "configured_max_single_action_cents": configured_max_single_action_cents,
        "configured_max_hourly_aggregate_cents": configured_max_hourly_aggregate_cents,
        "observed_max_single_action_cents": observed_max_single_action_cents,
        "observed_max_hourly_aggregate_cents": observed_max_hourly_aggregate_cents,
        "observed_window_days": observed_window_days,
        "warn_threshold_single_cents": warn_threshold_single_cents,
        "warn_threshold_hourly_cents": warn_threshold_hourly_cents,
        "enabled_skill_count": enabled_skill_count,
    }


def derive_blast_radius_warnings(blast_radius: dict) -> list[DeploymentWarning]:
    """Derive blast-radius warnings deterministically in Python (OD-1b).

    Pure function — no DB, no LLM. Warnings are derived from CONFIGURED
    values only. A figure describing history (what has actually happened)
    is not the same claim as current authorization, and warning on it would
    ask the owner to acknowledge a risk they may already have tightened
    away — so this function reads only the two "configured_max_*" keys and
    never touches the historical-maximum keys blast_radius also carries.

    Args:
        blast_radius: the dict returned by _fetch_blast_radius_sync (or the
            BLAST_RADIUS_DEFAULT_SIGNAL fallback).

    Returns:
        A list of DeploymentWarning, empty when enabled_skill_count == 0 —
        an agent with no enabled transactional skill has no blast radius to
        warn about.
    """
    if blast_radius.get("enabled_skill_count", 0) == 0:
        return []

    warnings: list[DeploymentWarning] = []

    configured_single = blast_radius.get("configured_max_single_action_cents")
    configured_hourly = blast_radius.get("configured_max_hourly_aggregate_cents")
    warn_single = blast_radius.get("warn_threshold_single_cents")
    warn_hourly = blast_radius.get("warn_threshold_hourly_cents")

    if configured_single is None:
        warnings.append(
            DeploymentWarning(
                warning_id="blast_radius_no_ceiling_configured",
                category="financial_exposure",
                message=(
                    "This agent can move money but has no configured ceiling on "
                    "a single action - set a maximum amount for every enabled "
                    "transactional skill."
                ),
                severity_level="warning",
            )
        )
    elif warn_single is not None and configured_single > warn_single:
        warnings.append(
            DeploymentWarning(
                warning_id="blast_radius_single_action_above_threshold",
                category="financial_exposure",
                message=(
                    f"The configured ceiling for a single action is "
                    f"R{configured_single / 100:.2f}, above the "
                    f"R{warn_single / 100:.2f} warning threshold - review "
                    f"whether this limit is intentional."
                ),
                severity_level="warning",
            )
        )

    if (
        configured_hourly is not None
        and warn_hourly is not None
        and configured_hourly > warn_hourly
    ):
        warnings.append(
            DeploymentWarning(
                warning_id="blast_radius_hourly_aggregate_above_threshold",
                category="financial_exposure",
                message=(
                    f"The configured hourly exposure ceiling is "
                    f"R{configured_hourly / 100:.2f}, above the "
                    f"R{warn_hourly / 100:.2f} warning threshold - review "
                    f"whether this limit is intentional."
                ),
                severity_level="warning",
            )
        )

    return warnings


# ---------------------------------------------------------------------------
# Agent SDK orchestrator loop
# ---------------------------------------------------------------------------


async def _run_orchestrator_loop(
    signals_json: str,
    result_container: dict,
) -> None:
    """Async Agent SDK loop that calls submit_report as a side-effect tool.

    Captures the first ToolUseBlock(name='submit_report') into result_container
    and returns immediately. No tool result is sent back to the agent
    (same side-effect pattern as report_finding in red_team_service.py).
    """
    options = ClaudeAgentOptions(
        model=SONNET_MODEL,
        system_prompt=_DEPLOYMENT_SYSTEM_PROMPT,
        max_turns=5,
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Here are the agent's quality signals:\n\n{signals_json}\n\n"
            "Assess deployment readiness and call submit_report."
        )
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock) and block.name == "submit_report":
                        result_container["report"] = block.input
                        # Side-effect only — no tool result sent back (same as report_finding)
                        return


def run_orchestrator(signals_json: str, result_container: dict) -> None:
    """Synchronous bridge called from the Celery task.

    Uses asyncio.run(asyncio.wait_for(..., timeout=120.0)) to guard against
    runaway Sonnet calls. Logs and swallows exceptions so the task can mark
    the run as failed rather than crashing the worker.
    """
    try:
        asyncio.run(
            asyncio.wait_for(
                _run_orchestrator_loop(signals_json, result_container),
                timeout=120.0,
            )
        )
    except Exception as exc:
        log.warning("deployment_orchestrator.failed", error=str(exc))
