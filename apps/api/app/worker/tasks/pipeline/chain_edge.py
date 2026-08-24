"""job_in_job_out, the Celery edge of an ingestion chain hop (ticket #43, issue #7).

WHAT IT DOES
    Celery serialises task arguments as JSON, so a chain hop receives a dict and
    must return one. The work inside a hop wants the IngestionJob. This decorator
    is that conversion, in one place:

        @celery_app.task(bind=True, acks_late=True, ...)
        @job_in_job_out
        def chunk_documents(self, job: IngestionJob) -> IngestionJob:

    The name Celery registers, the module it belongs to and the docstring all
    come from the function below it, so `app.worker.tasks.pipeline.chunk.chunk_documents`
    is still the task name on the broker and `.s()` still takes one result dict.

WHY THE PASS-THROUGH ON A DICT IT CANNOT READ
    chunk, metadata and embed each opened with the same four-clause guard, logged
    `<task>.invalid_result_dict`, and returned the dict unchanged. That is
    deliberate: a chain re-dispatched mid-flight can deliver a dict from a
    different revision of the pipeline, and a hop that raised on it would fail a
    job the operator can still recover. The rule moved into IngestionJob's
    construction; the logging and the pass-through moved here. Both are
    unchanged from the outside, event name included.

WHY A DECORATOR RATHER THAN A NAMED CORE PER TASK
    The obvious shape is a small task function calling a big `_chunk_documents(self,
    job)`. It is the shape the complexity gate forbids: the body carries the
    complexity, the gate pins it by (file, function), and entries may be lowered or
    deleted but never added, so moving a 216-line body under a new name is a new
    over-standard function the baseline cannot name. Wrapping instead keeps the body
    on `chunk_documents`, which is the pin that already exists, and all three pins
    shrank on this change.

WHY THE LOGGER IS THE CORE'S OWN
    structlog names a logger after the module that made it. Building it from
    `core.__module__` keeps every line reading `app.worker.tasks.pipeline.chunk`
    rather than this file, which is what the operator greps for.
"""

import functools
from collections.abc import Callable
from typing import Any

import structlog

from app.domain.ingestion_job import IngestionJob


def job_in_job_out(
    core: Callable[[Any, IngestionJob], IngestionJob],
) -> Callable[[Any, dict], dict]:
    """Wrap a hop's core so Celery sees dict in, dict out.

    Args:
        core: The hop itself, `(self, job) -> IngestionJob`. `self` is the bound
              Celery task, which the core needs for `self.retry`.

    Returns:
        The Celery-facing function, `(self, result) -> dict`. It builds the job,
        runs the core, and returns the returned job's wire form. A result dict
        the job cannot be built from is logged as `<core name>.invalid_result_dict`
        and handed back untouched, with the core never called.
    """
    log = structlog.get_logger(core.__module__)
    invalid_result_dict = core.__name__ + ".invalid_result_dict"

    @functools.wraps(core)
    def edge(self, result: dict) -> dict:
        try:
            job = IngestionJob.from_dict(result)
        except (TypeError, ValueError):
            log.error(invalid_result_dict, keys=list(result.keys()))
            return result
        return core(self, job).to_dict()

    return edge
