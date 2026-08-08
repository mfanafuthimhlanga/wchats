"""
Celery application factory for W Chats.

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

worker_pool — ENVIRONMENT-conditional (PROD-15 / Landmine 3):
    "solo" on development / test (Windows billiard fix):
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

    "prefork" on production (Linux Fargate):
        On Linux, prefork is safe and correct. The Fargate runtime worker CMD
        passes --pool=prefork --concurrency=2 explicitly, which overrides this
        setting. The ENVIRONMENT-conditional default ensures the config reflects
        the correct pool for each environment, not just the CMD override.

    Note: the --pool CLI flag passed in the Fargate task definition CMD is the
    authoritative override; this setting is the application-level default.
"""

import socket
import ssl

import structlog
from celery import Celery, signals
from celery.schedules import crontab
from kombu import Exchange, Queue

from app.core.config import settings

# ---------------------------------------------------------------------------
# How long the broker waits before deciding a delivered message was lost
# ---------------------------------------------------------------------------
# THE LONGEST TASK IN THIS SYSTEM IS NO LONGER PROVISIONING. It was 3600 s with
# a comment reasoning about "provision + migrations can take ~60 s". D1/P2 made
# `run_eval_suite` invoke the customer agent once per scenario: sixty turns at a
# 90 s ceiling is 5400 s of worst case, which the run STAMPS ON ITSELF as
# `max_wall_clock_s`. A run that actually consumes the bound it advertises was
# therefore redelivered at 60 minutes and a second worker began running the same
# agent concurrently — the run's own record describing a bound the broker would
# not let it reach.
#
# Deliberately NOT imported from eval_service: that module pulls ragas,
# instructor and anthropic at import time, and celery_app is imported by every
# task module and by the API process. The relation is pinned by a test instead —
# tests/unit/test_eval_agent_invocation.py asserts this exceeds
# AGENT_INVOCATION_MAX_CALLS_PER_RUN x AGENT_TURN_TIMEOUT_S, so the two cannot
# drift apart silently the way a copied number would.
BROKER_VISIBILITY_TIMEOUT_S = 7200

# ---------------------------------------------------------------------------
# Celery application instance
# ---------------------------------------------------------------------------

