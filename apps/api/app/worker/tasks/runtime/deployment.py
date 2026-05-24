"""run_deployment_checklist Celery task — built in Plan 08-03.

This stub is provided to allow Plan 08-04 route imports to resolve.
The real implementation is in apps/api/app/worker/tasks/runtime/deployment.py
created by Plan 08-03 (wave 3 parallel execution).
"""
from __future__ import annotations

from app.worker.celery_app import celery_app

log_msg = "deployment task stub — will be replaced by 08-03"


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.deployment.run_deployment_checklist",
)
def run_deployment_checklist(self, agent_id: str) -> dict:
    """Stub — real implementation from Plan 08-03."""
    raise NotImplementedError("Stub — replaced by Plan 08-03 implementation")
