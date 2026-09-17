"""rejudge_eval_run, scoring a finished run's stored answers with today's Judges (#274).

WHY A SECOND RUN AND NOT AN UPDATE
    #274 replaced the instrument behind the gated `answer_relevancy` metric. The
    owner's 46 labels were collected against the old one on run 0a99f7ab, and the
    only honest way to measure the new Judge against those labels is to put it in
    front of the same answers. Overwriting the source run's rows would destroy
    the measurement the labels were taken against, which is the one thing a
    calibration needs to keep. So this writes a NEW run, naming the old one in
    `eval_runs.source_run_id` (tenant migration 0030), and never issues an UPDATE
    or a DELETE against anything the source run wrote.

WHAT IT DOES NOT DO
    It drives no agent turn. `eval_samples` already holds the four strings the
    run scored, so the response under judgement is the one the agent gave that
    night and the retrieved contexts are the ones it retrieved. That is what
    makes this comparable to the labels: a fresh turn would answer differently
    and the labels would be about a different answer.

    It resolves no question. The source run's `resolved_question` is on the row
    and is reused, so the Judge reads the string the labeller was shown (ADR
    0010) and the run pays for no rewrites.

    It scores the two GATED metrics and no others (`REJUDGE_METRIC_KEYS`).
    Context precision and recall gate nothing, and ragas relevancy costs three
    judge calls and four embeddings per row for a number that is now reported
    rather than read. The three it skips still get an `eval_results` row carrying
    no score and no verdict, because an unmeasured dimension has to be visible as
    one.

IDEMPOTENCY (project rule 2, and it is the expensive half)
    `acks_late=True` means a redelivered message re-runs the body. The guard is
    the pair (source run, instrument), where the instrument is every Judge this
    rejudge pays for and every threshold it writes against
    (`eval_service.rejudge_instrument`). A completed rejudge by that exact
    instrument is returned rather than paid for again; a move in any model,
    effort, prompt or gate is a different measurement and runs.

Queue: runtime (CLAUDE.md: both queues always). No connection string in the task
args (project rule 1): it takes `agent_id` and `source_run_id`, and decrypts the
dsn from the control DB inside.
"""

from __future__ import annotations

import uuid

import psycopg2
import structlog

