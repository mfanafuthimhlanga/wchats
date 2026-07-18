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
from typing import Literal

import psycopg2
import structlog
from pydantic import BaseModel
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    ToolUseBlock,
)
from app.core.config import settings

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

Write the summary for a non-technical business owner — no jargon, 2-3 sentences.
List each concern as a warning with a unique warning_id slug.
Call submit_report exactly once with your assessment.
"""


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
