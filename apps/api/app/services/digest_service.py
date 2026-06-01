"""
digest_service — Weekly digest stats collection and email dispatch (M10 OPS-02).

Collects four metrics per agent from the per-tenant Neon DB:
  1. conversation_count   — conversations in the last 7 days (tenant DB)
  2. faithfulness_score   — latest Ragas faithfulness from eval_results (tenant DB)
  3. critical_red_team_count — count of critical findings from latest red_team_run (tenant DB)
  4. escalation_count     — conversations flagged for escalation in last 7 days (tenant DB)

Email is plain-text, fire-and-forget (same pattern as escalation.py).
Connection strings NEVER logged (CTL-08).
"""
from __future__ import annotations

import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import psycopg2
import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)


def _collect_digest_stats(agent_id: str, conn_str: str, db) -> dict:
    """Collect 4 digest metrics from the tenant DB (conn_str).

    All four metrics are fetched from the per-tenant Neon DB via a single psycopg2
    connection. eval_runs, eval_results, red_team_runs, and conversations only exist
    in tenant DBs — NOT in the control DB. conn_str must NOT be logged (CTL-08).

    The `db` parameter (control DB session) is retained in the signature for
    backward-compat but is no longer used for data queries.
    """
    stats: dict = {
        "conversation_count": 0,
        "faithfulness_score": None,
        "critical_red_team_count": 0,
        "escalation_count": 0,
    }
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        try:
            # --- Tenant DB: latest faithfulness from eval_results joined to eval_runs ---
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT AVG(er.score)
                    FROM eval_results er
                    JOIN eval_runs r ON er.eval_run_id = r.id
                    WHERE r.kind = %s
                      AND r.status = 'complete'
                      AND er.metric = 'faithfulness'
                      AND r.started_at = (
                          SELECT MAX(started_at) FROM eval_runs
                          WHERE kind = %s AND status = 'complete'
                      )
                    """,
                    (f"m6:{agent_id}", f"m6:{agent_id}"),
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    stats["faithfulness_score"] = float(row[0])

            # --- Tenant DB: critical findings from latest red_team_run ---
            # Filter by kind = 'm7:{agent_id}' — no agent_id column in tenant schema
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT findings FROM red_team_runs
                    WHERE kind = %s
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (f"m7:{agent_id}",),
                )
                row = cur.fetchone()
                if row and row[0]:
                    findings = row[0] if isinstance(row[0], list) else []
                    stats["critical_red_team_count"] = sum(
                        1 for f in findings if f.get("severity") == "critical"
                    )

            # --- Tenant DB: 7d conversation and escalation counts ---
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM conversations WHERE created_at >= %s", (since,)
                )
                stats["conversation_count"] = int(cur.fetchone()[0] or 0)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM conversations "
                    "WHERE created_at >= %s AND metadata->>'escalated' = 'true'",
                    (since,),
                )
                stats["escalation_count"] = int(cur.fetchone()[0] or 0)

        finally:
            conn.close()
    except Exception as exc:
        log.warning("digest_service.tenant_fetch_failed", agent_id=agent_id, error=str(exc))

    return stats


def send_digest_email(agent_name: str, agent_id: str, stats: dict) -> None:
    """Send weekly digest email to OWNER_EMAIL. NEVER raises (fire-and-forget)."""
    if not settings.DIGEST_ENABLED:
        return
    if not all([settings.SMTP_HOST, settings.SMTP_FROM, settings.OWNER_EMAIL]):
        log.warning("digest_service.smtp_not_configured", agent_id=agent_id)
        return

    faithfulness = stats.get("faithfulness_score")
    faith_str = f"{faithfulness:.2f}" if faithfulness is not None else "no data"

    body = (
        f"Weekly Digest — {agent_name}\n"
        f"{'=' * 40}\n\n"
        f"Conversations (7 days): {stats['conversation_count']}\n"
        f"Escalations (7 days):   {stats['escalation_count']}\n"
        f"Latest faithfulness:    {faith_str}\n"
        f"Critical red team hits: {stats['critical_red_team_count']}\n\n"
        f"Review your agent at your W Chats dashboard.\n"
    )
    msg = MIMEText(body)
    msg["Subject"] = f"[W Chats] Weekly Digest: {agent_name}"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = settings.OWNER_EMAIL

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT or 587, timeout=5) as server:
            server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, [settings.OWNER_EMAIL], msg.as_string())
        log.info("digest_service.email_sent", agent_id=agent_id, to=settings.OWNER_EMAIL)
    except Exception as exc:
        log.warning("digest_service.email_failed", agent_id=agent_id, error=str(exc))
