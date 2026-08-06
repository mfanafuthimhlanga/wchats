"""
check_index_staleness — Celery task (pipeline queue): scans documents/chunks/
embeddings for staleness + embedding-model drift (OPS-08).

Why pipeline, not runtime (Pitfall 6, T-21-04-02):
    This is a read-only scan, ingestion-adjacent (not a live agent-turn
    concern). Routing it to `runtime` would contend with live agent-turn
    traffic on the same worker under `worker_pool="solo"` in local dev.

Signals computed (compute_index_staleness_summary — a plain function, reused
by both this task AND GET /agents/{id}/retrieval-health for a live fallback
when no cached summary exists):
    stale documents — a document is "stale" when either:
        (a) it has chunks with no embedding row at all, or
        (b) documents.created_at is newer than the latest embedding's
            created_at among its chunks (source re-ingested/re-parsed after
            the existing embeddings were written).
    Honesty note: `documents` has no dedicated `updated_at` column (only
    `created_at`, set once at INSERT — confirmed against
    alembic_tenant/versions/0001, 0002). created_at is therefore used as the
    best-available "source last touched" proxy. This is documented here (not
    silently assumed) per the plan's own escape hatch: "where a staleness
    signal genuinely cannot be computed from existing columns, surface 'not
    tracked yet' — never fabricate."
    embedding-model drift — any embeddings.model value that differs from
    bedrock_embedding_service.active_embedding_model() (the same "target
    model" concept reembed_corpus already uses for its idempotency filter).

Idempotency (CLAUDE.md rule 5):
    The scan itself is a pure read — naturally idempotent, re-running yields
    the same summary for unchanged data. The side effect (raising an alert
    via alert_service) is deduplicated by alert_service._active_alert_exists
    (an unresolved alert of the same type is never duplicated), matching the
    existing eval_regression/red_team_critical alert flow (M10).

Security (CLAUDE.md rule 4): check_index_staleness takes only agent_id.
conn_str is decrypted at runtime from the control DB, never in task args.

Deviation note (see 21-04-SUMMARY.md): alerts.alert_type has a live CHECK
constraint (0012_alerts_digest_runs.py) restricted to ('eval_regression',
'red_team_critical') — the same class of landmine RESEARCH.md's Pitfall 2
documents for eval_scenarios.source. A new control-DB migration (0017) widens
this constraint to add 'index_staleness', mirroring that established
DROP/ADD CONSTRAINT convention. This migration file was not in this plan's
`files_modified` list — it was required to avoid a silent CheckViolation when
writing the new alert type (Rule 3: auto-fix blocking issue).
"""

from __future__ import annotations

import structlog
import psycopg2
from sqlalchemy import select

from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services import alert_service
from app.services import bedrock_embedding_service
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

NOT_TRACKED = "not_tracked"

_ALERT_TYPE = "index_staleness"


# ---------------------------------------------------------------------------
# Plain scan function — reused by the Celery task AND the retrieval-health
# route (metrics.py) for a live-computed fallback.
# ---------------------------------------------------------------------------


