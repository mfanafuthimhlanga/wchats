"""Docling's cold start is paid at worker boot, not by the first upload.

`#24`. A `parse_documents` run on 2026-08-22 spent **3m43s** between reading the
first file out of S3 and docling's first detection, for a 500-byte file. That
cost is the DocLayNet and TableFormer model load, and it lands on whichever
document happens to arrive first after a deploy: the owner watches an upload sit
at `parsing.started` for four minutes with no way to tell a slow model load from
a hung worker.

The load has to happen somewhere. Moving it to `worker_ready` puts it where
nobody is waiting on it, and where the deploy log records how long it took.

Three things this pins:

    IT RUNS ON THE PIPELINE WORKER ONLY.  The runtime worker is built from the
    plain Dockerfile and has no docling; the pipeline worker is the 3 GB image.
    Warming the wrong one would import a package that is not installed.

    IT NEVER RUNS AT IMPORT.  `celery_app` is imported by the API process and by
    every task module, so a warm-up at import time would put four minutes and
    two gigabytes into `uvicorn` startup and into `pytest` collection. A signal
    handler is the shape that cannot do that.

    A FAILED WARM-UP IS NOT A FAILED WORKER.  The warm-up is an optimisation. A
    worker that could not preload the models still parses documents, slowly, and
    a raise here would crash-loop the service over a performance measure.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import structlog
from celery import signals

from app.domain import docling_service
from app.worker import celery_app as celery_module


def _sender(*queues: str):
    """A stand-in for the Consumer the worker_ready signal sends.

    Celery narrows `app.amqp.queues` to the `-Q` selection before the worker is
    ready, so that attribute is what this worker actually consumes.
    """
    return SimpleNamespace(
        hostname="worker@host",
        app=SimpleNamespace(
            amqp=SimpleNamespace(queues={name: object() for name in queues})
        ),
    )


@pytest.fixture
def warm_up_spy():
    """Replace the real warm-up, which loads ML models, with a recorder."""
    with patch.object(docling_service, "warm_up", return_value=12.5) as spy:
        yield spy


def test_the_handler_is_connected_to_worker_ready():
    """A handler nothing calls is a handler that does not exist."""
    names = [str(receiver) for _key, receiver in signals.worker_ready.receivers]
    assert any("on_worker_ready" in name for name in names), (
        f"on_worker_ready is not connected to worker_ready. receivers={names!r}"
    )


def test_the_pipeline_worker_warms_up_once(warm_up_spy):
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
        celery_module.on_worker_ready(sender=_sender("pipeline"))
    assert warm_up_spy.call_count == 1


def test_the_duration_is_logged(warm_up_spy):
    """The number the issue is about. A deploy log carrying it turns the next
    'is the worker hung?' into a lookup."""
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
        with structlog.testing.capture_logs() as logs:
            celery_module.on_worker_ready(sender=_sender("pipeline"))

    done = [line for line in logs if line["event"] == "worker.docling_warmup_complete"]
    assert len(done) == 1, f"expected one completion line, got {logs!r}"
    assert done[0]["duration_s"] == 12.5


def test_the_setting_off_means_no_warm_up(warm_up_spy):
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=False):
        celery_module.on_worker_ready(sender=_sender("pipeline"))
    assert warm_up_spy.call_count == 0


def test_a_worker_without_the_pipeline_queue_never_warms_up(warm_up_spy):
    """The runtime image has no docling to load."""
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
        celery_module.on_worker_ready(sender=_sender("runtime"))
    assert warm_up_spy.call_count == 0


def test_a_warm_up_failure_logs_and_the_worker_still_starts():
    """docling is absent from the runtime image and can be absent from a local
    checkout. The handler returning normally is what keeps that a slow first
    parse instead of a crash-loop."""
    with patch.object(
        docling_service, "warm_up", side_effect=ImportError("No module named 'docling'")
    ):
        with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
            with structlog.testing.capture_logs() as logs:
                celery_module.on_worker_ready(sender=_sender("pipeline"))

    failed = [line for line in logs if line["event"] == "worker.docling_warmup_failed"]
    assert len(failed) == 1, f"expected one failure line, got {logs!r}"
    assert failed[0]["error_type"] == "ImportError"
    assert failed[0]["log_level"] == "warning"


def test_a_sender_that_reports_no_queues_is_not_warmed(warm_up_spy):
    """Celery's signal contract is looser than its implementation. A sender
    shape this handler cannot read is a reason to skip the optimisation, not to
    take a guess and load two gigabytes on the wrong service."""
    with patch.multiple(celery_module.settings, DOCLING_WARMUP_ON_BOOT=True):
        celery_module.on_worker_ready(sender=object())
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
