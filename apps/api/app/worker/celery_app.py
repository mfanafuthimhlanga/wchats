"""
Celery application factory for Veridian.

Provides:
    celery_app — Celery instance with two queues (pipeline, runtime) and
                 global reliability settings.

Queue topology (CLAUDE.md rule — non-negotiable):
    pipeline  — ingestion/build tasks (provision_neon, apply_migrations)
    runtime   — eval, agent call tasks (idle in M1)

Reliability settings (CLAUDE.md rule — non-negotiable):
    task_acks_late=True              — message acknowledged AFTER task completes
    task_reject_on_worker_lost=True  — requeue on unexpected worker death (kill -9)

JSON serializer (Threat T-02-04):
    Pickle deserialization from untrusted Redis is an RCE vector.
    JSON enforced at all levels (task, result, accepted content).

structlog integration (RESEARCH.md §Pattern 10, Pitfall 6):
    task_prerun signal clears contextvars before each task so a previous task's
    request_id cannot bleed into the next task's log lines.
"""

import structlog
from celery import Celery, signals
from kombu import Exchange, Queue

from app.core.config import settings

# ---------------------------------------------------------------------------
# Celery application instance
# ---------------------------------------------------------------------------

celery_app = Celery("veridian")

celery_app.conf.update(
    # Broker and result backend — both point to Redis
    broker_url=settings.REDIS_URL,
    result_backend=settings.REDIS_URL,

    # --- Queue topology -------------------------------------------------
    # Two named queues with matching exchanges and routing keys.
    # Direct exchange: routing_key must match the queue name exactly.
    task_queues=(
        Queue(
            "pipeline",
            Exchange("pipeline", type="direct"),
            routing_key="pipeline",
        ),
        Queue(
            "runtime",
            Exchange("runtime", type="direct"),
            routing_key="runtime",
        ),
    ),
    # Default queue for tasks that don't match any route
    task_default_queue="runtime",

    # Explicit task routing — module path prefix → queue name
    # Any task under app.worker.tasks.pipeline.* goes to pipeline queue.
    # Any task under app.worker.tasks.runtime.* goes to runtime queue.
    task_routes={
        "app.worker.tasks.pipeline.*": {"queue": "pipeline"},
        "app.worker.tasks.runtime.*": {"queue": "runtime"},
    },

    # --- Reliability (CLAUDE.md: both are non-negotiable) ---------------
    # acks_late: Celery acknowledges the message AFTER the task function returns.
    # If the worker crashes mid-task, the message is redelivered.
    task_acks_late=True,
    # reject_on_worker_lost: If the worker process is killed (SIGKILL / kill -9),
    # the unacknowledged message is sent back to the queue rather than silently
    # dropped into the "unacked" state forever.
    task_reject_on_worker_lost=True,

    # --- Serialization (T-02-04: no pickle) ----------------------------
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # --- Timezone -------------------------------------------------------
    timezone="UTC",
    enable_utc=True,
)


# ---------------------------------------------------------------------------
# structlog context isolation between tasks (RESEARCH.md Pitfall 6)
# ---------------------------------------------------------------------------


@signals.task_prerun.connect
def on_task_prerun(sender, task_id, task, args, kwargs, **_):
    """Clear structlog contextvars before each task starts.

    Celery worker processes are persistent; contextvars from a previous task
    are NOT automatically cleared between invocations.  Without this handler,
    task B inherits the request_id from task A, producing misleading log lines.

    After clearing, bind the current task's identifiers so all log lines within
    the task automatically include task_id, task_name, and request_id.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        task_id=task_id,
        task_name=task.name,
        # request_id is propagated from FastAPI via Celery task headers
        request_id=task.request.get("headers", {}).get("request_id", ""),
    )
