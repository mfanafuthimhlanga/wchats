"""
alert_service — Metric threshold evaluation and alert dispatch (M10 OPS-01, OPS-04).

Evaluates two conditions per agent:
  1. eval_regression: latest faithfulness < ALERT_FAITHFULNESS_THRESHOLD
  2. red_team_critical: critical findings >= ALERT_RED_TEAM_CRITICAL_COUNT

Writes new Alert rows to control DB. Skips if active alert of same type exists.
Sends plain-text email notification after each new alert (fire-and-forget).
"""
from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText

import psycopg2
import structlog
from sqlalchemy import text

from app.core.config import settings
from app.models.alert import Alert

log = structlog.get_logger(__name__)


def _get_latest_faithfulness(agent_id: str, conn_str: str) -> float | None:
    """Fetch latest faithfulness score from the TENANT DB eval_results table.

    Must query the tenant DB (not control DB) — eval_runs/eval_results only
    exist in per-tenant Neon DBs. conn_str must NOT be logged (CTL-08).
    """
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        try:
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
                return float(row[0]) if row and row[0] is not None else None
        finally:
            conn.close()
    except Exception as exc:
        log.warning("alert_service.faithfulness_fetch_failed", agent_id=agent_id, error=str(exc))
        return None


def _get_latest_critical_count(agent_id: str, conn_str: str) -> int:
    """Fetch critical red team finding count from the TENANT DB red_team_runs table.

    Must query the tenant DB — red_team_runs only exists in per-tenant Neon DBs.
    Filters by kind = 'm7:{agent_id}' (no agent_id column in the tenant schema).
    conn_str must NOT be logged (CTL-08).
    """
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        try:
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
                    return sum(1 for f in findings if f.get("severity") == "critical")
                return 0
        finally:
            conn.close()
    except Exception as exc:
        log.warning("alert_service.critical_count_fetch_failed", agent_id=agent_id, error=str(exc))
        return 0


def _active_alert_exists(agent_id: str, alert_type: str, db) -> bool:
    row = db.execute(
        text(
            "SELECT id FROM alerts WHERE agent_id = :agent_id "
            "AND alert_type = :alert_type AND resolved_at IS NULL LIMIT 1"
        ),
        {"agent_id": agent_id, "alert_type": alert_type},
    ).fetchone()
    return row is not None


def _write_alert(
    agent_id: str, alert_type: str, severity: str, message: str, db, tenant_id: str | None = None
) -> Alert:
    alert = Alert(
        agent_id=agent_id,
        alert_type=alert_type,
        severity=severity,
        message=message,
        triggered_at=datetime.now(timezone.utc),
        tenant_id=tenant_id,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def send_alert_email(agent_name: str, agent_id: str, alert_type: str, message: str) -> None:
    """Send alert notification email. NEVER raises."""
    # Bound to locals so the narrowing survives for the type checker — mypy cannot
    # narrow through all([...]), and these are Optional[str] on Settings.
    smtp_host = settings.SMTP_HOST
    smtp_from = settings.SMTP_FROM
    owner_email = settings.OWNER_EMAIL
    if not smtp_host or not smtp_from or not owner_email:
        log.warning("alert_service.smtp_not_configured", agent_id=agent_id)
        return
    subject_map = {
        "eval_regression": "Eval Regression Detected",
        "red_team_critical": "Critical Red Team Finding",
    }
    subject = f"[W Chats] {subject_map.get(alert_type, 'Alert')}: {agent_name}"
    msg = MIMEText(f"Alert for agent: {agent_name}\n\n{message}\n")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = owner_email
    try:
        with smtplib.SMTP(smtp_host, settings.SMTP_PORT or 587, timeout=5) as server:
            server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(smtp_from, [owner_email], msg.as_string())
        log.info("alert_service.email_sent", agent_id=agent_id, alert_type=alert_type)
    except Exception as exc:
        log.warning("alert_service.email_failed", agent_id=agent_id, error=str(exc))


def check_and_write_alerts(
    agent_id: str,
    conn_str: str | None = None,
    faithfulness: float | None = None,
    critical_red_team_count: int | None = None,
    agent_name: str = "",
    tenant_id: str | None = None,
    db=None,
) -> list[Alert]:
    """Evaluate thresholds and write new alerts. Returns list of newly created alerts.

    conn_str: decrypted tenant DB connection string; required for DB-path (when db is not None
    and pre-computed values are not supplied). Must NOT be logged (CTL-08).
    """
    new_alerts: list[Alert] = []

    # Resolve values from tenant DB if not passed directly.
    # conn_str is required when db is not None and values are not pre-supplied.
    if db is not None and faithfulness is None and conn_str:
        faithfulness = _get_latest_faithfulness(agent_id, conn_str)
    if db is not None and critical_red_team_count is None and conn_str:
        critical_red_team_count = _get_latest_critical_count(agent_id, conn_str)

    # Check eval regression — email fires only after successful DB commit (CR-02)
    if faithfulness is not None and faithfulness < settings.ALERT_FAITHFULNESS_THRESHOLD:
        if db is None or not _active_alert_exists(agent_id, "eval_regression", db):
            msg = f"Faithfulness {faithfulness:.2f} is below threshold {settings.ALERT_FAITHFULNESS_THRESHOLD}."
            if db is not None:
                alert = _write_alert(agent_id, "eval_regression", "warning", msg, db, tenant_id=tenant_id)
                new_alerts.append(alert)
                # Email only after row is committed (inside _write_alert above)
                send_alert_email(agent_name, agent_id, "eval_regression", msg)
            else:
                # Test path: no DB, send email directly
                send_alert_email(agent_name, agent_id, "eval_regression", msg)

    # Check red team critical — email fires only after successful DB commit (CR-02)
    if critical_red_team_count is not None and critical_red_team_count >= settings.ALERT_RED_TEAM_CRITICAL_COUNT:
        if db is None or not _active_alert_exists(agent_id, "red_team_critical", db):
            msg = f"{critical_red_team_count} critical red team finding(s) detected."
            if db is not None:
                alert = _write_alert(agent_id, "red_team_critical", "critical", msg, db, tenant_id=tenant_id)
                new_alerts.append(alert)
                # Email only after row is committed (inside _write_alert above)
                send_alert_email(agent_name, agent_id, "red_team_critical", msg)
            else:
                # Test path: no DB, send email directly
                send_alert_email(agent_name, agent_id, "red_team_critical", msg)

    return new_alerts
