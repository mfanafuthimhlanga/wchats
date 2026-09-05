"""Docling's cold start is paid at worker boot, in the process that parses.

`#24`. A `parse_documents` run on 2026-08-22 spent **3m43s** between reading the
first file out of S3 and docling's first detection, for a 500-byte file. That
cost is the DocLayNet and TableFormer model load, and it lands on whichever
document happens to arrive first after a deploy: the owner watches an upload sit
at `parsing.started` for four minutes with no way to tell a slow model load from
a hung worker.

`#172`. The load has to happen somewhere, and `worker_ready` was the wrong
somewhere. That signal fires from `Consumer.on_ready()`, and the Consumer
bootstep starts after the Pool bootstep, so under prefork every child already
exists and the handler runs in the PARENT, which never executes a task. The
warm-up is now connected to `worker_process_init`, which prefork sends inside
each forked child and solo sends in the main process. Either way it is the
process that parses.

Five things this pins:

    IT IS CONNECTED TO THE SIGNAL THAT RUNS IN THE PARSING PROCESS, and not to
    the one that runs in the parent.

    THE POOL WAITS LONG ENOUGH FOR IT.  A child sends WORKER_UP only after the
    pool initializer returns, and the pool SIGKILLs a child that has not sent it
    within `worker_proc_alive_timeout`. Celery's default is 4.0 seconds against
    a 3m43s load, which is a crash loop rather than an optimisation.

    IT RUNS ON THE PIPELINE WORKER ONLY.  The runtime worker is built from the
    plain Dockerfile and has no docling; the pipeline worker is the 3 GB image.
    Warming the wrong one would import a package that is not installed.

    IT NEVER RUNS AT IMPORT.  `celery_app` is imported by the API process and by
    every task module, so a warm-up at import time would put four minutes and
    two gigabytes into `uvicorn` startup and into `pytest` collection. A signal
    handler is the shape that cannot do that.

    A FAILED WARM-UP IS NOT A FAILED WORKER.  The warm-up is an optimisation. A
    worker that could not preload the models still parses documents, slowly, and
    a raise inside a pool initializer would crash the child on every fork.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import structlog
from celery import signals

from app.domain import docling_service
from app.worker import celery_app as celery_module


@contextlib.contextmanager
def _selection(*queues: str):
    """Narrow the real `celery_app`'s queue selection, the way `-Q` does.

    `-Q runtime` reaches Celery as `Queues.select(["runtime"])`, which records
    the selection in `consume_from` and leaves `app.amqp.queues` holding both
    declared queues (celery/app/amqp.py, `select` and the `consume_from`
    property). An earlier double here modelled the narrowing as a dict of the
    selected queues only, so it could not tell the two attributes apart, and a
    guard reading the wrong one passed every test while the runtime worker tried
    to load docling on every boot. Narrowing the shipped app is what makes that
    difference visible.

    The selection is process-wide, so it is restored on the way out.
    """
    registry = celery_module.celery_app.amqp.queues
    previous = registry._consume_from  # noqa: SLF001 - the only handle on the selection
    registry.select(list(queues))
    try:
        yield
    finally:
        registry._consume_from = previous  # noqa: SLF001


@pytest.fixture
def warm_up_spy():
    """Replace the real warm-up, which loads ML models, with a recorder."""
    with patch.object(docling_service, "warm_up", return_value=12.5) as spy:
        yield spy


def _receiver_names(signal) -> list[str]:
    return [str(receiver) for _key, receiver in signal.receivers]


def test_the_handler_is_connected_to_worker_process_init():
    """The signal that fires in the process that executes tasks.

    prefork sends it from `process_initializer` inside each forked child;
    solo sends it from `TaskPool.__init__` in the main process.
    """
    names = _receiver_names(signals.worker_process_init)
    assert any("on_worker_process_init" in name for name in names), (
        f"the warm-up is not connected to worker_process_init. receivers={names!r}"
    )


def test_the_handler_is_not_connected_to_worker_ready():
    """`#172`, pinned as an absence.

    `worker_ready` fires from `Consumer.on_ready()`, after the Pool bootstep has
    already forked, so a handler there warms the parent and the child that
    parses the first upload stays cold. That is the defect, and a handler
    connected to both signals would warm the parent again.
    """
    names = _receiver_names(signals.worker_ready)
    assert not any("on_worker_process_init" in name for name in names), (
        f"the warm-up is still connected to worker_ready, which runs in the "
        f"parent under prefork. receivers={names!r}"
    )


def test_the_pool_waits_longer_than_the_measured_model_load():
    """Without this the placement above is a crash loop, not an optimisation.

    `verify_process_alive` (celery/concurrency/asynpool.py) SIGKILLs a child
    still waiting to start when `worker_proc_alive_timeout` passes, and billiard
    sends WORKER_UP only after the pool initializer returns
    (billiard/pool.py, `Worker.__call__`: `after_fork()` then `on_loop_start()`).
    The warm-up runs inside that initializer.
    """
    measured_load_s = 223.0  # 3m43s, #24, measured 2026-08-22
    configured = celery_module.celery_app.conf.worker_proc_alive_timeout
    assert configured == celery_module.WORKER_PROC_ALIVE_TIMEOUT_S
    assert configured > measured_load_s, (
        f"the pool gives a forked child {configured}s to report for work and the "
        f"docling load inside its initializer was measured at {measured_load_s}s. "
        f"The child is killed and respawned for as long as the service runs."
    )


def test_the_pipeline_worker_warms_up_once(warm_up_spy):
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
        with _selection("pipeline"):
            celery_module.on_worker_process_init(sender=None)
    assert warm_up_spy.call_count == 1


def test_the_duration_is_logged(warm_up_spy):
    """The number the issue is about. A deploy log carrying it turns the next
    'is the worker hung?' into a lookup."""
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
        with structlog.testing.capture_logs() as logs:
            with _selection("pipeline"):
                celery_module.on_worker_process_init(sender=None)

    done = [line for line in logs if line["event"] == "worker.docling_warmup_complete"]
    assert len(done) == 1, f"expected one completion line, got {logs!r}"
    assert done[0]["duration_s"] == 12.5


def test_the_setting_off_means_no_warm_up(warm_up_spy):
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=False):
        with _selection("pipeline"):
            celery_module.on_worker_process_init(sender=None)
    assert warm_up_spy.call_count == 0


def test_a_worker_without_the_pipeline_queue_never_warms_up(warm_up_spy):
    """The runtime image has no docling to load.

    This is the whole point of the guard, and `-Q runtime` is exactly how
    `railway.worker-runtime.toml` starts that process.
    """
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
        with _selection("runtime"):
            celery_module.on_worker_process_init(sender=None)
    assert warm_up_spy.call_count == 0


def test_a_worker_consuming_both_queues_warms_up(warm_up_spy):
    """`-Q pipeline,runtime` serves ingestion, so it needs the models loaded."""
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
        with _selection("pipeline", "runtime"):
            celery_module.on_worker_process_init(sender=None)
    assert warm_up_spy.call_count == 1


def test_a_worker_started_without_dash_q_warms_up(warm_up_spy):
    """No selection means consume everything, and everything includes pipeline.

    Celery returns the whole queue registry from `consume_from` when nothing was
    selected, so the guard has to read that shape too.
    """
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
        with _selection():
            celery_module.on_worker_process_init(sender=None)
    assert warm_up_spy.call_count == 1


def test_a_warm_up_failure_logs_and_the_worker_still_starts():
    """docling is absent from the runtime image and can be absent from a local
    checkout. The handler returning normally is what keeps that a slow first
    parse instead of a child that dies inside every fork."""
    with patch.object(
        docling_service, "warm_up", side_effect=ImportError("No module named 'docling'")
    ):
        with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
            with structlog.testing.capture_logs() as logs:
                with _selection("pipeline"):
                    celery_module.on_worker_process_init(sender=None)

    failed = [line for line in logs if line["event"] == "worker.docling_warmup_failed"]
    assert len(failed) == 1, f"expected one failure line, got {logs!r}"
    assert failed[0]["error_type"] == "ImportError"
    assert failed[0]["log_level"] == "warning"


def test_an_app_that_reports_no_queues_is_not_warmed(warm_up_spy):
    """Celery's signal contract is looser than its implementation. An app shape
    this guard cannot read is a reason to skip the optimisation, not to take a
    guess and load two gigabytes on the wrong service."""
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
        with patch.object(celery_module.celery_app, "amqp", SimpleNamespace()):
            celery_module.on_worker_process_init(sender=None)
    assert warm_up_spy.call_count == 0


def test_the_warm_up_document_is_bundled():
    """The warm-up parses a real document, because building the converter alone
    does not load the layout models; the first conversion does."""
    path = docling_service.WARMUP_DOCUMENT
    assert path.exists(), f"the bundled warm-up document is missing: {path}"
    assert path.suffix == ".pdf", (
        "the warm-up document must be a PDF. Markdown and plain text go through "
        "docling's simple backends and never touch DocLayNet or TableFormer, so "
        "warming with one would leave the whole cold start for the first upload."
    )
    assert path.stat().st_size < 20_000, (
        "the warm-up document ships in the image and is parsed on every boot; "
        "one paragraph on one page is the whole requirement"
    )
    assert path.read_bytes().startswith(b"%PDF-")