celery_app = Celery("wchats")

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
        # P13-04: one-time per-tenant re-embed / backfill to Bedrock Titan v2 (PROD-06)
        "app.worker.tasks.pipeline.reembed",
        # M3: hybrid retrieval task (runtime queue)
        "app.worker.tasks.runtime.retrieve",
        # M4: agent turn task (runtime queue)
        "app.worker.tasks.runtime.agent",
        # M5: validation chain (Gatekeeper, Auditor, Strategist)
        "app.worker.tasks.runtime.validators",
        # M6: eval suite + scenario mining tasks (runtime queue)
        "app.worker.tasks.runtime.eval",
        # M7: red team tasks (runtime queue)
        "app.worker.tasks.runtime.red_team",
        # M8: deployment checklist task (runtime queue)
        "app.worker.tasks.runtime.deployment",
        # M9: retrieval strategy synthesis (pipeline queue)
        "app.worker.tasks.pipeline.strategy",
        # M10: weekly digest (runtime queue)
        "app.worker.tasks.runtime.digest",
        # M10: daily alert check (runtime queue)
        "app.worker.tasks.runtime.alert",
        # Phase 21 (OPS-07): sampled retrieval faithfulness task (runtime queue)
        "app.worker.tasks.runtime.retrieval_eval",
        # Phase 21 (OPS-08): index staleness / embedding-drift scan (pipeline queue)
        "app.worker.tasks.pipeline.staleness",
        # Phase 21 (OPS-11): promote-trace-to-scenario flywheel task (runtime queue)
        "app.worker.tasks.runtime.bench",
        # Phase 22 (ACT-07): pending-confirmation resolver execution task (runtime queue)
        "app.worker.tasks.runtime.confirmations",
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

    # --- Broker transport options (Upstash idle-connection fix) ----------
    # Upstash Redis (TLS) drops idle TCP connections after ~10 minutes with no
    # FIN — the worker's BLPOP socket becomes half-open. Workers appear alive
    # in the process list but receive zero tasks. socket_keepalive enables TCP
    # keepalive probes; socket_timeout causes a blocked socket op to raise after
    # 30 s so Kombu reconnects. retry_on_timeout retries BLPOP instead of
    # propagating the timeout exception. visibility_timeout must exceed the
    # longest expected task runtime — see BROKER_VISIBILITY_TIMEOUT_S above, which
    # is no longer about provisioning. On Windows, TCP_KEEPIDLE/INTVL/CNT are set
    # at the OS level and socket_keepalive_options is ignored — but
    # socket_keepalive=True and retry_on_timeout=True still apply.
    broker_transport_options={
        "socket_timeout": 30,
        "socket_connect_timeout": 10,
        "socket_keepalive": True,
        "socket_keepalive_options": {
            k: v
            for k, v in [
                (getattr(socket, "TCP_KEEPIDLE", None), 60),
                (getattr(socket, "TCP_KEEPINTVL", None), 10),
                (getattr(socket, "TCP_KEEPCNT", None), 5),
            ]
            if k is not None
        },
        "visibility_timeout": BROKER_VISIBILITY_TIMEOUT_S,
        "retry_on_timeout": True,
    },

    # --- Celery beat schedule (M6: nightly eval) ------------------------
    # D-19 LOCKED: 'eval-nightly' runs run_eval_suite_beat at 02:00 UTC daily.
    # D-20 LOCKED: the beat task receives agent_id (not a connection string).
    # Run the beat process separately:
    #   celery -A app.worker.celery_app beat --loglevel=info
    beat_schedule={
        "eval-nightly": {
            "task": "app.worker.tasks.runtime.eval.run_eval_suite_beat",
            "schedule": crontab(hour=2, minute=0),
        },
        "red-team-weekly": {
            "task": "app.worker.tasks.runtime.red_team.run_red_team_beat",
            "schedule": crontab(hour=3, minute=0, day_of_week=1),
        },
        "digest-weekly": {
            "task": "app.worker.tasks.runtime.digest.run_weekly_digest_beat",
            "schedule": crontab(hour=6, minute=0, day_of_week=0),  # Sunday 06:00 UTC
        },
        "alert-daily": {
            "task": "app.worker.tasks.runtime.alert.run_alert_check_beat",
            "schedule": crontab(hour=4, minute=0),  # Daily 04:00 UTC
        },
        # Phase 21 (OPS-08): index staleness / embedding-drift scan.
        # 05:00 UTC — a quiet hour between alert-daily (04:00) and eval-nightly
        # (02:00 the following cycle); routes to pipeline (Pitfall 6).
        "index-staleness-daily": {
            "task": "app.worker.tasks.pipeline.staleness.check_index_staleness_beat",
            "schedule": crontab(hour=5, minute=0),
        },
    },

    # --- Serialization (T-02-04: no pickle) ----------------------------
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # --- Timezone -------------------------------------------------------
    timezone="UTC",
    enable_utc=True,

    # --- Worker pool (ENVIRONMENT-conditional, PROD-15) -----------------
    # "solo"    — development / test: Windows billiard fix (see module docstring).
    #             billiard prefork raises "ValueError: not enough values to unpack"
    #             on Windows (billiard issue #299); solo eliminates the subprocess
    #             pipe race.  Local dev has no concurrency penalty (one worker/queue).
    # "prefork" — production (Linux Fargate): safe and correct on Linux; required
    #             for real concurrency > 1.  The Fargate task CMD passes
    #             --pool=prefork --concurrency=2 which takes authoritative precedence;
    #             this default makes the config self-consistent with that CMD.
    worker_pool="solo" if settings.ENVIRONMENT in ("development", "test") else "prefork",
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
