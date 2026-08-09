"""M6 eval tasks — nightly eval suite, per-agent Ragas eval, scenario generation.

All tasks: acks_late=True, runtime queue, no conn_str in args (CTL-08).
Neon branch created per eval run, deleted in finally block (D-10).
Ragas 0.4.x only — D-01 through D-04.

Where each write lands
----------------------
D-10 ("never evaluate against production") is correct for tenant data and was
over-applied to the run's own results: `eval_results`, the terminal `eval_runs`
status and the verified_qa promotion were all written to the Neon branch that
this task then deletes in `finally`. Production consequently never learned that
any run finished — a successful run left status='running' forever, and
`eval_results` never existed on production at all.

An eval result is an OBSERVATION ABOUT a run, not tenant data. So:

    scenario read / eval_runs / results  -> conn_str         (PRODUCTION)
    scoring (run_ragas_eval)             -> no database at all
    branch deletion in finally           -> unchanged, every path

The middle line is the correction to an earlier version of this docstring,
which said scoring ran against `branch_conn_str`. It does not, and never did:
run_ragas_eval scores rows that are already in memory against the judge API and
never referenced the connection string it was handed. The scenario rows
themselves are read from PRODUCTION below. So no statement in this task is ever
issued against the eval branch.

The branch is still created and still deleted in `finally`, because D-10 has to
be in place the day this eval starts invoking retrieval or the agent against
tenant data. What changed is that its ABSENCE is no longer fatal while
eval_service.EVAL_SCORING_REQUIRES_BRANCH is False: a degraded Neon endpoint
used to abandon a whole night's measurement over a resource nothing reads.

verified_qa promotion is not performed by this task at all. It is disabled
behind eval_service's label trust hierarchy, and the decision — with its reason
— is recorded on the run in `eval_runs.config` so the disablement is a statement
in the record rather than an absence a later reader has to infer.

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
constructor run_agent_turn uses (agent.build_agent_options — the seam, P1), and
the agent's own response is what gets scored. Four properties, each of which is
a way this could have gone wrong and been invisible:

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
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.eval_service import (
    AGENT_INVOCATION_CONCURRENCY,
    AGENT_INVOCATION_MAX_CALLS_PER_RUN,
    AGENT_INVOCATION_MEASURED,
    DATASET_GOLDEN,
    EVAL_RUN_IDEMPOTENCY_SLACK_S,
    EVAL_SCORING_REQUIRES_BRANCH,
    EXPLORATORY_SAMPLE_SIZE,
    VERIFIED_QA_PROMOTION_DECISION,
    build_eval_run_config,
    dataset_composition,
    dataset_of,
    insert_eval_run,
    invocation_provenance,
    run_ragas_eval,
    summarise_agent_invocation,
    summarise_run_validity,
    update_eval_run_config,
    update_eval_run_status,
    write_eval_results,
)
from app.services.neon import create_branch, delete_branch, wait_for_neon_ready
from app.services.scenario_service import (
    generate_eval_suite_for_agent,
    mine_production_scenarios,
    store_scenarios,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


def _mark_failed_on_production(run_id: str, conn_str: str, agent_id: str) -> None:
    """Best-effort terminal 'failed' status write on PRODUCTION.

    A run must end in a terminal state on production or it never happened —
    but the write itself must never derail the two things that matter more on
    an already-failing path: deleting the Neon branch (the `finally` below)
    and the caller's `self.retry`. An unguarded raise here would skip
    `raise self.retry(...)` entirely, so the task would die instead of
    retrying, and the failure would be attributed to the status write rather
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


