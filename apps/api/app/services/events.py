"""
Event emission helper for W Chats Celery tasks.

emit() is the single function called at every Celery task checkpoint.
Order (WR-05: publish before commit to prevent duplicate-event retries):
  1. Publishes a JSON message to the Redis pub/sub channel job_events:{job_id}
     (best-effort live SSE delivery — lossy but idempotent on retry)
  2. Inserts a row into the job_events table and commits (durable replay log)

emit_async() is the same event for a caller already on an event loop (#86).
Step 1 stays inline, because the live SSE delivery is the half a customer is
watching. Step 2 goes to a worker thread, because both halves are BLOCKING —
a sync Session and a sync Redis client are what a Celery task holds — and
run_agent_loop calls this twice per tool call from inside the async body the
customer is waiting on. A turn spending the retrieve cap of 8 is sixteen
control-DB round trips, and the control DB is Neon: a suspended endpoint takes
8 to 20 seconds to wake. #48 moved the tenant ledger writes off the loop for
exactly this reason and left these behind.

The row count does not change and neither does replay fidelity: every event is
still committed at the moment it happens, which is what the buffer-until-the-end
alternative would have traded away for a turn that dies mid-flight.

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

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

from redis import Redis as SyncRedis
from sqlalchemy.orm import Session

from app.models.job_event import JobEvent


def emit(
    job_id: UUID | str,
    event_type: str,
    payload: dict | None,
    db: Session,
    redis: SyncRedis,
) -> None:
    """Persist an event to job_events and publish it to the Redis pub/sub channel.

    Args:
        job_id:     Job id, as a UUID or as its canonical string form.
                    Both are accepted deliberately, not by accident: Celery task
                    arguments are JSON, so every worker task holds job_id as a
                    ``str`` (see the ``job_id: str`` parameter on every task in
                    app/worker/tasks/), while API-side callers hold the ORM
                    ``Job.id``, which is a ``UUID``.  Neither of the two uses
                    below is UUID-specific — the Redis channel is an f-string and
                    the job_events.job_id column is a SQLAlchemy Uuid, which
                    accepts the canonical string form — so widening the
                    annotation describes the real contract rather than papering
                    over a defect.  ``TestEmitAcceptsStringJobId`` pins the two
                    forms as producing identical output.
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
    event_payload = _stamped(payload)
    _publish(job_id, event_type, event_payload, redis)
    _persist(job_id, event_type, event_payload, db)


async def emit_async(
    job_id: UUID | str,
    event_type: str,
    payload: dict | None,
    db: Session,
    redis: SyncRedis,
) -> None:
    """emit() for a caller on an event loop: publish inline, persist in a thread.

    Same arguments, same row, same order. The only difference is which thread the
    job_events write runs on, and that is the whole of #86 — the live Redis
    publish stays on the caller's thread so the widget stream does not wait on a
    hop, and the blocking control-DB round trip does not sit on the loop.

    ``db`` is a sync Session and Sessions are not thread-safe, so this hands the
    thread one write and awaits it. The caller must not use ``db`` concurrently
    while this is in flight; run_agent_loop's tool calls are sequential awaits,
    which is what makes the handoff safe there.
    """
    event_payload = _stamped(payload)
    _publish(job_id, event_type, event_payload, redis)
    await asyncio.to_thread(_persist, job_id, event_type, event_payload, db)


def _stamped(payload: dict | None) -> dict:
    """A copy of the caller's payload carrying the UTC timestamp every consumer reads.

    A copy, because the caller's dict is theirs; None normalises to {}.
    """
    event_payload: dict = dict(payload) if payload else {}
    event_payload["at"] = datetime.now(timezone.utc).isoformat()
    return event_payload


def _publish(job_id: UUID | str, event_type: str, event_payload: dict, redis: SyncRedis) -> None:
    """Best-effort live delivery, and it runs FIRST (WR-05).

    If the publish raises, no DB commit has happened yet, so retrying the task
    cannot produce a duplicate job_events row. A Redis failure loses only the live
    broadcast; the durable row below still reaches late-join clients via replay.
    """
    redis.publish(
        f"job_events:{job_id}",
        json.dumps({"event_type": event_type, "payload": event_payload}),
    )


def _persist(job_id: UUID | str, event_type: str, event_payload: dict, db: Session) -> None:
    """The durable replay log for late-join SSE clients: one row, one commit."""
    db.add(JobEvent(job_id=job_id, event_type=event_type, payload=event_payload))
    db.commit()
