"""
alert_service — Metric threshold evaluation and alert dispatch (M10 OPS-01, OPS-04).

Evaluates two conditions per agent:
  1. eval_regression: latest faithfulness < ALERT_FAITHFULNESS_THRESHOLD
  2. red_team_critical: critical findings >= ALERT_RED_TEAM_CRITICAL_COUNT

Writes new Alert rows to control DB. Skips if active alert of same type exists.
Sends plain-text email notification after each new alert (fire-and-forget).

WHERE "LATEST FAITHFULNESS" COMES FROM (#51 slice 4). It used to be
`AVG(er.score)` over the latest complete run's `eval_results` rows, joined here
in this module's own SQL. That was a fourth derivation of a number the run had
already computed, and it pooled the golden and exploratory halves into a single
mean. It is now lifted off `eval_runs.result`, the record `run_eval_suite`
writes once at the end of its own body. Nothing here averages anything.

AND IT IS READ PER DATASET (#175). `run_level_metrics` reports a run-level
faithfulness only when exactly one dataset scored, so a tenant who designated a
golden set has two measurements, no run-level number, and until this change no
eval_regression alert at all. Refusing to pool was right and dropping the alert
was not: `latest_faithfulness_by_dataset` returns each half under its own name,
the threshold is compared against each, and the message says which half fell and
by how much. A golden regression now fires whether or not the exploratory draw
moved with it.
"""
from __future__ import annotations

import smtplib
from collections.abc import Mapping
from datetime import datetime, timezone
from email.mime.text import MIMEText

import psycopg2
import structlog
from sqlalchemy import text

from app.core.config import settings
from app.core.log_bounds import log_failure
from app.models.alert import Alert
from app.services.eval_service import latest_faithfulness_by_dataset

log = structlog.get_logger(__name__)


def latest_faithfulness_readings(agent_id: str, conn_str: str) -> dict[str, float]:
    """The latest finished run's faithfulness on each dataset that measured it.

    `eval_service.latest_faithfulness_by_dataset` is the rule and carries the
    reasoning: every number is lifted off `eval_runs.result`, never averaged here,
    and the two halves are never pooled into one. This function only owns the
    connection.

    An empty dict on any failure, because an alert that cannot read a measurement
    must not invent one. Must query the tenant DB, not the control DB, because
    `eval_runs` only exists in per-tenant Neon DBs. conn_str must NOT be logged (CTL-08).
    """
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                return latest_faithfulness_by_dataset(cur, agent_id)
        finally:
            conn.close()
    except Exception as exc:
        log_failure(log, "alert_service.faithfulness_fetch_failed", exc, agent_id=agent_id)
        return {}


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
        log_failure(log, "alert_service.critical_count_fetch_failed", exc, agent_id=agent_id)
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
        log_failure(log, "alert_service.email_failed", exc, agent_id=agent_id)


#: The words the message uses for each dataset key, so an owner reading an email
#: is not handed the API's vocabulary.
_DATASET_WORDS = {"golden": "golden set", "exploratory": "exploratory sample"}


def eval_regression_message(faithfulness_by_dataset: Mapping[str, float]) -> str | None:
    """The message for every dataset below the threshold, or None when none is.

    EACH HALF IS ITS OWN COMPARISON (#175). A run with a golden set at 0.42 and an
    exploratory sample at 0.88 is a regression, and pooling the two to 0.65 would
    have hidden it just as surely as the old code's silence did. Every dataset
    that fell is named with its own number, so the reader can see which half
    moved.

    None over an empty mapping, which is what every absence reads as. An alert
    that cannot read a measurement must not fire and must not report a pass.
    """
    threshold = settings.ALERT_FAITHFULNESS_THRESHOLD
    below = {
        name: value
        for name, value in faithfulness_by_dataset.items()
        if value < threshold
    }
    if not below:
        return None
    named = ", ".join(
        f"{value:.2f} on the {_DATASET_WORDS.get(name, name)}"
        for name, value in sorted(below.items())
    )
    return f"Faithfulness is below threshold {threshold}: {named}."


def _raise_alert(
    agent_id: str,
    alert_type: str,
    severity: str,
    message: str,
    *,
    agent_name: str,
    tenant_id: str | None,
    db,
    new_alerts: list[Alert],
) -> None:
    """Write the row when no alert of this type is open, then email it.

    The email fires only after the row is committed (CR-02), which `_write_alert`
    does on the way out. The no-DB path is the test path: it skips the
    already-open check it cannot run and sends the email.
    """
    if db is None:
        send_alert_email(agent_name, agent_id, alert_type, message)
        return
    if _active_alert_exists(agent_id, alert_type, db):
        return
    new_alerts.append(
        _write_alert(agent_id, alert_type, severity, message, db, tenant_id=tenant_id)
    )
    send_alert_email(agent_name, agent_id, alert_type, message)


def check_and_write_alerts(
    agent_id: str,
    conn_str: str | None = None,
    faithfulness_by_dataset: Mapping[str, float] | None = None,
    critical_red_team_count: int | None = None,
    agent_name: str = "",
    tenant_id: str | None = None,
    db=None,
) -> list[Alert]:
    """Evaluate thresholds and write new alerts. Returns list of newly created alerts.

    faithfulness_by_dataset: dataset name to that dataset's faithfulness. A
    mapping rather than one float because a run whose two datasets both scored has
    two readings and no honest way to combine them (#175); an unnamed number was
    the shape that made the old signature refuse such a run outright.

    conn_str: decrypted tenant DB connection string; required for DB-path (when db is not None
    and pre-computed values are not supplied). Must NOT be logged (CTL-08).
    """
    new_alerts: list[Alert] = []

    # Resolve values from tenant DB if not passed directly.
    # conn_str is required when db is not None and values are not pre-supplied.
    if db is not None and faithfulness_by_dataset is None and conn_str:
        faithfulness_by_dataset = latest_faithfulness_readings(agent_id, conn_str)
    if db is not None and critical_red_team_count is None and conn_str:
        critical_red_team_count = _get_latest_critical_count(agent_id, conn_str)

    eval_message = eval_regression_message(faithfulness_by_dataset or {})
    if eval_message is not None:
        _raise_alert(
            agent_id,
            "eval_regression",
            "warning",
            eval_message,
            agent_name=agent_name,
            tenant_id=tenant_id,
            db=db,
            new_alerts=new_alerts,
        )

    if (
        critical_red_team_count is not None
        and critical_red_team_count >= settings.ALERT_RED_TEAM_CRITICAL_COUNT
    ):
        _raise_alert(
            agent_id,
            "red_team_critical",
            "critical",
            f"{critical_red_team_count} critical red team finding(s) detected.",
            agent_name=agent_name,
            tenant_id=tenant_id,
            db=db,
            new_alerts=new_alerts,
        )

    return new_alerts