# ---------------------------------------------------------------------------
# Invoking the agent, per scenario (audit D1 / plan P2)
# ---------------------------------------------------------------------------
# WHY THE IMPORTS BELOW ARE LAZY. `agent.py` and `agent_tools.py` both import
# `claude_agent_sdk` at module scope, and several test modules install a FAKE
# `claude_agent_sdk` into `sys.modules` at import time. Pulling either into THIS
# module's import graph would make `tests/unit/test_eval_task.py` — which has
# nothing to do with the SDK — depend on pytest's collection order for whether it
# gets the real package or a stand-in. `test_agent_options_seam.py` records that
# exact failure ("a guard whose meaning depends on collection order is not a
# guard"), and `eval_service.build_eval_run_config` already imports
# `deployment_service` inside the function body for the same class of reason.
#
# They are imported BY NAME rather than through an accessor, because the static
# half of tests/unit/test_eval_agent_invocation.py reads this module's AST to
# prove every `build_agent_options(...)` call asks for recorded side effects, and
# a computed callee has no name to read.


class _EvalEventSink:
    """The db/redis double `_run_sdk_turn` emits SSE events through.

    `_run_sdk_turn` calls `emit(job_id, "agent.tool_call", …, db, redis)` for
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


def _run_one_eval_turn(
    *,
    agent_id: str,
    conn_str: str,
    question: str,
    prompt_version_id: str | None,
) -> dict:
    """Put one scenario question to the customer agent. Returns `_run_sdk_turn`'s dict.

    Same constructor as the chat path (`build_agent_options` — the seam, P1) and
    same turn loop (`_run_sdk_turn`), so what is measured is what is served. What
    differs is stated here rather than discovered later:

      * `side_effects="recorded"` — ALWAYS, never "live". Six of the eleven tools
        the seam grants reach a real ProviderAdapter, and this loop runs nightly,
        unattended, against a real tenant.
      * `verified_session_token=""` — an eval scenario is an UNVERIFIED customer.
        Every identity-gated skill therefore refuses, which is the correct
        posture for a question that arrived with no IDV session, and it is the
        posture a mined production scenario carries no evidence against.
      * `resume=None` and a fresh conversation id per scenario — scenarios are
        independent by construction; a shared session would let scenario 12's
        answer be shaped by scenario 11.
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
    from app.worker.tasks.runtime.agent import (  # noqa: PLC0415
        AGENT_TURN_TIMEOUT_S,
        _resolve_turn_prompt_version,
        _run_sdk_turn,
        build_agent_options,
    )

    conversation_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    # The control-DB session is held only for as long as the options need it.
    # build_agent_options reads every field it wants off the agent row before it
    # returns, so the SDK turn — up to AGENT_TURN_TIMEOUT_S of it, sixty times a
    # night — runs with no session open.
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

        options = build_agent_options(
            agent=agent,
            conn_str=conn_str,
            conversation_id=conversation_id,
            job_id=job_id,
            side_effects="recorded",
            verified_session_token="",
            soul_override=soul_override,
            resume=None,
        )

    sink = _EvalEventSink()
    return asyncio.run(
        asyncio.wait_for(
            _run_sdk_turn(
                message=question,
                options=options,
                job_id=job_id,
                local_conversation_id=conversation_id,
                conn_str=conn_str,
                db=sink,
                redis=sink,
            ),
            timeout=AGENT_TURN_TIMEOUT_S,
        )
    )


