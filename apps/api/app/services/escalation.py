"""
escalation — Fire-and-forget email notification for customer escalations.

Design: NEVER raises. If SMTP is not configured, logs a warning and returns.
If SMTP send fails, logs a warning and returns. This function is called from
within a Celery task as the notify_fn `agent_loop.build_agent_turn` binds; any
exception here would abort the agent turn incorrectly.

Threat context (T-04-03-03):
    Escalation suppression via prompt injection is mitigated by detecting
    escalation from ToolUseBlock evidence in the stream (not from agent prose).
    This function is only called when the SDK reports an escalate_to_human
    ToolUseBlock — not when the agent says "I am escalating" in text.
"""

import smtplib
from email.mime.text import MIMEText

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)


def send_escalation_email(agent, reason: str, context: str) -> None:
    """Send an escalation notification email to the owner (fire-and-forget).

    Called as a notify_fn callback from within a running Celery task.
    NEVER raises — any failure is logged as a WARNING and the task continues.

    Args:
        agent:   Agent ORM model or compatible duck-type with .id and .name attrs.
        reason:  Brief escalation reason string (from escalate_to_human tool input).
        context: Conversation context summary (from escalate_to_human tool input).

    Returns:
        None — always.

    Side-effects:
        - Sends one SMTP email when all SMTP env vars are configured.
        - Logs structlog WARNING when SMTP not configured or SMTP call fails.
    """
    # Guard: all three SMTP fields must be non-None and non-empty.
    # Bound to locals so the narrowing survives for the type checker — mypy cannot
    # narrow through all([...]), and these are Optional[str] on Settings.
    smtp_host = settings.SMTP_HOST
    smtp_from = settings.SMTP_FROM
    owner_email = settings.OWNER_EMAIL
    if not smtp_host or not smtp_from or not owner_email:
        log.warning(
            "escalation.email_not_configured",
            agent_id=str(getattr(agent, "id", "")),
            reason=reason,
        )
        return

    # Build MIME message.
    body = (
        f"Conversation escalated.\n"
        f"Agent: {agent.name}\n"
        f"Reason: {reason}\n\n"
        f"Context:\n{context}"
    )
    msg = MIMEText(body)
    msg["Subject"] = f"[W Chats] Escalation: {agent.name}"
    msg["From"] = smtp_from
    msg["To"] = owner_email

    try:
        with smtplib.SMTP(smtp_host, settings.SMTP_PORT or 587, timeout=5) as server:
            server.starttls()
            server.sendmail(smtp_from, [owner_email], msg.as_string())
        log.info(
            "escalation.email_sent",
            agent_id=str(getattr(agent, "id", "")),
            to=owner_email,
        )
    except Exception as exc:
        # Fire-and-forget: log warning but NEVER re-raise (T-04-03-03).
        log.warning(
            "escalation.email_failed",
            error=str(exc),
            agent_id=str(getattr(agent, "id", "")),
        )
