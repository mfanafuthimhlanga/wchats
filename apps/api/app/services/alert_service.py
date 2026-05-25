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

import structlog
from sqlalchemy import text

from app.core.config import settings
from app.models.alert import Alert

log = structlog.get_logger(__name__)


def _get_latest_faithfulness(agent_id: str, db) -> float | None:
    row = db.execute(
        text(
            "SELECT aggregate_scores FROM eval_runs "
            "WHERE agent_id = :agent_id AND status = 'complete' "
            "ORDER BY started_at DESC LIMIT 1"
        ),
        {"agent_id": agent_id},
    ).fetchone()
    if row and row[0]:
        return row[0].get("faithfulness")
    return None


def _get_latest_critical_count(agent_id: str, db) -> int:
    row = db.execute(
        text(
            "SELECT findings FROM red_team_runs "
            "WHERE agent_id = :agent_id "
            "ORDER BY started_at DESC LIMIT 1"
        ),
        {"agent_id": agent_id},
    ).fetchone()
    if row and row[0]:
        return sum(1 for f in row[0] if f.get("severity") == "critical")
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


def _write_alert(agent_id: str, alert_type: str, severity: str, message: str, db) -> Alert:
    alert = Alert(
        agent_id=agent_id,
        alert_type=alert_type,
        severity=severity,
        message=message,
        triggered_at=datetime.now(timezone.utc),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def send_alert_email(agent_name: str, agent_id: str, alert_type: str, message: str) -> None:
    """Send alert notification email. NEVER raises."""
    if not all([settings.SMTP_HOST, settings.SMTP_FROM, settings.OWNER_EMAIL]):
        log.warning("alert_service.smtp_not_configured", agent_id=agent_id)
        return
    subject_map = {
        "eval_regression": "Eval Regression Detected",
        "red_team_critical": "Critical Red Team Finding",
    }
    subject = f"[Veridian] {subject_map.get(alert_type, 'Alert')}: {agent_name}"
    msg = MIMEText(f"Alert for agent: {agent_name}\n\n{message}\n")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = settings.OWNER_EMAIL
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT or 587, timeout=5) as server:
            server.starttls()
            server.sendmail(settings.SMTP_FROM, [settings.OWNER_EMAIL], msg.as_string())
        log.info("alert_service.email_sent", agent_id=agent_id, alert_type=alert_type)
    except Exception as exc:
        log.warning("alert_service.email_failed", agent_id=agent_id, error=str(exc))


def check_and_write_alerts(
    agent_id: str,
    faithfulness: float | None = None,
    critical_red_team_count: int | None = None,
    agent_name: str = "",
    db=None,
) -> list[Alert]:
    """Evaluate thresholds and write new alerts. Returns list of newly created alerts."""
    new_alerts: list[Alert] = []

    # Resolve values from DB if not passed directly (task path passes db; test path passes values)
    if db is not None and faithfulness is None:
        faithfulness = _get_latest_faithfulness(agent_id, db)
    if db is not None and critical_red_team_count is None:
        critical_red_team_count = _get_latest_critical_count(agent_id, db)

    # Check eval regression
    if faithfulness is not None and faithfulness < settings.ALERT_FAITHFULNESS_THRESHOLD:
        if db is None or not _active_alert_exists(agent_id, "eval_regression", db):
            msg = f"Faithfulness {faithfulness:.2f} is below threshold {settings.ALERT_FAITHFULNESS_THRESHOLD}."
            if db is not None:
                alert = _write_alert(agent_id, "eval_regression", "warning", msg, db)
                new_alerts.append(alert)
            send_alert_email(agent_name, agent_id, "eval_regression", msg)

    # Check red team critical
    if critical_red_team_count is not None and critical_red_team_count >= settings.ALERT_RED_TEAM_CRITICAL_COUNT:
        if db is None or not _active_alert_exists(agent_id, "red_team_critical", db):
            msg = f"{critical_red_team_count} critical red team finding(s) detected."
            if db is not None:
                alert = _write_alert(agent_id, "red_team_critical", "critical", msg, db)
                new_alerts.append(alert)
            send_alert_email(agent_name, agent_id, "red_team_critical", msg)

    return new_alerts