from app.core.database import get_sync_db
from app.core.log_bounds import log_failure
from app.core.model_client import LedgerContext, ledger_recorder
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.clarifying_check import clarifying_verdicts, split_checked_rows
from app.services.eval_service import (
    REJUDGE_METRIC_KEYS,
    existing_rejudge_run,
    insert_rejudge_run,
    read_eval_samples,
    rejudge_config,
    rejudge_instrument,
    run_ragas_eval,
    update_eval_run_status,
    write_eval_results,
    write_eval_samples,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


def _agent_connection(agent_id: str) -> tuple[str, str] | None:
    """(tenant_id, conn_str) for this agent, decrypted inside the task.

    None when the agent is absent or has no database, which is the same answer
    `run_eval_suite` gives: there is nothing to rescore and nothing to retry.
    """
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_connection_string:
            log.error("rejudge_eval_run.agent_not_found_or_unconfigured", agent_id=agent_id)
            return None
        return str(agent.tenant_id), fernet_decrypt(agent.neon_connection_string)


def _already_rejudged(
    agent_id: str, source_run_id: str, instrument, conn_str: str
) -> str | None:
    """The completed rejudge of this source by this instrument, or None.

    BEST EFFORT, the same choice `run_eval_suite` makes for its own guard: a
    check that cannot run must not stop the work it guards. What a failure costs
    is a second set of judge calls, which is money and never a wrong row, because
    the second run writes its own id.
    """
    try:
        return existing_rejudge_run(agent_id, source_run_id, instrument, conn_str)
    except Exception as exc:
        log_failure(
            log, "rejudge_eval_run.idempotency_check_failed", exc, agent_id=agent_id
        )
        return None


def _score_into(
    run_id: str,
    samples: list,
    *,
    tenant_id: str,
    agent_id: str,
    conn_str: str,
) -> tuple[dict, list, list]:
    """Copy the samples onto the new run, judge them, write the rows, close it.

    EVERY WRITE TAKES `run_id`, and `run_id` is the NEW run. The source run's id
    reaches nothing in this function, which is what keeps the measurement the
    owner's labels describe intact.

    Returns (the scoring payload, the judged rows, the rows the rule decided).
    """
    judged, checked = split_checked_rows(samples)
    # THE COPY IS WHAT MAKES THE NEW RUN LABELLABLE. `calibrate_run.py` reads
    # `eval_samples` for the run id it is given, so a rejudge run with no samples
    # of its own would produce an empty sheet and join nothing.
    write_eval_samples(run_id, samples, conn_str)
    ledger = LedgerContext(
        tenant_id=tenant_id,
        agent_id=agent_id,
        job_id=run_id,
        recorder=ledger_recorder(conn_str),
    )
    results = run_ragas_eval(judged, ledger, metric_keys=REJUDGE_METRIC_KEYS)
    write_eval_results(run_id, results["judge_records"], conn_str)
    update_eval_run_status(run_id, "complete", finished_at=True, conn_str=conn_str)
    return results, judged, checked


def _run_row_written(
    run_id: str, agent_id: str, source_run_id: str, instrument, conn_str: str
) -> bool:
    """Insert the rejudge run's row. False when the tenant has no `source_run_id`.

    NOT A RETRY, and not a degraded write either. `insert_rejudge_run` has no
    narrower rung on purpose: a rejudge written without `source_run_id` could not
    be joined back to the labels it exists to be joined to and could not be found
    by the idempotency check, so every retry would pay for the judge calls again.
    Three attempts would discover the same missing column three times. Re-run the
    tenant's migrations and dispatch it again.
    """
    try:
        insert_rejudge_run(
            run_id, agent_id, source_run_id,
            rejudge_config(source_run_id, instrument), conn_str,
        )
    except psycopg2.errors.UndefinedColumn:
        log.error(
            "rejudge_eval_run.tenant_behind_0030",
            agent_id=agent_id,
            source_run_id=source_run_id,
            detail=(
                "eval_runs has no source_run_id column: this tenant database "
                "predates alembic_tenant 0030. Re-run its migrations, then "
                "dispatch the rejudge again"
            ),
        )
        return False
    return True


def _mark_failed(run_id: str, conn_str: str) -> None:
    """Best-effort terminal 'failed' status, the same guard `run_eval_suite` has.

    A run must end in a terminal state or it never happened, and the write itself
    must never derail the caller's `self.retry`. A failure is logged at error
    level because the consequence, a row that reads 'running' for a run that is
    over, is the indistinguishable-from-hung state it exists to prevent.
    """
    try:
        update_eval_run_status(run_id, "failed", finished_at=True, conn_str=conn_str)
    except Exception as exc:
        log_failure(
            log, "rejudge_eval_run.mark_failed_failed", exc, level="error", run_id=run_id
        )


def _rejudge(
    agent_id: str, source_run_id: str, *, tenant_id: str, conn_str: str
) -> dict:
    """The whole of the work. Raises, and the task above owns the retry."""
    instrument = rejudge_instrument()
    existing = _already_rejudged(agent_id, source_run_id, instrument, conn_str)
    if existing:
        log.info(
            "rejudge_eval_run.already_rejudged",
            agent_id=agent_id, source_run_id=source_run_id, run_id=existing,
        )
        return {
            "status": "already_rejudged",
            "run_id": existing,
            "source_run_id": source_run_id,
        }

    samples = read_eval_samples(source_run_id, conn_str)
    if not samples:
        # REPORTED, NOT RETRIED. A run with no samples has nothing to rescore,
        # and retrying would spend three attempts discovering that again.
        log.warning(
            "rejudge_eval_run.no_samples",
            agent_id=agent_id,
            source_run_id=source_run_id,
            detail=(
                "the source run stored no eval_samples: it predates tenant "
                "migration 0027 or it scored nothing"
            ),
        )
        return {"status": "no_samples", "source_run_id": source_run_id}

    run_id = str(uuid.uuid4())
    if not _run_row_written(run_id, agent_id, source_run_id, instrument, conn_str):
        return {"status": "tenant_behind_0030", "source_run_id": source_run_id}
    try:
        results, judged, checked = _score_into(
            run_id, samples, tenant_id=tenant_id, agent_id=agent_id, conn_str=conn_str
        )
    except Exception:
        _mark_failed(run_id, conn_str)
        raise

    log.info(
        "rejudge_eval_run.complete",
        agent_id=agent_id,
        run_id=run_id,
        source_run_id=source_run_id,
        judged=len(judged),
        scored=len(results["scores"]),
        rule_checked=len(clarifying_verdicts(checked)),
    )
    return {
        "status": "complete",
        "run_id": run_id,
        "source_run_id": source_run_id,
        "judged": len(judged),
        "scored": len(results["scores"]),
    }


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.rejudge.rejudge_eval_run",
)
def rejudge_eval_run(self, agent_id: str, source_run_id: str) -> dict:
    """Rescore one finished run's stored samples into a new run.

    Sequence:
        1. Fetch the agent from the control DB; decrypt the dsn at runtime.
        2. Idempotency: a completed rejudge of this source by this Judge wins.
        3. Read the source run's `eval_samples`. An empty read is reported, not
           retried: a run with no samples has nothing to rescore, and retrying
           would spend three attempts discovering that again.
        4. Insert the new run, copy the samples onto it, score the two gated
           metrics, write the results, mark it complete.

    Args:
        agent_id:      UUID string of the agent whose tenant database holds the run.
        source_run_id: UUID string of the finished run to rescore.

    Returns:
        {"status": "complete", "run_id", "source_run_id", "scored", "judged"} on
            a run that wrote rows.
        {"status": "already_rejudged", "run_id", "source_run_id"} when this Judge
            had already scored this source run.
        {"status": "no_samples", "source_run_id"} when the source run stored none.
        {"status": "tenant_behind_0030", "source_run_id"} when the tenant database
            has no `source_run_id` column. Not retried: three attempts would
            find the same missing column three times.
        {} on retry exhaustion.
    """
    connection = _agent_connection(agent_id)
    if connection is None:
        return {}
    tenant_id, conn_str = connection
    try:
        return _rejudge(
            agent_id, source_run_id, tenant_id=tenant_id, conn_str=conn_str
        )
    except Exception as exc:
        log_failure(
            log, "rejudge_eval_run.failed", exc, level="error",
            agent_id=agent_id, source_run_id=source_run_id,
        )
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
