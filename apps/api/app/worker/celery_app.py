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

worker_pool = "solo" (Windows fix):
    The billiard prefork pool (Celery's default) has a Windows bug where two
    child processes share the same pipe_handle. When select.select() is called
    on that handle, it returns an empty sequence instead of the expected
    (readable, writable, exceptional) 3-tuple, raising:
        ValueError: not enough values to unpack (expected 3, got 0)
    This manifests as a FAILURE result with null traceback for any task
    (including provision_neon) picked up from Redis. The solo pool runs tasks
    in the worker's main process with no subprocess spawning, eliminating the
    race condition. For local dev (one worker process per queue) there is no
    concurrency loss.
"""

import ssl

import structlog
from celery import Celery, signals
from kombu import Exchange, Queue

from app.core.config import settings

# ---------------------------------------------------------------------------
# Celery application instance
# ---------------------------------------------------------------------------

celery_app = Celery("veridian")

_redis_url = settings.REDIS_URL
# Strip ssl_cert_reqs from URL — configured explicitly via broker_use_ssl /
# redis_backend_use_ssl so Celery 5.x receives the Python ssl constant, not a string.
_redis_url_clean = _redis_url.split("?")[0] if "?" in _redis_url else _redis_url
_ssl_opts = (
    {"ssl_cert_reqs": ssl.CERT_NONE} if _redis_url_clean.startswith("rediss://") else None
)

celery_app.conf.update(
    # Broker and result backend — both point to Redis
    broker_url=_redis_url_clean,
    result_backend=_redis_url_clean,
    **({"broker_use_ssl": _ssl_opts, "redis_backend_use_ssl": _ssl_opts} if _ssl_opts else {}),

    # --- Task autodiscovery -----------------------------------------------
    # Worker discovers tasks by importing these modules on startup.
    # M1 entries (provision, migrations) listed first; M2 entries appended.
    include=[
        "app.worker.tasks.pipeline.provision",
        "app.worker.tasks.pipeline.migrations",
        "app.worker.tasks.pipeline.parse",
        "app.worker.tasks.pipeline.chunk",
        "app.worker.tasks.pipeline.metadata",
        "app.worker.tasks.pipeline.embed",
        # M3: hybrid retrieval task (runtime queue)
        "app.worker.tasks.runtime.retrieve",
        # M4: agent turn task (runtime queue)
        "app.worker.tasks.runtime.agent",
        # M5: validation chain (Gatekeeper, Auditor, Strategist)
        "app.worker.tasks.runtime.validators",
    ],

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
    # Result expiry (F5: broker now carries PII — purge results after 5 min)
    result_expires=300,

    # --- Serialization (T-02-04: no pickle) ----------------------------
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # --- Timezone -------------------------------------------------------
    timezone="UTC",
    enable_utc=True,

    # --- Worker pool (Windows billiard fix) -----------------------------
    # The billiard prefork pool raises "ValueError: not enough values to
    # unpack (expected 3, got 0)" on Windows when two children share the
    # same pipe_handle (billiard issue #299). The solo pool runs tasks in
    # the worker main process — no subprocess spawning, no pipe race.
    # For local dev this has no concurrency penalty (one worker per queue).
    # CLI flag --pool=solo in start_native.ps1 is kept as an explicit
    # override; this setting makes it the default so plain
    # `celery -A app.worker.celery_app worker` also works on Windows.
    worker_pool="solo",
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
    # task.request is None in CELERY_TASK_ALWAYS_EAGER mode (no message envelope).
    # Guard against AttributeError when running integration tests in eager mode.
    _request = task.request or {}
    structlog.contextvars.bind_contextvars(
        task_id=task_id,
        task_name=task.name,
        # request_id is propagated from FastAPI via Celery task headers
        request_id=(_request.get("headers", {}) or {}).get("request_id", ""),
    )