def compute_index_staleness_summary(conn_str: str) -> dict:
    """Scan documents/chunks/embeddings for staleness + model-drift signals.

    Read-only; never raises. Each of the two signals degrades independently
    to NOT_TRACKED on its own query failure (e.g. a column genuinely absent
    from an older tenant schema) rather than fabricating a value or failing
    the whole scan.

    Returns:
        {
            "stale_count": int | "not_tracked",
            "stale_document_ids": list[str] (capped at 20),
            "drift_detected": bool | "not_tracked",
            "drift_model_counts": dict[str, int] | "not_tracked",
            "current_embedding_model": str,
        }
    """
    target_model = bedrock_embedding_service.active_embedding_model()

    stale_count: int | str = NOT_TRACKED
    stale_document_ids: list[str] = []
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT d.id, d.created_at, COUNT(c.id) AS chunk_count,
                           COUNT(e.chunk_id) AS embedded_count, MAX(e.created_at) AS last_embed_at
                    FROM documents d
                    JOIN chunks c ON c.document_id = d.id
                    LEFT JOIN embeddings e ON e.chunk_id = c.id
                    GROUP BY d.id, d.created_at
                    """
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        stale_count = 0
        for doc_id, doc_created_at, chunk_count, embedded_count, last_embed_at in rows:
            is_stale = (embedded_count < chunk_count) or (
                last_embed_at is not None and doc_created_at > last_embed_at
            )
            if is_stale:
                stale_count += 1
                stale_document_ids.append(str(doc_id))
    except Exception as exc:  # noqa: BLE001 — degrade this signal to not_tracked, never fabricate
        log.warning("check_index_staleness.staleness_scan_failed", error=str(exc))
        stale_count = NOT_TRACKED
        stale_document_ids = []

    drift_detected: bool | str = NOT_TRACKED
    drift_model_counts: dict | str = NOT_TRACKED
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT model, COUNT(*) FROM embeddings GROUP BY model")
                rows = cur.fetchall()
        finally:
            conn.close()

        drift_model_counts = {model: count for model, count in rows}
        drift_detected = any(model != target_model for model in drift_model_counts)
    except Exception as exc:  # noqa: BLE001 — degrade this signal to not_tracked, never fabricate
        log.warning("check_index_staleness.drift_scan_failed", error=str(exc))
        drift_detected = NOT_TRACKED
        drift_model_counts = NOT_TRACKED

    return {
        "stale_count": stale_count,
        "stale_document_ids": stale_document_ids[:20],
        "drift_detected": drift_detected,
        "drift_model_counts": drift_model_counts,
        "current_embedding_model": target_model,
    }


def _raise_alerts_if_needed(db, agent, summary: dict) -> None:
    """Raise an `index_staleness` alert via the existing alert_service, deduped."""
    stale_count = summary.get("stale_count")
    drift_detected = summary.get("drift_detected")

    needs_alert = (isinstance(stale_count, int) and stale_count > 0) or drift_detected is True
    if not needs_alert:
        return

    if alert_service._active_alert_exists(str(agent.id), _ALERT_TYPE, db):
        return

    parts = []
    if isinstance(stale_count, int) and stale_count > 0:
        parts.append(f"{stale_count} document(s) have stale or missing embeddings")
    if drift_detected is True:
        parts.append("embedding-model drift detected (stale model versions in the index)")
    message = "; ".join(parts) + "."

    alert_service._write_alert(
        str(agent.id), _ALERT_TYPE, "warning", message, db, tenant_id=str(agent.tenant_id),
    )
    alert_service.send_alert_email(agent.name, str(agent.id), _ALERT_TYPE, message)


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="pipeline",
    name="app.worker.tasks.pipeline.staleness.check_index_staleness",
)
def check_index_staleness(self, agent_id: str) -> dict:  # noqa: ARG001
    """Per-agent index staleness + embedding-drift scan (OPS-08).

    Args:
        agent_id: UUID string. conn_str is decrypted at runtime from the
                  control DB — NEVER an argument (CLAUDE.md rule 4).

    Returns:
        {"agent_id": str, **compute_index_staleness_summary(...)}
        {} on agent-not-found or missing conn_str.
    """
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error("check_index_staleness.agent_not_found", agent_id=agent_id)
            return {}
        if not agent.neon_connection_string:
            log.info("check_index_staleness.no_conn_str", agent_id=agent_id)
            return {"agent_id": agent_id, "skipped": True, "reason": "no_conn_str"}

        conn_str = fernet_decrypt(agent.neon_connection_string)
        summary = compute_index_staleness_summary(conn_str)

        try:
            _raise_alerts_if_needed(db, agent, summary)
        except Exception as exc:  # noqa: BLE001 — alerting must never fail the scan
            log.warning("check_index_staleness.alert_failed", agent_id=agent_id, error=str(exc))

    log.info(
        "check_index_staleness.complete",
        agent_id=agent_id,
        stale_count=summary["stale_count"],
        drift_detected=summary["drift_detected"],
    )
    return {"agent_id": agent_id, **summary}


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=1,
    default_retry_delay=60,
    queue="pipeline",
    name="app.worker.tasks.pipeline.staleness.check_index_staleness_beat",
)
def check_index_staleness_beat(self) -> dict:  # noqa: ARG001
    """Beat-triggered: fan out check_index_staleness per deployed agent.

    Mirrors app.worker.tasks.runtime.alert.run_alert_check_beat's fan-out
    pattern exactly (same Agent.is_deployed filter, same dispatch shape).
    """
    with get_sync_db() as db:
        agents = db.execute(
            select(Agent).where(Agent.is_deployed == True)  # noqa: E712
        ).scalars().all()
    dispatched = 0
    for agent in agents:
        check_index_staleness.apply_async(kwargs={"agent_id": str(agent.id)}, queue="pipeline")
        dispatched += 1
    return {"dispatched": dispatched}
