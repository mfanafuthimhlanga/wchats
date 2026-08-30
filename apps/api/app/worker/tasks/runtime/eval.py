"""M6 eval tasks — nightly eval suite, per-agent Ragas eval, scenario generation.

All tasks: acks_late=True, runtime queue, no conn_str in args (CTL-08).
Ragas 0.4.x only — D-01 through D-04.

Where each write lands
----------------------
An eval result is an OBSERVATION ABOUT a run, not tenant data. So:

    scenario read / eval_runs / results  -> conn_str         (PRODUCTION)
    scoring (run_ragas_eval)             -> no database at all

run_ragas_eval scores rows that are already in memory against the judge API and
opens no connection at all. The scenario rows themselves are read from
PRODUCTION below.

verified_qa promotion is not performed by this task at all.

WHAT HOLDS IT SHUT IS TWO THINGS, AND THIS PARAGRAPH USED TO NAME ONE (D6 P3
review, finding 6). It said "disabled behind eval_service's label trust
hierarchy", which was the whole answer before D6. Strongest first.

    0. NO CODE. No promotion writer exists in this tree (ADR 0003), so the
       `promoted: 0` this task returns is a literal, not a result. Pinned by
       TestPromotionIsUnreachableFromTheTask below.
    1. THE DECISION. `VERIFIED_QA_PROMOTION_DECISION["enabled"]` is False, the
       owner's settled eval-only decision of 2026-08-08.

The decision carries its reason, and both are recorded on the run in
`eval_runs.config`
and returned as `promotion_enabled` / `promotion_disabled_reason`, so the
disablement is a statement in the record rather than an absence a later reader
has to infer.

Which rows a run covers
-----------------------
The selector was `ORDER BY RANDOM() LIMIT 30` — a different sample every night,
so run-to-run variance was dominated by the draw and a regression could not be
distinguished from a redraw. It is now two queries: every `dataset='golden'` row
UNSAMPLED, plus a rotating exploratory sample. The same golden items scored on
consecutive runs give a paired per-item delta; the rotating half is what stops
the fixed half being overfit. The two are reported separately all the way out
(`datasets` in the return, `config["dataset"]` on the run) because a golden
score and an exploratory score are different measurements.

Every report carries (attempted, valid, scored): rows fetched, rows carrying a
label, rows Ragas returned a real number for. A rate without its denominator
must not be constructible from what this task returns.

The agent is invoked (audit D1, plan P2)
----------------------------------------
Until this phase the task built every sample with

    # For M6: use reference_answer as proxy agent_response to test the eval harness
    "agent_response": row[3],       # row[3] IS reference_answer

so Ragas scored each reference answer against the contexts that answer was
written from. Faithfulness and AnswerRelevancy approached 1.0 BY CONSTRUCTION,
the score was invariant to the agent's model, prompt, retrieval configuration
and capability envelope, and every layer built on top — the configuration tuple,
the deploy gate's eval half — was reasoning about a number that measured
nothing. Three years of scaffolding on one line of scaffolding.

Now each scenario's question is put to the customer agent, through the SAME
seam run_agent_turn uses (agent_loop.build_agent_turn) and the SAME loop
(agent_loop.run_agent_loop), and the agent's own response is what gets scored.
Four properties, each of which is a way this could have gone wrong and been
invisible:

  * ALWAYS side_effects="recorded", never "live". The seam grants eleven tools
    and six of them reach a real ProviderAdapter; one eval scenario in which the
    agent decides to refund would execute a refund against the tenant's
    provider. The parameter is mandatory precisely so it cannot be forgotten,
    and tests/unit/test_eval_agent_invocation.py fails if this module ever asks
    for "live".
  * retrieved_contexts come from the AGENT'S OWN retrieve result, never from the
    scenario's stored column. Scoring faithfulness against contexts the agent
    never saw is D1 wearing a different hat, so the stored column is carried
    under a name run_ragas_eval does not read (`stored_retrieved_contexts`)
    rather than left where a future edit could reconnect it.
  * A scenario whose agent call FAILS is EXCLUDED AND COUNTED, never scored 0.
    Zero is not a low score, it is the absence of one.
  * A run where too few scenarios answered reports 'unknown', never 'pass', at
    the MIN_RESPONSE_RATE floor.

THE PII FIREWALL RUNS ON THIS PATH TOO, since #50. It used to run in the live
Celery task body only, so an eval scored the agent's own words while a customer
read the deflection — and a response carrying a customer's email address, card
number or ID number was posted verbatim to a third-party judge API.
`agent_loop._turn_result` scans inside the seam now, so what Ragas scores is what
a customer would have read. The cost of that is stated rather than left implicit:
a deflected turn is scored AS the deflection, which measures the firewall rather
than the answer behind it, and a run whose firewall fires often will read as a
grounding regression.

And the mutating-skill attempts recorded mode captured travel out with the run.
That the agent CHOSE to call issue_refund is capability-envelope adherence and
one of the more valuable things an eval can observe; it is invisible unless it
is carried out of the turn.

EXPECT THE NUMBERS TO GET WORSE. Faithfulness falls from ~1.0 to whatever is
true. That is the instrument starting to work, not a regression.
"""

from __future__ import annotations

import asyncio
import uuid

import psycopg2
import structlog
from sqlalchemy import select