def _invoke_agent_for_scenarios(
    *,
    agent_id: str,
    conn_str: str,
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
    from app.services.agent_tools import (  # noqa: PLC0415
        CHUNK_CONTENT_CHAR_LIMIT,
        get_recorded_side_effects,
        reset_side_effect_context,
    )
    from app.worker.tasks.runtime.agent import (  # noqa: PLC0415
        AGENT_TURN_TIMEOUT_S,
        RETRIEVE_CHUNKS_KEY,
        RETRIEVE_CHUNKS_SOURCE_KEY,
        RETRIEVE_CHUNKS_UNPARSED,
        RETRIEVE_RESULT_CAPTURE_CHARS,
    )

    # The provenance says concurrency=1 and the loop below is sequential. Rather
    # than let those two drift into disagreement — a run whose record claims a
    # bound it did not run under is this phase's whole subject — raise. 4 GB of
    # RAM and one Agent SDK subprocess per turn is why the number is 1.
    if AGENT_INVOCATION_CONCURRENCY != 1:
        raise RuntimeError(
            "AGENT_INVOCATION_CONCURRENCY is "
            f"{AGENT_INVOCATION_CONCURRENCY}, but this loop invokes scenarios "
            "one at a time. Change the loop in the same edit, or the run's "
            "provenance describes a bound nothing enforced."
        )

    invocable = scenarios[:AGENT_INVOCATION_MAX_CALLS_PER_RUN]
    skipped = scenarios[AGENT_INVOCATION_MAX_CALLS_PER_RUN:]
    skipped_golden = sum(
        1 for s in skipped if dataset_of(s.get("dataset")) == DATASET_GOLDEN
    )
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
            # build_agent_options resets it on entry, but everything
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
                "retrieve_calls": 0,
                "retrieve_at_cap": False,
                "retrieve_unparsed": 0,
                "retrieved_chunks": 0,
                "side_effects": [],
            }
            turn: dict | None = None
            try:
                turn = _run_one_eval_turn(
                    agent_id=agent_id,
                    conn_str=conn_str,
                    question=scenario.get("question", ""),
                    prompt_version_id=prompt_version_id,
                )
            except Exception as exc:
                # EXCLUDED AND COUNTED, never scored 0 — the lesson
                # tests/evals/calibration/compute_correlation.py:485 learned
                # about a judge that errors, applied one layer earlier. A zero
                # here would move every metric with the failure rate of the
                # Agent SDK rather than with the agent's behaviour, and it would
                # do it in the direction that looks like a quality regression.
                record["error"] = type(exc).__name__
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
                # ONE STRING PER CHUNK, not one repr per tool call. `result` is
                # the audit capture — a Python repr of the SDK content block, cut
                # at RETRIEVE_RESULT_CAPTURE_CHARS, which is below one full
                # retrieval — and scoring it made the capture format the dominant
                # term in Faithfulness and collapsed ContextPrecision's ranking
                # to a single element. agent.py decodes the framed payload back
                # into the chunks the agent was shown; those are what is scored.
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
        # error anywhere — a failure a customer finds, not us. build_agent_options
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
        # The served path deflects a response that trips the PII firewall
        # (agent.py's scan_response) before a customer sees it. The eval does
        # NOT, and the reason is that the deflection is not an answer: scoring it
        # would measure the firewall's hit rate as if it were the agent's
        # grounding. Recorded rather than left implicit, because it is a real
        # difference between the text scored here and the text a customer reads.
        pii_firewall_applied=False,
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
    """Per-agent eval run. Records the run on production, creates and deletes a
    Neon branch (D-10), and scores without touching either. Receives agent_id
    str — no conn_str in args (CTL-08 / D-18).

    Sequence:
        1. Idempotency guard — skip if a 'running' eval_run for this agent
           was created within the last 10 minutes.
        2. Fetch agent from control DB; decrypt conn_str at runtime.
        3. Fetch scenarios from PRODUCTION: EVERY golden row, unsampled, plus a
           rotating exploratory sample of EXPLORATORY_SAMPLE_SIZE. Mine new
           production scenarios.
        4. Collect the configuration tuple, then insert the eval_run row on
           PRODUCTION with it (status='running').
        5. Create the Neon branch. Readiness is probed only if scoring is going
           to connect to it, and a branch that cannot be created is fatal only
           then — see EVAL_SCORING_REQUIRES_BRANCH and the block comment below.
        6. try: INVOKE THE AGENT once per valid scenario (recorded side effects)
                → patch the observation onto the run's config on PRODUCTION
                → run Ragas eval over the rows that answered (no database)
                → write results to PRODUCTION → mark complete on PRODUCTION.
           except: mark failed on PRODUCTION.
           finally: delete the Neon branch if one was created (D-10 — always
                runs, even on exception).

    No verified_qa promotion happens here. See the module docstring and
    eval_service.VERIFIED_QA_PROMOTION_DECISION: promotion is gated on the label
    trust hierarchy and unreachable for every scenario source the schema allows,
    and the decision is recorded on the run in eval_runs.config.

    A HUMAN-LABELLED ROW CHANGES NOTHING ABOUT THAT (D6 P3). Labelling makes a
    row eligible to the SELECTOR above — it acquires a reference_answer, so it
    is fetched, counted in `valid`, put to the agent and scored — and it changes
    nothing downstream of the score. It does not touch `dataset`, so it joins the
    exploratory half and never the golden one (membership of the golden set is
    asserted, never inherited); and it does not touch `source`, so it stays
    unpromotable, on top of the decision flag now returned as
    `promotion_enabled`.

    Args:
        agent_id: UUID string of the agent to evaluate.

    Returns:
        {"run_id", "scenario_count", "attempted", "valid", "scored", "datasets",
         "dataset_column_available", "golden_set_present", "promoted",
         "config_recorded", "promotion_enabled", "promotion_disabled_reason",
         "branch_isolation", "agent_invoked", "agent_invocation",
         "invocation_recorded"}                                  on success.
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
        neon_project_id = agent.neon_project_id

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
    # `reference_answer != ''` survives in BOTH queries. It is the empty-label
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

    # ------------------------------------------------------------------
    # Step 6 — Neon branch (D-10 LOCKED), then scoring in try/finally.
    #
    # The branch exists so an eval can never mutate tenant data. Nothing in
    # this build connects to it: run_ragas_eval scores rows already in memory
    # against the judge API, and the scenario rows were read from production
    # above. So the branch is isolation held IN RESERVE, and this block says
    # which of the two it is instead of asserting the one that is false.
    #
    # P2 DID NOT CHANGE THAT, and the reason is worth stating because it is the
    # obvious place to be wrong. The agent turns below run against the tenant's
    # PRODUCTION connection string, not the branch, and they must: `retrieve`
    # has to see the corpus the customer is served, and a branch is a copy taken
    # at run start. What stops those turns writing is RECORDED MODE (BACKLOG
    # 2.5) — the retrieval_metrics row, the escalation marker and mail, and the
    # six mutating skills' ProviderAdapter calls are all suppressed and recorded
    # at the tool layer. Two independent mechanisms for two different jobs:
    # the branch would isolate a WRITE, recorded mode prevents one. Pointing the
    # agent at the branch instead would swap a real measurement for a measurement
    # against a snapshot, and would still not stop the ProviderAdapter, which is
    # outside the database entirely.
    #
    #   * It is still created and still deleted in `finally`, so the guarantee
    #     is already in place the day scoring starts issuing statements.
    #   * A branch that cannot be created or readied no longer abandons the
    #     run. Abandoning it threw away a night's measurement — on production,
    #     which is reachable — over a resource nothing reads.
    #   * The readiness probe only runs when something is going to connect.
    #     Waiting for an endpoint nobody opens is cost and failure surface
    #     with no signal in it.
    #
    # eval_service.EVAL_SCORING_REQUIRES_BRANCH is the single switch: flip it
    # in the same edit that gives scoring a database, and both the probe and
    # the fatal failure come back, because then the branch really is what
    # stands between the eval and production tenant data.
    #
    # Acquisition sits INSIDE the try/finally rather than before it. Previously
    # it had its own try/except that returned, so a branch that was created and
    # then failed its readiness probe leaked: the `finally` belonged to the
    # block that was never entered. Every path after create_branch returns an
    # id now reaches the deletion below.
    # ------------------------------------------------------------------
    branch_id_for_finally: str | None = None
    branch_isolation = "provisioned_unused"
    # Set the moment the first SDK turn could have run. A retry after that point
    # re-invokes the whole set — see the `except` below.
    agent_was_invoked = False
    try:
        try:
            branch_id_for_finally, branch_conn_str = create_branch(
                neon_project_id, f"eval-{run_id}"
            )
            if EVAL_SCORING_REQUIRES_BRANCH:
                wait_for_neon_ready(branch_conn_str)
        except Exception as branch_exc:
            log.error(
                "run_eval_suite.branch_create_failed",
                agent_id=agent_id,
                run_id=run_id,
                error=str(branch_exc),
                scoring_requires_branch=EVAL_SCORING_REQUIRES_BRANCH,
            )
            if EVAL_SCORING_REQUIRES_BRANCH:
                raise
            # Scoring needs no branch, so the run continues and says on the way
            # out that it ran without one — a reader must never have to guess
            # whether isolation was in force.
            branch_isolation = "unavailable"

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
            agent_id=agent_id,
            conn_str=conn_str,
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
        # deployment_service._fetch_eval_summary_sync built a non-empty
        # pass_rates from them and returned EVAL_SIGNAL_MEASURED. The 'unknown'
        # lived in a config key that nothing outside this module reads, so
        # everything a consumer actually reads reported a pass over two
        # observations. Before P2 that state was unreachable — every fetched row
        # was always 'scored'.
        #
        # The deploy gate learning to read `agent_invoked` is P3. Until it does,
        # the refusal has to be here, where the observation is: no eval_results
        # rows means _fetch_eval_summary_sync finds an empty pass_rates and
        # returns EVAL_SIGNAL_NO_VALID_SCORES, which apply_signal_evidence_gate
        # already refuses. Fail-closed with the machinery that exists rather than
        # a window in which the plan's "reports unknown, never pass" is true of
        # one key and false of the run.
        #
        # The run still ends terminally and still carries its whole invocation
        # observation, so "this run measured too little" stays readable — it is
        # the SCORES that are withheld, not the record.
        # ------------------------------------------------------------------
        # Annotated, because the two branches below assign different literal
        # types and the join would otherwise be dict[str, object] — which makes
        # `results["scores"]` an `object` that write_eval_results and
        # summarise_run_validity both reject.
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
                    "no eval_results written and no judge call billed — a run "
                    "below the floor is not a measurement, and writing its "
                    "scores would make the deploy gate read it as one"
                ),
            )
            update_eval_run_status(
                run_id, "complete", finished_at=True, conn_str=conn_str
            )
            results = {"scores": [], "means": {}, "sent": 0, "returned": 0,
                       "unattributed": 0}
        else:
            # No connection string is passed: scoring opens nothing. It scores
            # the AGENT'S responses against the contexts the AGENT retrieved.
            results = run_ragas_eval(scored_scenarios)

            # Observations about the run land on PRODUCTION, which is the whole
            # point of the split: the branch below is about to be destroyed.
            write_eval_results(run_id, results["scores"], conn_str)
            update_eval_run_status(
                run_id, "complete", finished_at=True, conn_str=conn_str
            )

        # (attempted, valid, scored) for the run and for each dataset. Computed
        # over the FETCHED set, not the valid one, so the two counts stay
        # distinguishable — a run that fetched 40 rows and could score 12 has
        # measured far less than a run that fetched 12, and a report that shows
        # only one of the two numbers cannot say which happened.
        validity = summarise_run_validity(scenarios, results["scores"])

        log.info(
            "run_eval_suite.complete",
            agent_id=agent_id,
            run_id=run_id,
            scenario_count=len(valid_scenarios),
            attempted=validity["attempted"],
            valid=validity["valid"],
            scored=validity["scored"],
            golden_valid=validity["datasets"][DATASET_GOLDEN]["valid"],
            golden_set_present=composition["golden_set_present"],
            dataset_column_available=dataset_column_available,
            config_recorded=config_recorded,
            promoted=0,
            promotion_enabled=VERIFIED_QA_PROMOTION_DECISION["enabled"],
            branch_isolation=branch_isolation,
            agent_invoked=provenance["agent_invoked"],
            invocation_status=invocation["status"],
            invocation_responded=invocation["responded"],
            invocation_attempted=invocation["attempted"],
            invocation_recorded=invocation_recorded,
        )
        return {
            "run_id": run_id,
            # Legacy alias for `valid`, kept so an existing reader does not
            # silently change meaning. New readers take the triple below.
            "scenario_count": len(valid_scenarios),
            # The triple. `valid` is the denominator; a rate computed against
            # `attempted` understates and one computed without a denominator at
            # all is not a measurement.
            "attempted": validity["attempted"],
            "valid": validity["valid"],
            "scored": validity["scored"],
            # Per dataset, never averaged together — a golden score and an
            # exploratory score answer different questions. Each metric carries
            # {value, measured, observations}; value is null exactly when
            # measured is false, which is 'unknown', not zero.
            "datasets": validity["datasets"],
            # WHICH rows this run covered, and whether the tenant DB could even
            # tell us. False for dataset_column_available means the tenant
            # predates migration 0014 — not that it has no golden rows.
            "dataset_column_available": dataset_column_available,
            "golden_set_present": composition["golden_set_present"],
            # Always 0 — promotion is disabled behind the trust gate, and the
            # key is kept so a caller reading it sees the zero rather than a
            # missing key it might treat as "not measured".
            "promoted": 0,
            "config_recorded": config_recorded,
            # THE FLAG TRAVELS WITH THE PROSE. `promoted: 0` and a reason string
            # are what a run reported before D6, and neither is machine-readable
            # as a policy: 0 is also what an ENABLED run that promoted nothing
            # reports, and a reader cannot tell "promotion is off" from "nothing
            # qualified" without parsing English. Since D6 the two are genuinely
            # different — the system can now produce a label that would qualify —
            # so the boolean is stated beside the count it explains.
            "promotion_enabled": VERIFIED_QA_PROMOTION_DECISION["enabled"],
            "promotion_disabled_reason": VERIFIED_QA_PROMOTION_DECISION["reason"],
            # 'provisioned_unused' — a branch exists and no statement ran
            # against it; 'unavailable' — Neon could not give us one and the
            # run scored anyway. Never absent, so the state is always readable.
            "branch_isolation": branch_isolation,
            # --- audit D1: did this run measure the agent? ------------------
            # The gate-facing conjunction (the agent produced the scored
            # responses AND enough rows answered to be a measurement), and
            # beside it the observation it was derived from, so "invoked but
            # below the floor" and "never invoked" stay different claims.
            "agent_invoked": provenance["agent_invoked"],
            "agent_invocation": invocation,
            # False means the run's config could not be patched — the row still
            # reads agent_invoked=false and the deploy gate will refuse it. A
            # measurement lost, in the fail-closed direction.
            "invocation_recorded": invocation_recorded,
        }

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
        # first turn (an insert failure, a branch failure) are unaffected and
        # still cost nothing.
        if agent_was_invoked:
            log.error(
                "run_eval_suite.not_retrying_after_invocation",
                agent_id=agent_id,
                run_id=run_id,
                detail=(
                    "the agent was already invoked for this run; retrying would "
                    "re-run every SDK turn. The run is recorded failed and the "
                    "next nightly dispatch is the retry."
                ),
            )
            return {}
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    finally:
        # D-10 LOCKED: always delete the Neon branch, even on exception.
        # None means create_branch itself failed, so there is nothing to
        # delete — not a path that may skip deletion for any other reason.
        if branch_id_for_finally is not None:
            try:
                delete_branch(neon_project_id, branch_id_for_finally)
            except Exception as del_exc:
                log.warning(
                    "run_eval_suite.branch_delete_failed",
                    agent_id=agent_id,
                    run_id=run_id,
                    error=str(del_exc),
                )


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
        count = generate_eval_suite_for_agent(
            agent_id,
            conn_str,
            num_scenarios=20,
        )
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
