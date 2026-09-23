"""
run_retrieval_faithfulness: Celery task (runtime queue), sampled faithfulness
scored by the grounding rule, + per-turn citation coverage (OPS-07).

Position in the post-turn chain (agent.py):
    celery_chain(run_gatekeeper.si(...), run_auditor.si(...), run_strategist.si(...),
                 run_retrieval_faithfulness.si(str(agent_id), job_id)).apply_async(queue="runtime")

Why the gating (sample rate OR Auditor-flagged) lives INSIDE this task, not at
dispatch time in agent.py:
    The Auditor's verdict (grounded/ungrounded/partial) is only known once
    run_auditor has executed and committed its `auditor.complete` job_events
    row. Since run_auditor is the PRECEDING step in the same celery_chain,
    that verdict does not exist yet at the moment agent.py assembles and
    dispatches the chain. Appending `run_retrieval_faithfulness.si(...)` as
    the chain's last step guarantees it runs strictly after Auditor commits
    its verdict, so THIS task can honestly query "was this turn flagged?" and
    apply DOMAIN-NOTES §2's "sample 1-10% + 100% of guardrail-flagged" rule.
    This is a sequencing decision documented as a deviation in 21-04-SUMMARY.md,
    not a re-interpretation of the sampling policy itself.

Idempotency (CLAUDE.md rule 2): re-running this task for the same job_id is a
no-op once retrieval_metrics.faithfulness is non-NULL for that row.

The instrument names itself (ticket #47, AC3):
    A faithfulness score nobody can attribute cannot be compared, so the row
    carries the instrument that produced it in `retrieval_metrics.judge_identity`
    (tenant migration 0020). Since ADR 0015 that is the grounding rule in
    `app.domain.grounding`, `GROUNDING_IDENTITY`, the same identity
    `eval_service` writes to `eval_results.judge_identity` for the offline
    faithfulness score. The rule makes no model call, so this task bills nothing
    to the ledger.

What "retrieved context" means here (#81, #84):
    The chunks the retrieve tool handed the agent, read back from the tenant's
    `tool_calls.retrieved_chunks` (BACKLOG 7.34) through the assistant message id
    the terminal `agent.response` event carries (WIRE-05). NOT the 200-character
    `summary` on `agent.tool_result`, which this task scored until now.

    That proxy carried no error flag, so a DoS-guard refusal emitted under the
    `retrieve` name was scored as retrieved context (#81). Its content also
    changed shape at the #48 loop cutover, from a repr of the SDK content blocks
    to `wire_text(wire)[:200]`, so faithfulness either side of that commit
    differs by the instrument rather than by the agent (#84). A version stamp
    would have made the second readable and left the first standing; reading the
    persisted chunks closes both, and it is what #84 asked for.

    `run_eval_suite` already scores the real chunks, and
    `app.domain.eval_result.CONTEXT_PROXY_VERSION` names that shape
    `agent_retrieve_chunks/1`. This task now reads the same one, so the offline
    score and the live one read the same kind of thing.

Security (CLAUDE.md rule 1): task args are (agent_id, job_id) ONLY. conn_str
is decrypted at runtime from the control DB, never in task args/logs.

How faithfulness is scored (ADR 0015):
    `ground(response_text, contexts).score`, the share of the answer's sentences
    the retrieved chunks carry. None where there is no response or no context,
    and None where the answer has no scoreable sentence: unknown, never zero.
"""

from __future__ import annotations

import dataclasses
import json
import random

import psycopg2
import structlog
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.log_bounds import log_failure
from app.core.security import fernet_decrypt, require_ciphertext
from app.domain.eval_result import CONTEXT_PROXY_VERSION
from app.domain.grounding import GROUNDING_IDENTITY, ground
from app.models.agent import Agent
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Tenant-DB helpers (psycopg2 connect/try/finally/close idiom, per convention)
# ---------------------------------------------------------------------------