from app.core.database import get_sync_db
from app.core.model_client import LedgerContext, ledger_recorder
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.eval_service import (
    AGENT_INVOCATION_CONCURRENCY,
    AGENT_INVOCATION_MAX_CALLS_PER_RUN,
    AGENT_INVOCATION_MEASURED,
    DATASET_GOLDEN,
    EVAL_RUN_IDEMPOTENCY_SLACK_S,
    EXPLORATORY_SAMPLE_SIZE,
    VERIFIED_QA_PROMOTION_DECISION,
    build_eval_result,
    build_eval_run_config,
    dataset_composition,
    dataset_of,
    insert_eval_run,
    invocation_provenance,
    read_run_ledger,
    run_ragas_eval,
    summarise_agent_invocation,
    summarise_run_validity,
    update_eval_run_config,
    update_eval_run_status,
    write_eval_result,
    write_eval_results,
)
from app.services.scenario_service import (
    generate_eval_suite_for_agent,
    mine_production_scenarios,
    store_scenarios,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

#: `run_ragas_eval`'s return shape for a run that scored nothing, which is what
#: the below-measurement-floor branch substitutes rather than calling the judge.
#:
#: `judge_records` is the key `write_eval_results` takes, and it is a different
#: grain from `scores`. One JudgeRecord is one (scenario, metric) decision
#: carrying its threshold, its verdict, its Judge and the ledger bucket that paid
#: for it; one `scores` row is one scenario carrying four numbers, which is what
#: `summarise_run_validity` counts. Both come out of `run_ragas_eval` built from
#: the same attributed rows. Rebuilding either at the call site would put a
#: second derivation between the number this task reports and the row it writes,
#: which is the defect #51 removes one grain up.
#:
#: Copied with `dict()` at every use, so a branch cannot mutate the shared shape.
_NOTHING_SCORED = {
    "scores": [], "judge_records": [], "means": {},
    "sent": 0, "returned": 0, "unattributed": 0,
}


def _run_ledger(tenant_id: str, agent_id: str, run_id: str, conn_str: str) -> LedgerContext:
    """Who this run's judge calls are billed to, and which database records them.

    The dsn reaches the recorder here and travels no further: `LedgerContext` has
    no field that could hold one, which is what lets `run_ragas_eval` go on saying
    it takes no connection string.
    """
    return LedgerContext(
        tenant_id=tenant_id, agent_id=agent_id, job_id=run_id,
        recorder=ledger_recorder(conn_str),
    )


def _mark_failed_on_production(run_id: str, conn_str: str, agent_id: str) -> None:
    """Best-effort terminal 'failed' status write on PRODUCTION.

    A run must end in a terminal state on production or it never happened —
    but the write itself must never derail what matters more on an
    already-failing path: the caller's `self.retry`. An unguarded raise here
    would skip `raise self.retry(...)` entirely, so the task would die instead
    of retrying, and the failure would be attributed to the status write rather
    than to whatever actually broke.

    A failure here is logged at error level rather than swallowed quietly: it
    means production still reads 'running' for a run that is over, which is
    exactly the indistinguishable-from-hung state this phase exists to remove.
    """
    try:
        update_eval_run_status(run_id, "failed", finished_at=True, conn_str=conn_str)
    except Exception as status_exc:
        log.error(
            "run_eval_suite.mark_failed_failed",
            agent_id=agent_id,
            run_id=run_id,
            error=str(status_exc),
            detail="production eval_runs row still reads 'running' for a finished run",
        )


def _agent_turn_timeout_s() -> int:
    """agent.py's per-turn wall-clock bound. ONE copy of the number, imported.

    Lazy for the reason the block comment below gives for every other agent.py
    import in this module, and a function rather than a module constant so the
    laziness survives: a second literal here would be the audit's D3 defect
    wearing new clothes, and this one would decide the idempotency window a
    redelivered message is judged against.
    """
    from app.worker.tasks.runtime.agent import AGENT_TURN_TIMEOUT_S  # noqa: PLC0415

    return AGENT_TURN_TIMEOUT_S


def _failure_of(exc: BaseException) -> tuple[str, str]:
    """One failed turn's class and what this build says about it (#25). Pure.

    THE MESSAGE IS COMPOSED HERE, NEVER COPIED OFF THE EXCEPTION. Two things
    forced that. `str(TimeoutError())` is the empty string, so eval run
    29754ceb's two timeouts logged `error= error_type=TimeoutError` and said
    nothing about what ran out of what; and `eval_runs.result` is jsonb the
    owner reads back, which is the boundary #96 keeps raw provider text off.
    A phrase this module chose cannot carry a customer's words, a connection
    string or a stack frame across it.

    A timeout gets the one fact its class cannot carry, the budget it exceeded.
    Every other class gets its own name and nothing more, which says as much as
    the exception's text was ever trusted to say in a stored row.

    Args:
        exc: whatever the agent turn raised.

    Returns:
        (error_type, message). The first counts into `Invocation.failed` and the
        run's error histogram; the second reaches `eval_runs.result`.
    """
    name = type(exc).__name__
    if isinstance(exc, TimeoutError):
        # asyncio.TimeoutError IS TimeoutError from 3.11, so the wait_for in
        # _drive_eval_turn and a provider's own timeout land on one branch.
        return name, f"agent turn exceeded {_agent_turn_timeout_s()}s"
    return name, name


# ---------------------------------------------------------------------------
# Invoking the agent, per scenario (audit D1 / plan P2)
# ---------------------------------------------------------------------------
# WHY THE IMPORTS BELOW ARE LAZY. `agent.py` pulls in Langfuse, the validators
# and the retrieval-faithfulness task at module scope, and it is a Celery task
# module besides. Putting that whole graph into THIS module's import graph would
# make `tests/unit/test_eval_task.py` carry it too, and that file has nothing to
# do with the customer turn. `eval_service.build_eval_run_config` already imports
# `deployment_service` inside the function body for the same class of reason.
#
# They are imported BY NAME rather than through an accessor, because the static
# half of tests/unit/test_eval_agent_invocation.py reads this module's AST to
# prove every `build_agent_turn(...)` call asks for recorded side effects, and a
# computed callee has no name to read.


class _EvalEventSink:
    """The db/redis double `run_agent_loop` emits SSE events through.

    `run_agent_loop` calls `emit(job_id, "agent.tool_call", …, db, redis)` for
    every tool use it observes. On the chat path those rows are the durable
    replay log a late-joining widget reads. On the eval path there is no widget,
    no SSE subscriber and — this is the part that matters — NO `jobs` ROW: the
    job_id is synthesised per scenario. Writing sixty scenarios' worth of
    `job_events` into the CONTROL DB under ids that name no job would put eval
    traffic into the same table the ops room and the SSE replay endpoint read,
    which is the tenant-data pollution approach (b) was chosen to avoid, one
    table over.

    So the events are dropped, deliberately and visibly, rather than persisted
    to a place nothing will ever read them from. `emit` is unchanged: it still
    publishes and still commits, into this.

    This is the SSE/persistence divergence the plan named as inherent to
    approach (b) — "Persistence and SSE differ by design" — and it is confined
    to this class so that the divergence has one location and a name.
    """

    def publish(self, channel: str, message: str) -> int:  # redis half
        return 0

    def add(self, obj) -> None:  # SQLAlchemy Session half
        return None

    def commit(self) -> None:
        return None


def _drive_eval_turn(turn, *, question: str, run_id: str) -> dict:
    """Run one assembled eval turn to its end, then write what it spent.

    The ledger rows are written here rather than during the loop, for the reason
    `record_turn_calls` gives. Writing one opens a tenant connection, and the loop
    runs on an event loop with a wall-clock ceiling over it. On BOTH paths, because
    a scenario that timed out still paid for the calls it made.

    `close_turn` also hands this turn's tool ContextVars back, so the run's
    "recorded" mode dies with the scenario that asked for it (#98). The recorded
    side-effect sink deliberately survives: `_invoke_agent_for_scenarios` reads it
    once this returns, and on the failure path too.
    """
    from app.services.agent_loop import close_turn, run_agent_loop  # noqa: PLC0415
    from app.worker.tasks.runtime.agent import AGENT_TURN_TIMEOUT_S  # noqa: PLC0415

    sink = _EvalEventSink()
    try:
        return asyncio.run(
            asyncio.wait_for(
                run_agent_loop(
                    question, history=[], turn=turn, job_id=run_id, db=sink, redis=sink
                ),
                timeout=AGENT_TURN_TIMEOUT_S,
            )
        )
    finally:
        close_turn(turn)


def _run_one_eval_turn(
    *,
    agent_id: str,
    conn_str: str,
    run_id: str,
    question: str,
    prompt_version_id: str | None,
) -> dict:
    """Put one scenario question to the customer agent. Returns `run_agent_loop`'s dict.

    Same seam as the chat path (`build_agent_turn`) and same loop
    (`run_agent_loop`), so what is measured is what is served. What differs is
    stated here rather than discovered later:

      * `side_effects="recorded"` — ALWAYS, never "live". Six of the eleven tools
        the seam grants reach a real ProviderAdapter, and this loop runs nightly,
        unattended, against a real tenant.
      * `verified_session_token=""` — an eval scenario is an UNVERIFIED customer.
        Every identity-gated skill therefore refuses, which is the correct
        posture for a question that arrived with no IDV session, and it is the
        posture a mined production scenario carries no evidence against.
      * `history=[]` and a fresh conversation id per scenario, because scenarios
        are independent by construction. A shared conversation would let scenario
        12's answer be shaped by scenario 11.
      * `job_id=run_id`, the eval run's own id and the id `_run_ledger` bills
        the judges under. A synthesised uuid per scenario names no job, so
        `model_calls WHERE job_id = <run_id>` returned the judge half of a run and
        none of the agent turns, and this run's agent traffic was indistinguishable
        from live customer traffic under `purpose='agent_turn'`.
      * No `conversations` row is created. Nothing writes one because recorded
        mode suppresses every tenant write the tools would make, and creating one
        would put eval traffic into the table `mine_production_scenarios` reads —
        the eval would begin generating its own future test set from its own
        output, which is the reason approach (a) was rejected.

    The canary is deliberately NOT re-rolled. `prompt_version_id` is the
    PRODUCTION label already resolved by `build_eval_run_config` for this run's
    attribution, and the same helper the chat path uses re-fetches that exact
    version's soul fields by id. Passing None instead would serve the agent's
    live `soul_*` columns while `eval_runs.prompt_version_id` still named the
    production version — a score attributed to a prompt that never produced it,
    which is BACKLOG 2.3's defect exactly.
    """
    from app.services.agent_loop import build_agent_turn  # noqa: PLC0415
    from app.worker.tasks.runtime.agent import (  # noqa: PLC0415
        _resolve_turn_prompt_version,
    )

    conversation_id = str(uuid.uuid4())

    # The control-DB session is held only for as long as the seam needs it.
    # build_agent_turn reads every field it wants off the agent row before it
    # returns, so the turn runs with no session open. That turn takes up to
    # AGENT_TURN_TIMEOUT_S, sixty times a night.
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None:
            raise RuntimeError("agent row disappeared mid-run")

        soul_override: dict | None = None
        if prompt_version_id:
            _pv_id, soul_override, _needs_persist = _resolve_turn_prompt_version(
                db,
                agent_id=agent_id,
                local_conversation_id=conversation_id,
                existing_prompt_version_id=prompt_version_id,
            )

        turn = build_agent_turn(
            agent=agent,
            conn_str=conn_str,
            conversation_id=conversation_id,
            job_id=run_id,
            side_effects="recorded",
            verified_session_token="",
            soul_override=soul_override,
            ledger=ledger_recorder(conn_str),
        )

    return _drive_eval_turn(turn, question=question, run_id=run_id)


def _retrieved_contexts(turn: dict, record: dict) -> list[str]:
    """The chunk texts this turn retrieved, counting what the record reports.

    ONE STRING PER CHUNK, not one repr per tool call. `result` is the audit
    capture, the tool result text cut at RETRIEVE_RESULT_CAPTURE_CHARS, which is
    below one full retrieval. Scoring it made the capture format the dominant
    term in Faithfulness and collapsed ContextPrecision's ranking to a single
    element. The loop captures the chunks the tool handed over; those are what is
    scored.

    Writes `retrieve_calls`, `retrieve_unparsed`, `retrieve_at_cap` and
    `retrieved_chunks` onto `record` as it goes, so the four counters and the
    list they describe come from one pass over one log.
    """
    from app.services.agent_loop import (  # noqa: PLC0415
        RETRIEVE_CHUNKS_KEY,
        RETRIEVE_CHUNKS_SOURCE_KEY,
        RETRIEVE_CHUNKS_UNPARSED,
    )
    from app.services.agent_tools import CHUNK_CONTENT_CHAR_LIMIT  # noqa: PLC0415

    contexts: list[str] = []
    for tc in turn.get("tool_calls_log", []):
        if tc.get("tool_name") != "retrieve" or "result" not in tc:
            continue
        record["retrieve_calls"] += 1
        if tc.get(RETRIEVE_CHUNKS_SOURCE_KEY) == RETRIEVE_CHUNKS_UNPARSED:
            record["retrieve_unparsed"] += 1
        chunks = [str(c) for c in (tc.get(RETRIEVE_CHUNKS_KEY) or []) if c]
        if any(len(c) >= CHUNK_CONTENT_CHAR_LIMIT for c in chunks):
            record["retrieve_at_cap"] = True
        contexts.extend(chunks)
    record["retrieved_chunks"] = len(contexts)
    return contexts


def _invoke_agent_for_scenarios(
    *,
    agent_id: str,
    conn_str: str,
    run_id: str,
    scenarios: list[dict],
    prompt_version_id: str | None,
) -> tuple[list[dict], dict]:
    """Drive the agent over the run's scenarios. Returns (scored_rows, observation).

    `scored_rows` is the subset that produced a response, each carrying the
    agent's own `agent_response` and the contexts the AGENT retrieved. Those are
    the only rows handed to Ragas. A row that is not in this list was not scored
    — not scored 0, not scored against its reference answer, not scored at all —
    and the observation says how many there were and why.

    Args:
        run_id: the eval run these turns belong to, and the job_id they bill under.
        scenarios: the VALID rows of the run (those carrying a label), golden
            first. Order is load-bearing: the per-run ceiling takes a prefix, so
            golden-first means the fixed set is invoked before the rotating one.
        prompt_version_id: the production prompt version this run is attributed
            to, or None.

    Returns:
        (scored_rows, summarise_agent_invocation(...)).

    No SCENARIO can raise out of here. An invocation phase where every turn fails
    yields zero scored rows and an observation saying so, and the run still
    completes and still records its provenance — which is what lets the deploy
    gate refuse it for a stated reason instead of blocking on an absence.

    The one exception is the concurrency guard below, and it is deliberate: that
    is a programming error in this file, not a runtime condition, and it fires
    before any turn has cost anything.
    """
    from app.services.agent_loop import (  # noqa: PLC0415
        RETRIEVE_RESULT_CAPTURE_CHARS,
        log_pii_firewall,
    )
    from app.services.agent_tools import (  # noqa: PLC0415
        CHUNK_CONTENT_CHAR_LIMIT,
        get_recorded_side_effects,
        reset_side_effect_context,
    )
    from app.worker.tasks.runtime.agent import AGENT_TURN_TIMEOUT_S  # noqa: PLC0415

    # The provenance says concurrency=1 and the loop below is sequential. Rather
    # than let those two drift into disagreement — a run whose record claims a
    # bound it did not run under is this phase's whole subject — raise. 4 GB of
    # RAM and one turn's worth of retrieval in flight is why the number is 1.
    if AGENT_INVOCATION_CONCURRENCY != 1:
        raise RuntimeError(
            "AGENT_INVOCATION_CONCURRENCY is "
            f"{AGENT_INVOCATION_CONCURRENCY}, but this loop invokes scenarios "
            "one at a time. Change the loop in the same edit, or the run's "
            "provenance describes a bound nothing enforced."
        )

    invocable = scenarios[:AGENT_INVOCATION_MAX_CALLS_PER_RUN]
    skipped = scenarios[AGENT_INVOCATION_MAX_CALLS_PER_RUN:]
    skipped_golden = sum(1 for s in skipped if dataset_of(s.get("dataset")) == DATASET_GOLDEN)
    if skipped:
        log.warning(
            "run_eval_suite.invocation_ceiling_reached",
            agent_id=agent_id,
            invoked=len(invocable),
            skipped=len(skipped),
            skipped_golden=skipped_golden,
            ceiling=AGENT_INVOCATION_MAX_CALLS_PER_RUN,
            detail=(
                "golden rows beyond the ceiling were not invoked — the paired "
                "per-item delta does not cover them this run"
                if skipped_golden
                else "exploratory rows beyond the ceiling were not invoked"
            ),
        )

    records: list[dict] = []
    scored_rows: list[dict] = []
    try:
        for scenario in invocable:
            # THE SINK IS EMPTIED BEFORE THE TURN, NOT ONLY INSIDE IT.
            # build_agent_turn resets it on entry, but everything
            # _run_one_eval_turn does BEFORE reaching the seam can raise:
            # get_sync_db(), the agent row lookup (which raises when the row is
            # gone), _resolve_turn_prompt_version. The unconditional read below
            # then returned the PREVIOUS scenario's sink, so a scenario 5 that
            # attempted a refund and a scenario 6 whose control-DB session
            # blipped produced two transactional.adapter entries, the second
            # carrying scenario_id 's6' for an attempt s6 never made — a
            # fabricated observation in the exact confusion-matrix cell the
            # recording exists to populate.
            reset_side_effect_context()
            record: dict = {
                "scenario_id": str(scenario.get("id", "")),
                "responded": False,
                "scorable": False,
                "error": None,
                "error_message": None,
                "retrieve_calls": 0,
                "retrieve_at_cap": False,
                "retrieve_unparsed": 0,
                "retrieved_chunks": 0,
                "side_effects": [],
                # The firewall's reading of this turn, in the seam's own
                # vocabulary: None, or "email" / "card" / "sa_id" (#103).
                "pii_detector": None,
            }
            turn: dict | None = None
            try:
                turn = _run_one_eval_turn(
                    agent_id=agent_id, conn_str=conn_str, run_id=run_id,
                    question=scenario.get("question", ""), prompt_version_id=prompt_version_id,
                )
            except Exception as exc:
                # EXCLUDED AND COUNTED, never scored 0 — the lesson
                # tests/evals/calibration/compute_correlation.py:485 learned
                # about a judge that errors, applied one layer earlier. A zero
                # here would move every metric with the turn's failure rate
                # rather than the agent's behaviour, in the direction that reads
                # as a quality regression. The message is composed, not copied.
                record["error"], record["error_message"] = _failure_of(exc)
                log.warning(
                    "run_eval_suite.scenario_invocation_failed",
                    agent_id=agent_id,
                    scenario_id=record["scenario_id"],
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

            # Read on BOTH paths and before the next turn resets the sink: a
            # scenario that drove the agent to attempt a refund and then timed
            # out still observed the attempt, and the attempt is the eval signal.
            record["side_effects"] = get_recorded_side_effects()

            if turn is not None:
                contexts = _retrieved_contexts(turn, record)

                # WHAT THE FIREWALL DID TO THIS ANSWER, counted and logged
                # (#103). `response_text` below is the SERVED text, so a
                # deflected scenario is scored on the firewall's sentence and
                # nothing said which ones those were: a run whose Faithfulness
                # fell because three answers were substituted read exactly like
                # a run where the model was wrong three times.
                record["pii_detector"] = turn["pii_detector"]
                log_pii_firewall(
                    log, turn, agent_id=agent_id, run_id=run_id,
                    scenario_id=record["scenario_id"],
                )

                response_text = str(turn.get("response_text") or "")
                if response_text.strip():
                    record["responded"] = True
                # EXCLUDED AND COUNTED, one metric over. A responded turn with no
                # retrieved context scores Faithfulness / ContextPrecision /
                # ContextRecall over an empty list, which is structurally 0 or
                # NaN — and a 0 for a question the agent answered correctly from
                # its system prompt ("what are your opening hours?") is the same
                # "zero is not a low score" error the failure path already
                # refuses. summarise_agent_invocation reports these as
                # `no_retrieval` / `retrieved_nothing_scorable`; they are not
                # failures and do not depress `response_rate`.
                if record["responded"] and contexts:
                    record["scorable"] = True
                    scored_rows.append(
                        {
                            **scenario,
                            # THE LINE THAT WAS D1. It used to be row[3], the
                            # reference answer, making the label the prediction.
                            "agent_response": response_text,
                            # THE OTHER HALF OF D1. The contexts the AGENT
                            # retrieved during this turn, never the scenario's
                            # stored column — scoring faithfulness against
                            # contexts the agent never saw measures the corpus
                            # the scenario was written from, not the retrieval
                            # the customer gets. NO FALLBACK: `contexts or
                            # scenario["stored_retrieved_contexts"]` is one token
                            # of D1 restored, and it fires precisely in the case
                            # no dynamic test covers.
                            "retrieved_contexts": contexts,
                        }
                    )

            records.append(record)
    finally:
        # The mode is process-context sticky and the Celery prefork pool does not
        # isolate contextvars per task. Leaving "recorded" in force would mean the
        # next thing to run in this context stops refunding real customers with no
        # error anywhere. That is a failure a customer finds, not us. build_agent_turn
        # resets on entry too; this closes the window between the two.
        reset_side_effect_context()

    summary = summarise_agent_invocation(
        records,
        valid=len(scenarios),
        ceiling_skipped=len(skipped),
        ceiling_skipped_golden=skipped_golden,
        per_turn_timeout_s=AGENT_TURN_TIMEOUT_S,
        # Two caps, and only the second bounds the evidence the judge saw. The
        # first bounds `tool_calls_log[*]["result"]`, the audit copy, which five
        # 2000-char chunks exceed by construction — reporting it as THE context
        # cap made `retrieved_context_at_cap` ~100% on every retrieving turn and
        # therefore a constant dressed as an observation.
        audit_capture_char_cap=RETRIEVE_RESULT_CAPTURE_CHARS,
        retrieved_context_chunk_char_cap=CHUNK_CONTENT_CHAR_LIMIT,
    )
    log.info(
        "run_eval_suite.invocation_complete",
        agent_id=agent_id,
        status=summary["status"],
        attempted=summary["attempted"],
        responded=summary["responded"],
        scorable=summary["scorable"],
        failed=summary["failed"],
        empty=summary["empty"],
        no_retrieval=summary["no_retrieval"],
        retrieved_context_unparsed=summary["retrieved_context_unparsed"],
        response_rate=summary["response_rate"],
        coverage_rate=summary["coverage_rate"],
        ceiling_skipped=summary["ceiling_skipped"],
    )
    return scored_rows, summary


# ---------------------------------------------------------------------------
# EVL-04: run_eval_suite_beat — beat dispatcher (D-19 LOCKED)
# Task name must match beat_schedule entry in celery_app.py exactly.
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=1,
    default_retry_delay=60,
    queue="runtime",
    name="app.worker.tasks.runtime.eval.run_eval_suite_beat",
)
def run_eval_suite_beat(self) -> dict:
    """Beat-triggered dispatcher: find all status='ready' agents and dispatch run_eval_suite per agent.

    Queries the control DB for agents with status='ready' and fans out one
    run_eval_suite task per agent. No conn_str is passed — the per-agent task
    fetches and decrypts at runtime (CTL-08).

    No idempotency guard needed here — the nightly beat fires once at 02:00 UTC;
    duplicate dispatches are harmless because run_eval_suite itself is idempotent.

    Returns:
        {"dispatched": int} — number of per-agent tasks dispatched.
    """
    with get_sync_db() as db:
        agents = db.execute(
            select(Agent).where(Agent.status == "ready")
        ).scalars().all()

    dispatched = 0
    for agent in agents:
        run_eval_suite.apply_async(
            kwargs={"agent_id": str(agent.id)},
            queue="runtime",
        )
        dispatched += 1

    log.info("run_eval_suite_beat.dispatched", count=dispatched)
    return {"dispatched": dispatched}


def _run_report(
    result,
    *,
    provenance: dict,
    invocation: dict,
    composition: dict,
    dataset_column_available: bool,
    config_recorded: bool,
    invocation_recorded: bool,
    result_recorded: bool,
) -> dict:
    """What a completed run returns: the record, plus what the record does not hold. Pure.

    `result.payload` first, so every number a caller reads is the one stored on
    `eval_runs.result` rather than a second arithmetic that agrees with it today.
    The keys after it are the ones that describe the RUN rather than its
    measurement, and each says something no reader can derive:

      * `scenario_count` is the record's `attempted`, the same number the
        console's `scenario_count` is. It was `len(valid_scenarios)` here and
        `record.attempted` there, one key with two meanings on two screens.
      * `dataset_column_available` False means the tenant predates migration
        0014, not that it holds no golden rows.
      * `promoted` is a literal 0 and `promotion_enabled` is what tells it apart
        from an enabled run that promoted nothing (no promotion writer exists).
      * `agent_invoked` is the gate-facing conjunction, and `agent_invocation` is
        the whole observation beside it, carrying the rates and the bounds the
        run ran under that the record's counters do not.
      * `config_recorded`, `invocation_recorded` and `result_recorded` are False
        when a write did not land. Each failure leaves the run claiming LESS
        than it did, which is the direction that costs a blocked deploy rather
        than a shipped tautology.
    """
    return {
        **result.payload,
        "scenario_count": result.attempted,
        "dataset_column_available": dataset_column_available,
        "golden_set_present": composition["golden_set_present"],
        "promoted": 0,
        "config_recorded": config_recorded,
        "promotion_enabled": VERIFIED_QA_PROMOTION_DECISION["enabled"],
        "promotion_disabled_reason": VERIFIED_QA_PROMOTION_DECISION["reason"],
        "agent_invoked": provenance["agent_invoked"],
        "agent_invocation": invocation,
        "invocation_recorded": invocation_recorded,
        "result_recorded": result_recorded,
    }


# ---------------------------------------------------------------------------
# EVL-02 / EVL-03 / EVL-05: run_eval_suite — per-agent eval run (D-10 LOCKED)
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.eval.run_eval_suite",
)
def run_eval_suite(self, agent_id: str) -> dict:
    """Per-agent eval run. Records the run on production and scores without
    touching tenant data. Receives agent_id str and no conn_str in args
    (CTL-08 / D-18).

    Sequence:
        1. Idempotency guard — skip if a 'running' eval_run for this agent
           was created within the last 10 minutes.
        2. Fetch agent from control DB; decrypt conn_str at runtime.
        3. Fetch scenarios from PRODUCTION: EVERY golden row, unsampled, plus a
           rotating exploratory sample of EXPLORATORY_SAMPLE_SIZE. Mine new
           production scenarios.
        4. Collect the configuration tuple, then insert the eval_run row on
           PRODUCTION with it (status='running').
        5. try: INVOKE THE AGENT once per valid scenario (recorded side effects)
                → patch the observation onto the run's config on PRODUCTION
                → run Ragas eval over the rows that answered (no database)
                → write results to PRODUCTION → mark complete on PRODUCTION.
           except: mark failed on PRODUCTION.

    No verified_qa promotion happens here. See the module docstring for the
    two locks and eval_service.VERIFIED_QA_PROMOTION_DECISION for the
    recorded reason.

    A HUMAN-LABELLED ROW CHANGES NOTHING ABOUT THAT (D6 P3). Labelling makes a
    row ELIGIBLE TO THE SELECTOR above — it acquires a reference_answer, which
    is the one thing `WHERE reference_answer != ''` was excluding it for. It
    does not touch `dataset`, so it joins the exploratory half and never the
    golden one (membership of the golden set is asserted, never inherited); and
    it does not touch `source`, so it stays unpromotable, on top of the decision
    flag now returned as `promotion_enabled`.

    ELIGIBLE IS NOT PRESENT, AND THE FIRST VERSION OF THIS PARAGRAPH CONFLATED
    THE TWO (D6 P3 review, finding 3). It said a labelled row "is fetched,
    counted in `valid`, put to the agent and scored", unconditionally. That holds
    only while the eligible exploratory pool is SMALLER than
    EXPLORATORY_SAMPLE_SIZE. `_EXPLORATORY_SQL` is `ORDER BY RANDOM() LIMIT 30`:
    at 200 eligible rows a new label does not raise `attempted` at all — it
    changes WHICH rows are drawn, and the labelled row has a 30/200 chance of
    being drawn on any given night. Nothing in the run report tells the owner
    their label was not exercised, and no run preferentially draws a fresh label
    (`BACKLOG 4.14`), so the feedback latency of the labelling loop is unbounded
    above the sample size. The golden half is unsampled, but labelling cannot
    reach it.

    A LABEL CHANGES WHAT THE DEPLOY GATE READS (D6 P3 review, finding 5). This
    is the live downstream consumer, and the earlier analysis stopped at
    verified_qa, which has no caller. The chain:

        this task -> build_eval_result -> eval_runs.result on PRODUCTION
        deployment_service._fetch_eval_summary_sync lifts that record, per
            dataset, pooling nothing (#51 slice 4) -> `pass_rates`
        run_deployment_checklist puts eval_summary on the orchestrator payload
        the orchestrator applies "all eval metrics >= 0.85" (ship) and
            "[0.70, 0.85)" (warn) — prose in _DEPLOYMENT_SYSTEM_PROMPT

    `apply_signal_evidence_gate` does NOT read the rates: it is a one-way floor
    on the signal's PRESENCE (measured, agent_invoked) and on red-team severity,
    so it can only make a recommendation more conservative and can never rescue
    a rate that labelling depressed.

    And labelling depresses rates by design: the queue is populated with mined
    production FAILURES, so answering them adds hard negatives to the scored
    population — an owner can lower their own pass rates by doing the work, with
    nothing connecting the refused deploy back to their labelling. The inverse is
    equally live: an owner who pastes the agent's own answer back in as the
    reference inflates faithfulness. And an owner-authored answer is not grounded
    in the retrieved corpus by construction, so context_recall over labelled rows
    measures something different from context_recall over Haiku-written
    references — averaged into one dataset mean, because no selector projects
    `label_trust_tier` (`BACKLOG 4.12`).

    Args:
        agent_id: UUID string of the agent to evaluate.

    Returns:
        On success, `EvalResult.payload` (#51) plus "scenario_count", the
        record's `attempted` under the console's name for it, and the keys the
        record does not carry: "dataset_column_available", "golden_set_present",
        "promoted", "config_recorded", "promotion_enabled",
        "promotion_disabled_reason", "agent_invoked", "agent_invocation",
        "invocation_recorded", "result_recorded". Every number is the record's.
        {"status": "already_running"}                            on idempotent skip.
        {"status": "no_scenarios", "run_id", "run_recorded", "attempted",
         "valid", "scored", "dataset_column_available"}          when nothing was
            selected. The empty run is still recorded terminally on production —
            "this tenant has no scenarios" and "nobody has ever evaluated this
            agent" are different claims and the deploy gate can only tell them
            apart if the empty run left a row.
        {}                                                        on retry exhaustion.

    (attempted, valid, scored) are three different claims and all three are
    reported: rows fetched, rows carrying a label (the DENOMINATOR), and rows
    Ragas returned a real number for. `scored < valid` means the run measured
    less than it attempted, and that is invisible from any one of them alone.
    """
    # ------------------------------------------------------------------
    # Step 1 — Idempotency guard: check for a recent 'running' eval run
    # The kind column is used as the per-agent idempotency key: 'm6:{agent_id}'
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_project_id or not agent.neon_connection_string:
            log.error(
                "run_eval_suite.agent_not_found_or_unconfigured",
                agent_id=agent_id,
            )
            return {}

        # Decrypt conn_str at runtime — never stored, never passed as arg (CTL-08)
        conn_str = fernet_decrypt(agent.neon_connection_string)
        tenant_id = str(agent.tenant_id)  # read while the session is open

    # Check eval_runs table on tenant DB for a recent running run.
    #
    # THE WINDOW HAS TO COVER A RUN THAT CONSUMES ITS OWN CEILING. It was a flat
    # 10 minutes, written when a run was seconds of arithmetic. P2 made the worst
    # case AGENT_INVOCATION_MAX_CALLS_PER_RUN x AGENT_TURN_TIMEOUT_S — 90 minutes
    # — so a 10-minute window let a redelivered or re-dispatched message start a
    # SECOND concurrent invocation of the same agent while the first was still
    # running: two live agents, two sets of turns, two eval_runs rows. Derived
    # from the same two constants the run stamps on itself rather than guessed
    # beside them.
    idempotency_window_s = (
        AGENT_INVOCATION_MAX_CALLS_PER_RUN * _agent_turn_timeout_s()
        + EVAL_RUN_IDEMPOTENCY_SLACK_S
    )
    try:
        _check_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with _check_conn.cursor() as _cur:
                _cur.execute(
                    """
                    SELECT id FROM eval_runs
                    WHERE kind = %s
                      AND status = 'running'
                      AND started_at > NOW() - (%s * INTERVAL '1 second')
                    LIMIT 1
                    """,
                    (f"m6:{agent_id}", idempotency_window_s),
                )
                _existing = _cur.fetchone()
        finally:
            _check_conn.close()

        if _existing:
            log.info(
                "run_eval_suite.idempotent_skip",
                agent_id=agent_id,
                window_s=idempotency_window_s,
            )
            return {"status": "already_running"}
    except Exception as exc:
        # If we cannot check, proceed — idempotency guard is best-effort
        log.warning(
            "run_eval_suite.idempotency_check_failed",
            agent_id=agent_id,
            error=str(exc),
        )

    # ------------------------------------------------------------------
    # Step 3 — Fetch eval scenarios from tenant DB (PRODUCTION).
    #
    # TWO QUERIES, NOT ONE SAMPLE. This used to be a single
    # `ORDER BY RANDOM() LIMIT 30`, which drew a different 30 rows every night:
    # run-to-run variance was dominated by the draw rather than by anything the
    # agent did, so a regression could not be seen. The golden rows now run in
    # FULL on every eval — the same items scored twice give a paired per-item
    # delta — and the exploratory rows keep rotating, which is what stops the
    # fixed set from being overfit. The two are kept apart all the way to the
    # report; averaging them would throw away exactly the property the split
    # exists to create.
    #
    # `reference_answer != ''` survives in ALL THREE queries. It is the empty-label
    # exclusion that makes an unlabelled row (a mined production failure, an
    # owner-filed failing trace — see bench.NO_GROUND_TRUTH) inert to this
    # selector by construction, and it is pinned across module boundaries by
    # test_the_scenario_is_inert_to_the_eval_selector_by_construction.
    #
    # A tenant DB that predates migration 0014 has no `dataset` column at all.
    # That is a degradation, not an outage: the fallback below is the pre-0014
    # single query, every row is then exploratory because the column that could
    # say otherwise does not exist, and `dataset_column_available` records which
    # of the two happened so "no golden rows" and "no way to tell" stay
    # different claims. Same tolerance shape as insert_eval_run's pre-0013
    # fallback, for the same reason: tenants are migrated at provision time.
    # ------------------------------------------------------------------
    _GOLDEN_SQL = """
        SELECT id, source, question, reference_answer, retrieved_contexts, dataset
        FROM eval_scenarios
        WHERE reference_answer != ''
          AND dataset = %(golden)s
        ORDER BY created_at
    """
    _EXPLORATORY_SQL = """
        SELECT id, source, question, reference_answer, retrieved_contexts, dataset
        FROM eval_scenarios
        WHERE reference_answer != ''
          AND (dataset IS NULL OR dataset <> %(golden)s)
        ORDER BY RANDOM()
        LIMIT %(limit)s
    """
    _PRE_0014_SQL = """
        SELECT id, source, question, reference_answer, retrieved_contexts, NULL
        FROM eval_scenarios
        WHERE reference_answer != ''
        ORDER BY RANDOM()
        LIMIT %(limit)s
    """

    dataset_column_available = True
    try:
        _scen_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            try:
                with _scen_conn.cursor() as _cur:
                    _cur.execute(_GOLDEN_SQL, {"golden": DATASET_GOLDEN})
                    rows = list(_cur.fetchall())
                    _cur.execute(
                        _EXPLORATORY_SQL,
                        {"golden": DATASET_GOLDEN, "limit": EXPLORATORY_SAMPLE_SIZE},
                    )
                    rows.extend(_cur.fetchall())
            except psycopg2.errors.UndefinedColumn:
                # The aborted transaction must be rolled back before the
                # connection will accept another statement.
                _scen_conn.rollback()
                dataset_column_available = False
                log.warning(
                    "run_eval_suite.dataset_column_absent",
                    agent_id=agent_id,
                    detail=(
                        "tenant DB predates alembic_tenant 0014 — no golden set "
                        "is held fixed for this run"
                    ),
                )
                with _scen_conn.cursor() as _cur:
                    _cur.execute(_PRE_0014_SQL, {"limit": EXPLORATORY_SAMPLE_SIZE})
                    rows = list(_cur.fetchall())
        finally:
            _scen_conn.close()
    except Exception as exc:
        log.error(
            "run_eval_suite.fetch_scenarios_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    scenarios = [
        {
            "id": str(row[0]),
            "source": row[1],
            "question": row[2],
            "reference_answer": row[3],
            # NOT `retrieved_contexts`, AND THE NAME IS THE GUARD. run_ragas_eval
            # reads `retrieved_contexts` off each sample; this column holds the
            # chunks the SCENARIO was written from, which for a source='generated'
            # row are the exact chunks Haiku was told to answer from
            # (scenario_service.py:118). Scoring the agent's answer against them
            # measures the corpus the question came out of rather than the
            # retrieval the customer gets, and scoring the REFERENCE answer
            # against them was D1 itself. The key is carried under a name the
            # scorer does not read so that reconnecting the two is an edit
            # somebody has to make on purpose.
            "stored_retrieved_contexts": row[4] if isinstance(row[4], list) else [],
            # NULL (never designated) resolves to exploratory — membership of
            # the golden set is asserted, never inherited.
            "dataset": dataset_of(row[5] if len(row) > 5 else None),
            # NO `agent_response` KEY. This is where D1 lived:
            #     # For M6: use reference_answer as proxy agent_response …
            #     "agent_response": row[3],   # row[3] IS reference_answer
            # It is set by _invoke_agent_for_scenarios, from the agent's own
            # turn, and ONLY on rows that produced one. A row that never reached
            # the agent has no response key at all rather than a plausible
            # placeholder, so the failure mode is a missing row in the scored
            # set — visible in (attempted, valid, scored) — instead of a number
            # that looks like a measurement.
        }
        for row in rows
    ]
    composition = dataset_composition(
        scenarios, dataset_column_available=dataset_column_available
    )
    if composition["golden_over_soft_ceiling"]:
        # Reported, never silently truncated: cutting the golden set down would
        # break the paired comparison it exists for.
        log.warning(
            "run_eval_suite.golden_set_over_soft_ceiling",
            agent_id=agent_id,
            golden_attempted=composition[DATASET_GOLDEN]["attempted"],
        )

    # Mine new production scenarios and store them
    try:
        with get_sync_db() as control_db:
            mined = mine_production_scenarios(agent_id, conn_str, control_db)
        if mined:
            store_scenarios(mined, conn_str)
    except Exception as mine_exc:
        # Mining is best-effort — never blocks the eval run
        log.warning(
            "run_eval_suite.mine_failed",
            agent_id=agent_id,
            error=str(mine_exc),
        )

    if not scenarios:
        log.warning(
            "run_eval_suite.no_scenarios",
            agent_id=agent_id,
            dataset_column_available=dataset_column_available,
        )
        # A RUN THAT COVERED NOTHING STILL HAPPENED (P2 review). This path used
        # to return without writing anything, so production held no eval_runs
        # row and the deploy gate reported EVAL_SIGNAL_NO_RUNS — the same signal
        # as an agent nobody has ever tried to evaluate, and one that blocks the
        # deploy with nothing on the record to explain why. Two consequences,
        # both bad: the owner is told "quality has never been measured" when the
        # truth is "this tenant has no scenarios to measure against", and
        # run_deployment_checklist's day-1 remedy (dispatching the first eval,
        # step 4b there) would re-fire on every readiness check forever because
        # the state it keys off never changes.
        #
        # So the empty run is recorded terminally, with its composition
        # (attempted=0, valid=0) stamped on it. The gate then reads a completed
        # run that produced no valid score — EVAL_SIGNAL_NO_VALID_SCORES, which
        # still blocks, honestly, and converges.
        empty_run_id = str(uuid.uuid4())
        run_recorded = False
        try:
            empty_attribution = build_eval_run_config(
                agent_id, conn_str, dataset=composition
            )
            insert_eval_run(
                empty_run_id,
                f"m6:{agent_id}",
                empty_attribution["prompt_version_id"],
                empty_attribution["config"],
                conn_str,
            )
            update_eval_run_status(
                empty_run_id, "complete", finished_at=True, conn_str=conn_str
            )
            run_recorded = True
        except Exception as record_exc:
            # Best-effort: failing to record an empty run must not turn a
            # nothing-to-do into a retry storm. It is logged at error level
            # because the consequence — an unexplained permanent block — is the
            # thing this write exists to prevent.
            log.error(
                "run_eval_suite.empty_run_record_failed",
                agent_id=agent_id,
                run_id=empty_run_id,
                error=str(record_exc),
                detail=(
                    "no eval_runs row explains why this agent's deploy is "
                    "blocked"
                ),
            )
        # The denominators travel even on the empty path. A run that scored
        # nothing must be readable as such rather than as an absent key a caller
        # might treat as "not applicable".
        return {
            "status": "no_scenarios",
            "run_id": empty_run_id if run_recorded else None,
            "run_recorded": run_recorded,
            "attempted": 0,
            "valid": 0,
            "scored": 0,
            "dataset_column_available": dataset_column_available,
        }

    # ------------------------------------------------------------------
    # Step 5 — Insert the eval_run row on PRODUCTION, stamped with the
    # configuration tuple this run is an assertion about (migration 0013).
    #
    # build_eval_run_config never raises: an unattributable run is worth less
    # than an attributed one but far more than no run at all, so a collector
    # failure degrades attribution and names itself in config["unavailable"].
    # ------------------------------------------------------------------
    run_id = str(uuid.uuid4())
    attribution = build_eval_run_config(agent_id, conn_str, dataset=composition)
    try:
        config_recorded = insert_eval_run(
            run_id,
            f"m6:{agent_id}",
            attribution["prompt_version_id"],
            attribution["config"],
            conn_str,
        )
    except Exception as exc:
        log.error(
            "run_eval_suite.insert_eval_run_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    # Set the moment the first turn could have run. A retry after that point
    # re-invokes the whole set — see the `except` below.
    agent_was_invoked = False
    try:
        # Filter scenarios — reference_answer already required by the SQL query above,
        # but double-check here for safety. This is the VALID set: rows that
        # were fetched and carry a label, i.e. the rows that can be scored at
        # all. It is the denominator, and it is not the same number as the rows
        # fetched (attempted) or the rows Ragas came back with (scored).
        valid_scenarios = [s for s in scenarios if s.get("reference_answer")]

        # ------------------------------------------------------------------
        # THE AGENT RUNS (audit D1). One turn per valid scenario, through the
        # same seam run_agent_turn uses, with side_effects="recorded" so a
        # scenario in which the agent decides to refund records the attempt
        # instead of moving money. Rows that produced no response are excluded
        # here and counted in `invocation` — never scored 0, and never scored
        # against their own reference answer, which is the defect being closed.
        # ------------------------------------------------------------------
        scored_scenarios, invocation = _invoke_agent_for_scenarios(
            agent_id=agent_id, conn_str=conn_str, run_id=run_id,
            scenarios=valid_scenarios,
            prompt_version_id=attribution["prompt_version_id"],
        )
        # From here on a retry would re-run every turn above. See the `except`.
        agent_was_invoked = True

        # WRITTEN BEFORE SCORING, DELIBERATELY. The invocation is the expensive,
        # unrepeatable half of the run; scoring can fail on a judge outage and be
        # retried. Patching the observation in first means a run that dies in
        # Ragas still carries what its agent actually did, and a run that dies
        # BEFORE this point keeps the agent_invoked=false it was inserted with —
        # so the deploy gate refuses it rather than inheriting a hopeful default.
        provenance = invocation_provenance(invocation)
        invocation_recorded = update_eval_run_config(run_id, provenance, conn_str)

        # ------------------------------------------------------------------
        # A RUN THAT DID NOT MEASURE THE AGENT WRITES NO SCORES.
        #
        # `agent_invocation.status` was 'unknown' for a below-floor run and the
        # run scored anyway: 2 surviving rows out of 40 produced 2x4 eval_results
        # rows, update_eval_run_status marked it 'complete', and
        # deployment_service._fetch_eval_summary_sync averaged them into a
        # non-empty pass_rates and returned EVAL_SIGNAL_MEASURED. The 'unknown'
        # lived in a config key that nothing outside this module reads, so
        # everything a consumer actually reads reported a pass over two
        # observations. Before P2 that state was unreachable — every fetched row
        # was always 'scored'.
        #
        # The deploy gate reads `agent_invoked` since P3 and refuses on it.
        # The refusal stays here as well, where the observation is: a record
        # whose metrics are all unmeasured reaches _fetch_eval_summary_sync as
        # EVAL_SIGNAL_NO_VALID_SCORES, and a gated metric measured on no dataset
        # is refused by _quality_evidence_warning (#51 slice 4). Two floors under
        # one hole, because the plan's "reports unknown, never pass" has to be
        # true of the run and not only of one key.
        #
        # The run still ends terminally and still carries its whole invocation
        # observation, so "this run measured too little" stays readable — it is
        # the SCORES that are withheld, not the record.
        # ------------------------------------------------------------------
        # Annotated, because the two branches assign different literal types and
        # the join would otherwise be dict[str, object] — which makes both
        # `results["scores"]` and `results["judge_records"]` an `object` that
        # summarise_run_validity and write_eval_results reject.
        results: dict
        if invocation["status"] != AGENT_INVOCATION_MEASURED:
            log.warning(
                "run_eval_suite.below_measurement_floor",
                agent_id=agent_id,
                run_id=run_id,
                invocation_status=invocation["status"],
                attempted=invocation["attempted"],
                responded=invocation["responded"],
                scorable=invocation["scorable"],
                response_rate=invocation["response_rate"],
                min_response_rate=invocation["min_response_rate"],
                min_scored_observations=invocation["min_scored_observations"],
                detail=(
                    "no eval_results written and no judge call billed. A run below the "
                    "floor is not a measurement, and writing its scores would make the "
                    "deploy gate read it as one"
                ),
            )
            update_eval_run_status(run_id, "complete", finished_at=True, conn_str=conn_str)
            results = dict(_NOTHING_SCORED)
        else:
            # No connection string is passed: scoring reads nothing. It scores the
            # AGENT'S responses against the contexts the AGENT retrieved, and each
            # judge call bills this run through the recorder.
            results = run_ragas_eval(scored_scenarios, _run_ledger(tenant_id, agent_id, run_id, conn_str))

            # Observations land on PRODUCTION because the branch below is about
            # to be destroyed, and it is the JUDGE RECORDS that go, not `scores`.
            write_eval_results(run_id, results["judge_records"], conn_str)
            update_eval_run_status(
                run_id, "complete", finished_at=True, conn_str=conn_str
            )

        # (attempted, valid, scored) for the run and for each dataset. Computed
        # over the FETCHED set, not the valid one, so the two counts stay
        # distinguishable — a run that fetched 40 rows and could score 12 has
        # measured far less than a run that fetched 12, and a report that shows
        # only one of the two numbers cannot say which happened.
        validity = summarise_run_validity(scenarios, results["scores"])

        # THE RUN'S NUMBERS, DERIVED ONCE (#51). What stood here was forty-nine
        # lines of hand-assembled dict, and it was the third derivation of one
        # run's figures: api/v1/evals.py recomputes them with COUNT/AVG and
        # deployment_service._fetch_eval_summary_sync recomputes them again, and
        # nothing held the three to each other. The record is built from the two
        # summaries above and stored on the run, and `_run_report` returns its
        # payload — so this task cannot report a number the row does not hold.
        #
        # The ledger read is what lets a run say what it cost, and it fails soft:
        # no rows means the cost is unknown, never that the run was free.
        result = build_eval_result(
            run_id=run_id,
            agent_id=agent_id,
            prompt_version_id=attribution["prompt_version_id"],
            validity=validity,
            invocation=invocation,
            ledger=read_run_ledger(run_id, conn_str),
            scenarios=scenarios,
            judge_records=results["judge_records"],
        )
        result_recorded = write_eval_result(run_id, result, conn_str)

        log.info(
            "run_eval_suite.complete",
            agent_id=agent_id,
            run_id=run_id,
            attempted=result.attempted,
            valid=result.valid,
            scored=result.scored,
            golden_valid=validity["datasets"][DATASET_GOLDEN]["valid"],
            golden_set_present=composition["golden_set_present"],
            dataset_column_available=dataset_column_available,
            config_recorded=config_recorded,
            promoted=0,
            promotion_enabled=VERIFIED_QA_PROMOTION_DECISION["enabled"],
            agent_invoked=provenance["agent_invoked"],
            invocation_status=invocation["status"],
            invocation_responded=invocation["responded"],
            invocation_attempted=invocation["attempted"],
            invocation_recorded=invocation_recorded,
            result_recorded=result_recorded,
            cost_measured=result.cost.measured,
        )
        return _run_report(
            result,
            provenance=provenance,
            invocation=invocation,
            composition=composition,
            dataset_column_available=dataset_column_available,
            config_recorded=config_recorded,
            invocation_recorded=invocation_recorded,
            result_recorded=result_recorded,
        )

    except Exception as exc:
        log.error(
            "run_eval_suite.eval_failed",
            agent_id=agent_id,
            run_id=run_id,
            error=str(exc),
            agent_was_invoked=agent_was_invoked,
        )
        _mark_failed_on_production(run_id, conn_str, agent_id)
        # A RETRY AFTER THE INVOCATION RE-BUYS THE INVOCATION. `max_retries=2`
        # meant a judge outage in run_ragas_eval re-entered this task body, drew
        # a fresh run_id and put all sixty scenarios to the agent again — one
        # nightly dispatch costing three times the ceiling the run stamps on
        # itself, and no field on the run expressing that. Losing one night's
        # scores to a judge outage is the cheaper failure by two orders of
        # magnitude, and tonight's beat repeats tomorrow. Retries before the
        # first turn (an insert failure) are unaffected and
        # still cost nothing.
        if agent_was_invoked:
            log.error(
                "run_eval_suite.not_retrying_after_invocation",
                agent_id=agent_id,
                run_id=run_id,
                detail=(
                    "the agent was already invoked for this run; retrying would "
                    "re-run every turn. The run is recorded failed and the "
                    "next nightly dispatch is the retry."
                ),
            )
            return {}
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


# ---------------------------------------------------------------------------
# EVL-02: generate_eval_suite — build initial scenario library (D-14 LOCKED)
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.eval.generate_eval_suite",
)
def generate_eval_suite(self, agent_id: str) -> dict:
    """Generate initial eval scenario suite for a newly provisioned agent.

    Dispatched as part of agent build chain (D-14). Receives agent_id — no conn_str
    in args (CTL-08). Fetches and decrypts conn_str at runtime.

    Idempotency: if eval_scenarios already has >= 10 rows for this tenant, skip.
    This prevents duplicate scenario generation on Celery retry (acks_late rule).

    Args:
        agent_id: UUID string of the newly provisioned agent.

    Returns:
        {"agent_id": str, "scenario_count": int}  on success.
        {"status": "already_generated", "count": int}  on idempotent skip.
        {}                                              on retry exhaustion.
    """
    # ------------------------------------------------------------------
    # Fetch agent from control DB and decrypt conn_str at runtime (CTL-08)
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_connection_string:
            log.error(
                "generate_eval_suite.agent_not_found_or_unconfigured",
                agent_id=agent_id,
            )
            return {}

        # T-02-01: conn_str is never logged — local variable only (D-18)
        conn_str = fernet_decrypt(agent.neon_connection_string)
        tenant_id = str(agent.tenant_id)  # read while the session is open

    # ------------------------------------------------------------------
    # Idempotency guard: skip if eval_scenarios already has >= 10 rows
    # ------------------------------------------------------------------
    try:
        _idm_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with _idm_conn.cursor() as _cur:
                _cur.execute("SELECT COUNT(*) FROM eval_scenarios")
                count = _cur.fetchone()[0]
        finally:
            _idm_conn.close()

        if count >= 10:
            log.info(
                "generate_eval_suite.idempotent_skip",
                agent_id=agent_id,
                count=count,
            )
            return {"status": "already_generated", "count": count}

    except Exception as exc:
        log.warning(
            "generate_eval_suite.idempotency_check_failed",
            agent_id=agent_id,
            error=str(exc),
        )

    # ------------------------------------------------------------------
    # Generate scenario suite via scenario_service (Claude Haiku — D-12)
    # ------------------------------------------------------------------
    try:
        count = generate_eval_suite_for_agent(agent_id, tenant_id, conn_str, num_scenarios=20)
        log.info(
            "generate_eval_suite.complete",
            agent_id=agent_id,
            count=count,
        )
        return {"agent_id": agent_id, "scenario_count": count}

    except Exception as exc:
        log.error(
            "generate_eval_suite.failed",
            agent_id=agent_id,
            error=str(exc),
        )
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
