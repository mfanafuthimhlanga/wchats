"""
Event emission helper for Veridian Celery tasks.

emit() is the single function called at every Celery task checkpoint.
Order (WR-05: publish before commit to prevent duplicate-event retries):
  1. Publishes a JSON message to the Redis pub/sub channel job_events:{job_id}
     (best-effort live SSE delivery — lossy but idempotent on retry)
  2. Inserts a row into the job_events table and commits (durable replay log)

Design decisions:
  - Accepts db (Session) and redis (SyncRedis) as arguments — callers own the
    session lifecycle and tests inject mocks.  emit() never creates its own
    Session or Redis connection (no get_sync_db() or create_engine() here).
  - Sync Session only — Celery workers are sync processes; using asyncio inside
    a Celery task blocks the entire worker (see RESEARCH.md Anti-Patterns).
  - Copies the caller's payload dict before mutation; never mutates the caller's
    original dict.
  - Adds "at" ISO UTC timestamp to every payload so SSE consumers have a
    reliable ordering timestamp independent of DB clock.

Threat context:
  T-02-03: M1 payloads contain only event metadata (job_id, at, project_id);
            no PII or credentials flow through this function.
"""

import json
from datetime import datetime, timezone
from uuid import UUID

from redis import Redis as SyncRedis
from sqlalchemy.orm import Session

from app.models.job_event import JobEvent


def emit(
    job_id: UUID,
    event_type: str,
    payload: dict | None,
    db: Session,
    redis: SyncRedis,
) -> None:
    """Persist an event to job_events and publish it to the Redis pub/sub channel.

    Args:
        job_id:     UUID of the job this event belongs to.
        event_type: Event name string (e.g. "job.started", "neon.project.ready").
        payload:    Arbitrary metadata dict.  None is treated as {}.
                    emit() works on a *copy* — the caller's dict is never mutated.
        db:         SQLAlchemy sync Session.  Caller is responsible for its lifecycle.
        redis:      Sync Redis client.  Caller is responsible for its lifecycle.

    Returns:
        None

    Side-effects:
        - Publishes one message to channel "job_events:{job_id}" (best-effort).
        - Inserts one row into job_events and commits (durable).
        - Adds "at" (UTC ISO 8601 string) to the payload copy before persist/publish.
    """
    # 1. Copy payload to avoid mutating the caller's dict; normalise None → {}
    event_payload: dict = dict(payload) if payload else {}

    # 2. Inject UTC ISO timestamp — required by every downstream consumer
    event_payload["at"] = datetime.now(timezone.utc).isoformat()

    # 3. Build the serialised message used by both Redis and the DB row
    message = json.dumps({"event_type": event_type, "payload": event_payload})

    # 4. Publish to Redis FIRST (best-effort live delivery) — before the DB commit.
    #    If Redis publish raises here, no DB commit has happened yet, so retrying
    #    the task won't produce a duplicate job_events row.  A Redis failure loses
    #    only the live broadcast; the DB commit below still creates the durable record
    #    so late-join clients will receive the event via replay.  (WR-05)
    redis.publish(f"job_events:{job_id}", message)

    # 5. Persist to job_events (durable replay log for late-join SSE clients)
    event = JobEvent(
        job_id=job_id,
        event_type=event_type,
        payload=event_payload,
    )
    db.add(event)
    db.commit()
