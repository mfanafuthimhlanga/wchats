"""
digest_service — Weekly digest stats collection and email dispatch (M10 OPS-02).

Collects four metrics per agent from the per-tenant Neon DB:
  1. conversation_count      conversations in the last 7 days (tenant DB)
  2. faithfulness_score      the latest finished run's own record (tenant DB)
  3. critical_red_team_count critical findings on the latest red_team_run (tenant DB)
  4. escalation_count        conversations flagged for escalation, last 7 days (tenant DB)

WHERE "LATEST FAITHFULNESS" COMES FROM (#51 slice 4). It used to be
`AVG(er.score)` over the latest complete run's `eval_results` rows, in this
module's own copy of `alert_service`'s query, and it pooled the golden and
exploratory halves into one mean. It is now lifted off `eval_runs.result`
through `eval_service.latest_faithfulness_by_dataset`, the same rule the alert and the
deploy gate and the alert read. The digest names the dataset it quotes, and says
so when the run measured nothing.

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
from app.services.eval_service import latest_faithfulness_by_dataset

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
        # None, never 0.0. A faithfulness of zero would be a catastrophic
        # measurement; no faithfulness at all is the absence of one, and the
        # email says "not measured" rather than printing a number nobody read.
        "faithfulness_score": None,
        # Which half of the run the score belongs to, null when there is none.
        "faithfulness_dataset": None,
        "faithfulness_by_dataset": {},
        "critical_red_team_count": 0,
        "escalation_count": 0,
    }
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        try:
            # --- Tenant DB: the latest complete run's own faithfulness ---
            # Each dataset that scored is read under its own name and never
            # pooled: a golden set at 0.42 beside an exploratory sample at 0.88
            # is two readings, not a 0.65. One scoring dataset keeps the flat
            # pair the mail has always carried; two or more leave the pair
            # empty and fill the per dataset map, and the mail names each.
            with conn.cursor() as cur:
                by_dataset = latest_faithfulness_by_dataset(cur, agent_id)
                stats["faithfulness_by_dataset"] = by_dataset
                if len(by_dataset) == 1:
                    (dataset, value), = by_dataset.items()
                    stats["faithfulness_score"] = value
                    stats["faithfulness_dataset"] = dataset

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


def _render_digest_body(agent_name: str, stats: dict) -> str:
    """The mail's text, from the stats alone, so a test can read it without SMTP."""
    faithfulness = stats.get("faithfulness_score")
    dataset = stats.get("faithfulness_dataset")
    # "not measured" rather than "no data": the run may well have happened and
    # scored other dimensions. Naming the dataset matters because the golden set
    # and the exploratory draw answer different questions, and a reader comparing
    # this week's number to last week's needs to know they are the same question.
    by_dataset = stats.get("faithfulness_by_dataset") or {}
    if len(by_dataset) >= 2:
        faith_str = ", ".join(
            f"{by_dataset[name]:.2f} ({name} set)" for name in sorted(by_dataset)
        )
    elif faithfulness is None:
        faith_str = "not measured"
    elif dataset:
        faith_str = f"{faithfulness:.2f} ({dataset} set)"
    else:
        faith_str = f"{faithfulness:.2f}"

    body = (
        f"Weekly Digest — {agent_name}\n"
        f"{'=' * 40}\n\n"
        f"Conversations (7 days): {stats['conversation_count']}\n"
        f"Escalations (7 days):   {stats['escalation_count']}\n"
        f"Latest faithfulness:    {faith_str}\n"
        f"Critical red team hits: {stats['critical_red_team_count']}\n\n"
        f"Review your agent at your W Chats dashboard.\n"
    )
    return body


def send_digest_email(agent_name: str, agent_id: str, stats: dict) -> None:
    """Send weekly digest email to OWNER_EMAIL. NEVER raises (fire-and-forget)."""
    if not settings.DIGEST_ENABLED:
        return
    # Bound to locals so the narrowing survives for the type checker — mypy cannot
    # narrow through all([...]), and these are Optional[str] on Settings.
    smtp_host = settings.SMTP_HOST
    smtp_from = settings.SMTP_FROM
    owner_email = settings.OWNER_EMAIL
    if not smtp_host or not smtp_from or not owner_email:
        log.warning("digest_service.smtp_not_configured", agent_id=agent_id)
        return

    body = _render_digest_body(agent_name, stats)
    msg = MIMEText(body)
    msg["Subject"] = f"[W Chats] Weekly Digest: {agent_name}"
    msg["From"] = smtp_from
    msg["To"] = owner_email

    try:
        with smtplib.SMTP(smtp_host, settings.SMTP_PORT or 587, timeout=5) as server:
            server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(smtp_from, [owner_email], msg.as_string())
        log.info("digest_service.email_sent", agent_id=agent_id, to=owner_email)
    except Exception as exc:
        log.warning("digest_service.email_failed", agent_id=agent_id, error=str(exc))