def _check_existing_score(conn_str: str, job_id: str) -> tuple[bool, bool]:
    """Return (already_scored, has_row) for the retrieval_metrics row at job_id.

    has_row is False when no retrieval_metrics row exists yet for this job
    (e.g. the turn never called the retrieve tool) — nothing to score.
    already_scored is True when faithfulness is already non-NULL (idempotent skip).
    """
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT faithfulness FROM retrieval_metrics WHERE job_id = %s LIMIT 1",
                (job_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return False, False
    return row[0] is not None, True


def _identity_row(faithfulness: float | None) -> dict | None:
    """The grounding rule's identity in the shape the row stores, or None.

    None where there is no verdict to attribute, which is unknown, and unknown is
    what the column then holds.
    """
    if faithfulness is None:
        return None
    return dataclasses.asdict(GROUNDING_IDENTITY)


def _update_retrieval_metrics(
    conn_str: str,
    job_id: str,
    citation_coverage: float | None,
    faithfulness: float | None,
    identity: dict | None,
) -> None:
    """Write this turn's two signals, and the instrument that produced the second one.

    `identity` belongs to `faithfulness` and to nothing else on the row.
    citation_coverage is arithmetic this task does itself, so a row carrying only
    that one gets NULL here rather than the name of an instrument that did no
    work (tenant migration 0020).

    `context_source` follows the same rule and answers the other half of the
    question (issue #120, tenant migration 0026). `judge_identity` says which
    instrument produced the verdict; this says what shape of text it read, and
    one column held three of those shapes with nothing to tell them apart. The
    value is the constant the offline eval record stamps, so the live score and
    the offline one name one shape with one string.
    """
    context_source = CONTEXT_PROXY_VERSION if faithfulness is not None else None
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE retrieval_metrics SET citation_coverage = %s, faithfulness = %s,"
                " judge_identity = %s::jsonb, context_source = %s WHERE job_id = %s",
                (
                    citation_coverage,
                    faithfulness,
                    json.dumps(identity) if identity else None,
                    context_source,
                    job_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Control-DB helpers (job_events — same tier as validators.py's queries)
# ---------------------------------------------------------------------------


def _is_auditor_flagged(db, job_id: str) -> bool:
    """True if this job's Auditor verdict was 'ungrounded' or 'partial'.

    Queried from control-DB job_events, which by the time this task runs
    (last step of the post-turn chain) already has auditor.complete committed
    if the Auditor step succeeded. If the Auditor step failed/exhausted
    retries (no auditor.complete row), this returns False — the sample-rate
    gate is the only signal in that case, which is the safe default (never
    force-score a turn on missing data).
    """
    flagged_row = db.execute(
        sa_text(
            "SELECT 1 FROM job_events WHERE job_id = :jid"
            " AND event_type = 'auditor.complete'"
            " AND payload->>'verdict' IN ('ungrounded', 'partial') LIMIT 1"
        ),
        {"jid": job_id},
    ).fetchone()
    return flagged_row is not None


def _fetch_turn_context(db, job_id: str) -> tuple[str, list, str | None] | None:
    """Return (response_text, citations_list, message_id) or None.

    `message_id` is the assistant message this turn wrote, off the terminal
    `agent.response` payload (WIRE-05). It is the join key for the turn's
    `tool_calls` rows, and `_fetch_retrieved_contexts` reads the retrieved chunks
    through it. The event is written after `_persist_messages` commits, so a row
    this id names is already there by the time this task runs.

    IT REPLACED A 200-CHARACTER SUMMARY, which is #81 and #84 (module docstring).
    job_events still carries no chunk content, by design; the chunks live in the
    tenant DB and this reader goes there for them.
    """
    response_row = db.execute(
        sa_text(
            "SELECT payload FROM job_events WHERE job_id = :jid"
            " AND event_type = 'agent.response' LIMIT 1"
        ),
        {"jid": job_id},
    ).fetchone()
    if response_row is None or response_row[0] is None:
        return None

    response_payload = response_row[0]
    response_text = response_payload.get("text", "") or ""
    citations_list = response_payload.get("citations") or []

    return response_text, citations_list, response_payload.get("message_id")


@dataclasses.dataclass(frozen=True)
class _TurnRetrieval:
    """What one turn's `retrieve` calls left behind, and how much of it is readable.

    `contexts` is one string per chunk, over every call that recorded one.
    `measured` and `unmeasured` count the CALLS behind that list, never the
    chunks, because a turn that retrieved nothing and a turn nobody could read
    both arrive here with an empty `contexts` and they are not the same
    observation.
    """

    contexts: tuple[str, ...]
    measured: int
    unmeasured: int


def _decode_chunks(value) -> list[str] | None:
    """One `tool_calls.retrieved_chunks` value as chunk strings, or None for unmeasured.

    psycopg2 decodes a jsonb column to a Python list and that is the usual shape.
    A str is decoded here too, because a connection without the json typecaster
    would otherwise report every retrieve in the tenant as unmeasured, and that
    outage would read as a quiet run of unknowns.

    None for SQL NULL and for anything that does not decode to a list. Both mean
    the call recorded no context, which is not the observation an empty list
    makes. `_persisted_chunks` in `app.worker.tasks.runtime.agent` is the writer
    that keeps the two apart, and it already returns NULL for an errored retrieve.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, list):
        return None
    return [str(chunk) for chunk in value if chunk]


def _read_retrieved_rows(rows) -> _TurnRetrieval:
    """Fold one turn's `retrieved_chunks` rows into its context and its two counts.

    THE UNMEASURED COUNT IS THE POINT (#81). An unmeasured call must not reach
    the rule as an empty context: an empty context makes every claim unsupported, so
    the score would describe the DoS guard or the decoder rather than the answer.
    It contributes nothing and is counted instead, and the count travels with the
    verdict so a turn whose every retrieve errored reads as unknown.
    """
    contexts: list[str] = []
    measured = 0
    unmeasured = 0
    for row in rows:
        chunks = _decode_chunks(row[0])
        if chunks is None:
            unmeasured += 1
            continue
        measured += 1
        contexts.extend(chunks)
    return _TurnRetrieval(tuple(contexts), measured, unmeasured)


#: One row per retrieve call the turn made, in ONE order for every reader.
#:
#: `created_at` alone is not an order. `tool_calls.created_at` defaults to
#: `now()`, which is the TRANSACTION's clock in Postgres, so every row a turn
#: writes in one transaction carries the same timestamp and the sort falls
#: through to whatever the heap hands back. Two reads of one turn could then
#: assemble the contexts in two orders and hand the rule two different documents.
#: `id` breaks the tie. It is a random uuid (alembic_tenant 0001), so it is a
#: stable order rather than the call order, which is what a set of contexts
#: needs: nothing downstream reads position, and everything downstream needs the
#: same list twice.
_RETRIEVED_CHUNKS_SQL = (
    "SELECT retrieved_chunks FROM tool_calls WHERE message_id = %s"
    " AND tool_name = 'retrieve' ORDER BY created_at, id"
)


def _fetch_retrieved_contexts(conn_str: str, message_id: str | None) -> _TurnRetrieval:
    """The chunks this turn retrieved, from the tenant's `tool_calls` rows.

    An absent `message_id` joins to nothing, so the turn reports zero calls
    either way rather than guessing how many it made; the log line names the id
    so the absence is visible.
    """
    if not message_id:
        return _TurnRetrieval((), 0, 0)
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(_RETRIEVED_CHUNKS_SQL, (message_id,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return _read_retrieved_rows(rows)


def _citation_coverage(citations: list, measured_calls: int) -> float | None:
    """Cited spans over the retrieve calls that actually retrieved something.

    None rather than 0.0 when no call was measured, which is the honest empty
    state (DOMAIN-NOTES §6). THE DENOMINATOR COUNTS MEASURED CALLS: an errored
    retrieve retrieved nothing, and counting it would report a turn as poorly
    cited because its DoS guard fired. The proxy this replaced counted one
    summary per call and could not tell the two apart.
    """
    if not measured_calls:
        return None
    return min(1.0, len(citations) / measured_calls)


# ---------------------------------------------------------------------------
# Faithfulness, by the grounding rule (ADR 0015)
# ---------------------------------------------------------------------------


def _compute_faithfulness(response_text: str, contexts: list[str]) -> float | None:
    """This turn's faithfulness: the share of its sentences the retrieved chunks carry.

    None when there is no context or no response, and None when the rule finds
    no scoreable sentence. Never raises: a scoring failure is logged
    and yields None, because it must never fail or retry the sampled analytics
    task's other work (citation_coverage still writes).
    """
    if not contexts or not response_text:
        return None
    try:
        return ground(response_text, contexts).score
    except Exception as exc:  # noqa: BLE001, never fail the task on a scoring defect
        log_failure(log, "run_retrieval_faithfulness.scoring_failed", exc)
        return None


def _scored_report(
    conn_str: str,
    job_id: str,
    citation_coverage: float | None,
    faithfulness: float | None,
    counts: dict,
) -> dict:
    """Write this turn's two signals, name the instrument, and report the lot.

    `counts` rides on both the log line and the return, so the number of retrieve
    calls nobody could read is beside the score rather than inferable from it
    (#81). A verdict that arrives without them is a verdict whose denominator is
    a guess.

    Returns {} when the UPDATE fails, because a telemetry write must never fail
    or retry an already-served turn (T-21-01-03). The row keeps its NULL
    faithfulness, so the next run of this task for the job scores the turn again
    rather than skipping it as already measured.
    """
    identity_row = _identity_row(faithfulness)
    try:
        _update_retrieval_metrics(conn_str, job_id, citation_coverage, faithfulness, identity_row)
    except Exception as exc:  # noqa: BLE001, never fail/retry an already-served turn
        log_failure(log, "run_retrieval_faithfulness.update_failed", exc, job_id=job_id)
        return {}

    log.info(
        "run_retrieval_faithfulness.complete",
        job_id=job_id,
        citation_coverage=citation_coverage,
        faithfulness=faithfulness,
        judge_identity=identity_row,
        **counts,
    )
    return {
        "status": "scored",
        "citation_coverage": citation_coverage,
        "faithfulness": faithfulness,
        "judge_identity": identity_row,
        **counts,
    }


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="app.worker.tasks.runtime.retrieval_eval.run_retrieval_faithfulness",
)
def run_retrieval_faithfulness(self, agent_id: str, job_id: str) -> dict:  # noqa: ARG001
    """Sampled faithfulness, by the grounding rule, + citation-coverage UPDATE (OPS-07).

    Args:
        agent_id: UUID string. conn_str is decrypted at runtime from the
                  control DB, NEVER an argument (CLAUDE.md rule 1).
        job_id:   UUID string of the runtime chat job this scores.

    Returns:
        {"status": "already_scored" | "no_retrieval_metrics_row" |
                    "skipped_not_sampled" | "no_agent_response_event" |
                    "no_signal" | "scored", ...}
        {} on agent-not-found or an unrecoverable UPDATE failure.

        Every status this task reaches after the sampling gate carries
        `retrieve_calls_measured` and `retrieve_calls_unmeasured` (#81). A turn
        whose retrieves all errored scores nothing and says so, rather than
        reading as a turn with no faithfulness problem.
    """
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error("run_retrieval_faithfulness.agent_not_found", job_id=job_id, agent_id=agent_id)
            return {}
        conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))

        # Idempotency guard (T-21-04-01 adjacent): skip the recompute if already
        # scored, and skip entirely if no retrieval_metrics row exists.
        already_scored, has_row = _check_existing_score(conn_str, job_id)
        if not has_row:
            log.info("run_retrieval_faithfulness.no_retrieval_metrics_row", job_id=job_id)
            return {"status": "no_retrieval_metrics_row"}
        if already_scored:
            log.info("run_retrieval_faithfulness.already_scored", job_id=job_id)
            return {"status": "already_scored"}

        # ------------------------------------------------------------------
        # Gating is sampled OR 100% of the Auditor-flagged ungrounded and
        # partial turns. It bounds the per-turn work past this point: the
        # agent.response read, the tool_calls read for the chunks, and the
        # retrieval_metrics UPDATE. The module docstring says why it lives here,
        # post-Auditor, rather than at dispatch.
        # ------------------------------------------------------------------
        sampled = random.random() < settings.RETRIEVAL_FAITHFULNESS_SAMPLE_RATE
        auditor_flagged = False if sampled else _is_auditor_flagged(db, job_id)
        if not (sampled or auditor_flagged):
            log.debug("run_retrieval_faithfulness.skipped_not_sampled", job_id=job_id)
            return {"status": "skipped_not_sampled"}

        turn_context = _fetch_turn_context(db, job_id)
        if turn_context is None:
            log.warning("run_retrieval_faithfulness.no_agent_response_event", job_id=job_id)
            return {"status": "no_agent_response_event"}

    response_text, citations_list, message_id = turn_context

    # The chunks the retrieve tool handed the agent, not a summary of them
    # (#81, #84, and the module docstring). The two counts beside them say how
    # many of this turn's retrieve calls could be read at all, and they travel
    # with the verdict all the way out.
    retrieval = _fetch_retrieved_contexts(conn_str, message_id)
    counts = {
        "retrieve_calls_measured": retrieval.measured,
        "retrieve_calls_unmeasured": retrieval.unmeasured,
    }

    # citation_coverage is a coarse proxy. The schema does not persist per-chunk
    # citation attribution, so this measures how often a retrieve call led to a
    # cited claim, not exact chunk-level coverage.
    citation_coverage = _citation_coverage(citations_list, retrieval.measured)

    faithfulness = _compute_faithfulness(
        response_text=response_text, contexts=list(retrieval.contexts))

    if citation_coverage is None and faithfulness is None:
        log.info("run_retrieval_faithfulness.no_signal", job_id=job_id,
                 message_id=message_id, **counts)
        return {"status": "no_signal", **counts}

    return _scored_report(conn_str, job_id, citation_coverage, faithfulness, counts)
