"""
M8 Deployment Checklist Celery task: run_deployment_checklist.

Lives in the `runtime` queue.

Architecture constraints (CLAUDE.md — non-negotiable):
    - acks_late=True AND idempotency guard on every Celery task (both always required)
    - run_deployment_checklist receives only `agent_id` — NEVER a conn_str in task args (CTL-08)
    - conn_str is fetched from the control DB and decrypted at runtime via fernet_decrypt
    - asyncio.run(asyncio.wait_for(..., timeout=ORCHESTRATOR_TIMEOUT_S)) bridge, never
      loop.run_until_complete

Dual-DB split (PATTERNS.md — non-negotiable):
    - Control DB (checklist_runs, agents): use get_sync_db() SQLAlchemy ORM
    - Tenant DB (eval_runs, red_team_runs, verified_qa, documents, chunks):
      use _fetch_*_sync psycopg2 functions from deployment_service.py
    - Phase 18 BLR-01 extends this: the fifth signal, blast_radius, reads the
      CONTROL DB via get_sync_db() (capability_envelopes/tool_calls_audit/
      tenants all live there) — it is the one collector that breaks the
      tenant-DB-only convention the other four follow, and it needs no
      conn_str at all.

Flow (run_deployment_checklist):
    1. Fetch agent from control DB; decrypt conn_str
    2. Idempotency guard — skip while a 'running' checklist_run for this agent is still beating
    3. Insert checklist_runs row (status='running') in control DB via ORM
    3b. THE CHECKLIST SEQUENCES THE JOBS IT GRADES (#54, decision 19 rule 5).
       Dispatch BOTH the eval chain and a red-team run, then wait for each to
       reach a terminal status before collecting anything, bounded by
       CHECKLIST_WAIT_CEILING_S. Until this existed the task dispatched an eval
       on two signal states, never dispatched a red team, and collected
       immediately, so the first checklist ever executed read eval_signal=no_runs
       seconds after starting the eval it was asking about. A job that does not
       reach terminal inside the ceiling reads as an ABSENT record and blocks;
       the pre-dispatch summary is never read.
       THE WAIT IS A CHAIN OF MESSAGES, NOT A SLEEP. Each pass polls once and
       re-queues itself with a countdown, because sleeping in the task body held
       the only `runtime` execution slot on the documented local topology and the
       two jobs it was waiting for could never start.
    4. Collect all 5 signals plus the BLR-02 envelope hash synchronously (4
       signals via psycopg2 against the tenant DB, the 5th signal —
       blast_radius — and the envelope hash both via get_sync_db() against
       the control DB). Always AFTER the wait, so every summary describes the
       runs this checklist sequenced.
    5. Run the orchestrator's turn on the owned loop, under ORCHESTRATOR_TIMEOUT_S,
       billed to the assessed tenant through a LedgerContext this task builds
    6. Parse result, apply the deterministic evidence gate (P2 — an eval or
       red-team signal that is not 'measured' forces recommendation='block'
       with a stated warning; the gate never softens a verdict), then UPDATE
       checklist_runs row to status='complete'; persist the envelope hash
       alongside status, recommendation, report and warnings; merge the
       deterministic evidence and blast-radius warnings into the persisted
       warnings list, de-duplicated by warning_id
    7. On exception: UPDATE checklist_runs row to status='failed'; retry if retries < max_retries
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.model_client import LedgerContext, ledger_recorder
from app.core.security import fernet_decrypt
from app.domain.verdict import Outcome, Verdict, decide
from app.models.agent import Agent
from app.models.checklist_run import ChecklistRun
from app.services.calibration_service import load_calibration_status
from app.services.deployment_service import (
    BLAST_RADIUS_DEFAULT_SIGNAL,
    CORPUS_STATS_UNAVAILABLE_SIGNAL,
    EVAL_SUMMARY_UNAVAILABLE_SIGNAL,
    NARRATION_UNAVAILABLE_SUMMARY,
    RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL,
    TERMINAL_RUN_STATUSES,
    VERIFIED_QA_STATS_UNAVAILABLE_SIGNAL,
    DeploymentReport,
    _compute_envelope_hash_sync,
    _dispatch_moment,
    _fetch_blast_radius_sync,
    _fetch_corpus_stats_sync,
    _fetch_eval_summary_sync,
    _fetch_red_team_summary_sync,
    _fetch_verified_qa_stats_sync,
    apply_signal_evidence_gate,
    derive_blast_radius_warnings,
    derive_quality_warnings,
    eval_summary_did_not_finish,
    latest_eval_run_id_since,
    latest_eval_run_status_since,
    latest_red_team_run_id_since,
    latest_red_team_run_status_since,
    parse_narration,
    poll_terminal_statuses,
    red_team_summary_did_not_finish,
    render_verdict,
    verdict_warnings,
)
from app.services.eval_service import read_eval_result
from app.services.red_team_service import read_red_team_result
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# BACKLOG 1.33: the ceiling now lives in deployment_service so the task and
# the service's own run_orchestrator bridge cannot drift apart. They had.
from app.services.deployment_service import ORCHESTRATOR_TIMEOUT_S  # noqa: E402


def _dispatch_eval_run(agent_id: str) -> bool:
    """Start an eval suite for this agent. Returns True iff it was dispatched.

    THE DAY-1 PATH HAD NO WAY TO PRODUCE AN EVAL RUN (P2 review). Making
    EVAL_SIGNAL_NO_RUNS hard-block is right — an agent with no measurement has
    no evidence of quality — but nothing in signup → ingest → deploy dispatched
    `run_eval_suite`. It runs from the nightly beat or from the "Run Now" button
    on the eval dashboard, and the onboarding flow routes to neither, so a new
    tenant's readiness check blocked and POST /approve-deployment answered 422
    with the only remedy on a page they had not been shown. CLAUDE.md's stated
    core value ends in "deploy"; a gate that cannot be satisfied by the primary
    journey is not a gate, it is a wall.

    So the checklist starts the measurement it is asking for, and the warning
    apply_signal_evidence_gate emits says so. The recommendation is still
    `block` — this run has no evidence and must not ship on the promise of some
    — but the block now converges instead of being terminal.

    NO LONGER ONLY THE FIRST (P3 review), hence the rename from
    `_dispatch_first_eval_run`: existing tenants report AGENT_NOT_INVOKED rather
    than `no_runs`, so the wall had moved to the larger population.

    AND NO LONGER CONDITIONAL AT ALL (#54). Step 4b chose between those states by
    reading a signal collected BEFORE the dispatch, the ordering the sequencer
    removes. The spend bound is the checklist's own idempotency guard, one eval
    per live checklist chain, tighter than the conditional ever was.

    `generate_eval_suite` runs first because a tenant whose scenario generation
    has never run has nothing to evaluate against; both tasks carry their own
    idempotency guards (>= 10 scenarios, and a 'running' run inside 10 minutes),
    and run_eval_suite records even an empty run terminally, so this fires at
    most once per agent rather than on every readiness check.

    Only agent_id crosses the task boundary — never a connection string
    (CLAUDE.md rule 4). Imported inside the function: the eval task module pulls
    in scenario_service and the Neon client, and this task has no other reason
    to load them.

    Best-effort. A broker failure here must not fail the checklist, which has
    already collected every other signal and still owes the owner a report.
    """
    try:
        from celery import chain  # noqa: PLC0415

        from app.worker.tasks.runtime.eval import (  # noqa: PLC0415
            generate_eval_suite,
            run_eval_suite,
        )

        chain(
            generate_eval_suite.si(agent_id),
            run_eval_suite.si(agent_id),
        ).apply_async(queue="runtime")
        log.info("run_deployment_checklist.eval_dispatched", agent_id=agent_id)
        return True
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.eval_dispatch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        return False


def _dispatch_red_team_run(agent_id: str) -> bool:
    """Start a red-team run for this agent. Returns True iff it was dispatched.

    THE CHECKLIST NEVER STARTED ONE (#54). It dispatched an eval on two of the
    seven eval signal states and left the security half entirely to the weekly
    beat, so an agent the beat had not reached was graded against a
    red_team_runs table the checklist itself could have filled. That is the
    same wall _dispatch_eval_run's docstring describes, on the other signal.

    run_red_team's own idempotency guard (a 'running' row inside 90 minutes)
    absorbs a run already in flight, so this costs at most one live run per
    agent. Only agent_id crosses the task boundary (CTL-08). Imported inside the
    function: red_team.py pulls in seven attacker runners and the transactional
    probe builder, and this task has no other reason to load them.

    Best-effort, on _dispatch_eval_run's terms. A broker failure must not fail a
    checklist that still owes the owner a report; the run then never reaches
    terminal, the wait reports it absent, and the gate blocks.
    """
    try:
        from app.worker.tasks.runtime.red_team import run_red_team  # noqa: PLC0415

        run_red_team.apply_async(kwargs={"agent_id": agent_id}, queue="runtime")
        log.info("run_deployment_checklist.red_team_dispatched", agent_id=agent_id)
        return True
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.red_team_dispatch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        return False


def _continue_wait(agent_id: str, wait_state: object) -> dict | None:
    """A continuation's carried state, or None with its run marked failed.

    An unreadable continuation stops, and stopping must not leave the row
    'running' forever (#125): when the state still names its run, that run is
    marked failed so the guard stops reading it as a live chain. A state too
    broken to name one is logged and dropped, which is the #125 residual.
    """
    try:
        return _require_wait_state(wait_state)
    except Exception as exc:
        salvaged = wait_state.get("run_id") if isinstance(wait_state, dict) else None
        log.error(
            "run_deployment_checklist.continuation_unreadable",
            agent_id=agent_id,
            run_id=salvaged,
            error_type=type(exc).__name__,
            error=str(exc) or repr(exc),
        )
        if salvaged:
            _persist_failed(agent_id, str(salvaged), exc)
        return None


def _wait_continues(pending: list, waited_s: float) -> bool:
    """Still under the ceiling with a half outstanding."""
    return bool(pending) and waited_s < settings.CHECKLIST_WAIT_CEILING_S


def _hand_off(
    agent_id: str, run_id: str, state: dict, pending: list, waited_s: float
) -> dict:
    """Give the wait to the next message, or mark the run failed when it cannot.

    A dispatch that fails still expires honestly; a re-queue that fails kills
    the whole chain, so the failure is persisted rather than left as a
    'running' row nothing will ever finish.
    """
    if not _requeue_wait(agent_id, state):
        _persist_failed(
            agent_id, run_id, RuntimeError("the wait could not be re-queued")
        )
        return {}
    log.info(
        "run_deployment_checklist.still_waiting",
        agent_id=agent_id,
        run_id=run_id,
        pending=pending,
        waited_s=round(waited_s, 1),
    )
    return {
        "status": "waiting",
        "run_id": run_id,
        "pending": pending,
        "waited_s": round(waited_s, 1),
    }


def _dispatched(state: dict) -> dict:
    """Which halves the broker actually accepted when the wait opened."""
    return {
        "eval": state["eval_dispatched"],
        "red_team": state["red_team_dispatched"],
    }


def _pending(state: dict) -> list:
    """The halves this wait can still be waiting FOR.

    A HALF THE BROKER REFUSED IS NOT PENDING (#130). `_open_wait` recorded the
    refusal, so that half is decided the moment the wait opens: no run of this
    checklist's exists to reach terminal, and every poll until the ceiling asks a
    question already answered. It reads as an absent measurement and the gate
    blocks either way; the only thing the ceiling bought was forty-five minutes
    of it.

    The poll still runs and its answer still counts. `run_eval_suite`'s own guard
    absorbs a dispatch made while a run is in flight, and the nightly beat starts
    runs this checklist did not, so a terminal row at or after `since` is this
    checklist's evidence whatever the broker said about the dispatch.
    """
    dispatched = _dispatched(state)
    return sorted(
        name
        for name, status in state["statuses"].items()
        if status is None and dispatched[name]
    )


def _log_wait_outcome(agent_id: str, state: dict, waited_s: float) -> None:
    """Name which half the wait ended without, and which of the two ways.

    A job that ran the ceiling out and a job that never reached a queue are
    different incidents with the same effect on the report, and a reader sent to
    the wrong one looks for a slow eval when the broker is down. The observed
    wait rather than the configured ceiling, for the same reason: a run that
    expired because the poll returned None instantly did not wait at all.
    """
    statuses, dispatched = state["statuses"], _dispatched(state)
    absent = [name for name, status in statuses.items() if status is None]
    never_dispatched = sorted(name for name in absent if not dispatched[name])
    timed_out = sorted(name for name in absent if dispatched[name])
    if never_dispatched:
        log.warning(
            "run_deployment_checklist.wait_closed_undispatched",
            agent_id=agent_id,
            never_dispatched=never_dispatched,
            waited_s=round(waited_s, 1),
            detail="the broker refused the dispatch, so no run of this check exists",
        )
    if timed_out:
        log.warning(
            "run_deployment_checklist.wait_ceiling_expired",
            agent_id=agent_id,
            timed_out=timed_out,
            waited_s=round(waited_s, 1),
            ceiling_s=settings.CHECKLIST_WAIT_CEILING_S,
            detail="each named job reads as an absent measurement and blocks",
        )
    if not absent:
        log.info(
            "run_deployment_checklist.both_runs_terminal",
            agent_id=agent_id,
            waited_s=round(waited_s, 1),
            eval_status=statuses.get("eval"),
            red_team_status=statuses.get("red_team"),
        )


#: Every key one continuation of the checklist carries across the broker. They
#: are all JSON, because a Celery kwarg is JSON, and the two moments travel as
#: ISO strings rather than datetimes for the same reason.
_WAIT_STATE_KEYS: tuple[str, ...] = (
    "run_id",
    "since",
    "started_at",
    "statuses",
    "eval_dispatched",
    "red_team_dispatched",
)


def _require_wait_state(wait_state: object) -> dict:
    """Refuse a continuation whose carried state is not the shape this build wrote.

    A missing key is refused rather than defaulted. Defaulting `since` would
    move the boundary the wait reads runs against, and defaulting `statuses`
    would forget every terminal status already observed and start the wait
    again, so both would resolve into a report about the wrong runs. The
    checklist is a deploy gate, so an unreadable continuation stops.
    """
    if not isinstance(wait_state, dict):
        raise TypeError(
            f"run_deployment_checklist needs wait_state as a dict, got "
            f"{type(wait_state).__name__}"
        )
    missing = sorted(set(_WAIT_STATE_KEYS) - set(wait_state))
    if missing:
        raise KeyError(
            f"run_deployment_checklist was continued without {', '.join(missing)}. "
            "A default in their place would grade runs this checklist did not start."
        )
    statuses = wait_state["statuses"]
    if not isinstance(statuses, dict) or set(statuses) != {"eval", "red_team"}:
        raise ValueError(
            "run_deployment_checklist waits on exactly eval and red_team, and "
            f"was continued with statuses {statuses!r}."
        )
    wrong = sorted(
        f"{name}={status!r}"
        for name, status in statuses.items()
        if status is not None and status not in TERMINAL_RUN_STATUSES
    )
    if wrong:
        # poll_terminal_statuses treats any recorded status as already
        # terminal, so a value this build never wrote would skip the wait
        # entirely and collect against runs that are still going.
        raise ValueError(
            "run_deployment_checklist was continued with " + ", ".join(wrong)
            + ", which this build never wrote as a terminal status."
        )
    for key in ("since", "started_at"):
        try:
            datetime.fromisoformat(wait_state[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"run_deployment_checklist was continued with {key}="
                f"{wait_state[key]!r}, which is not a timestamp this build "
                "wrote."
            ) from exc
    return dict(wait_state)


def _open_wait(run_id: str, agent_id: str, conn_str: str) -> dict:
    """Start both measurements and open the wait that grades them. Step 3b's first pass.

    The two dispatch calls are made before the first poll, so both jobs are in
    flight for the whole wait rather than one after the other.

    NO SECOND IDEMPOTENCY GUARD LIVES HERE, and that is deliberate (#85 family).
    What absorbs a broker redelivery is the beating-'running' checklist_runs
    guard in step 2, which is still holding this run's own row for as long as the
    wait lasts. A guard added here would be a second answer to a question step 2
    has already answered, and the two would drift the way the timeout in BACKLOG
    1.33 did.

    A JOB ALREADY IN FLIGHT AT DISPATCH TIME IS THE ONE RESIDUAL. Its guard
    absorbs the dispatch, so no row exists at or after `since`, the wait expires
    and the record reads absent even though a run will finish shortly after. The
    owner re-runs the check and gets it. That is the fail-closed direction and it
    is the price of the boundary that keeps last night's run out.
    """
    since = _dispatch_moment(conn_str)
    return {
        "run_id": run_id,
        "since": since.isoformat(),
        # The WORKER clock, and only for measuring how long the wait has run.
        # `since` is the tenant DB's clock and answers a different question: which
        # rows this checklist could have caused. Skew between the two would put
        # the boundary in the wrong place, so they are never the same field.
        "started_at": datetime.now(timezone.utc).isoformat(),
        "statuses": {"eval": None, "red_team": None},
        "eval_dispatched": _dispatch_eval_run(agent_id),
        "red_team_dispatched": _dispatch_red_team_run(agent_id),
    }


def _poll_wait(agent_id: str, conn_str: str, state: dict) -> dict:
    """One look at both runs, folded into the state the next continuation carries."""
    since = datetime.fromisoformat(state["since"])
    statuses = poll_terminal_statuses(
        state["statuses"],
        {
            "eval": lambda: latest_eval_run_status_since(agent_id, conn_str, since),
            "red_team": lambda: latest_red_team_run_status_since(
                agent_id, conn_str, since
            ),
        },
    )
    return {**state, "statuses": statuses}


#: The stretch after the last poll, which beats nothing: five collectors, the two
#: record reads, the Orchestrator's turn under ORCHESTRATOR_TIMEOUT_S and the
#: control DB write. Only the turn carries a bound of its own, so this is a budget
#: rather than a measurement, and it is one of the four terms `_stale_after_s`
#: sums. The other three are bounds their own modules already state.
CHECKLIST_DECIDE_GRACE_S = 900


def _eval_run_bound_s() -> float:
    """The wall clock one eval run can spend, from eval.py's own arithmetic.

    Lazy on _dispatch_eval_run's terms: the eval task module pulls in
    scenario_service and the Neon client, and the `runtime` worker has already
    imported every task module by the time a checklist pass runs, so this is a
    dict lookup there rather than a load.
    """
    from app.worker.tasks.runtime.eval import eval_run_bound_s  # noqa: PLC0415

    return eval_run_bound_s()


def _red_team_run_bound_s() -> float:
    """The wall clock one red-team run can spend, from red_team.py's own plan."""
    from app.worker.tasks.runtime.red_team import red_team_run_bound_s  # noqa: PLC0415

    return red_team_run_bound_s()


def _stale_after_s() -> float:
    """How long a 'running' row may go quiet before it stops meaning a live chain.

    ANCHORED ON THE LAST BEAT, NEVER ON created_at (#129). The guard used to read
    a row created more than sixty minutes ago as gone, and a congested chain
    outlives that. Past minute sixty a second trigger found no live row and
    started a second checklist for the same agent, and both persisted.

    THE GAP BETWEEN TWO BEATS IS THE QUEUE WAIT, NOT THE CEILING, and sizing this
    number on the ceiling is what the guard was still getting wrong. A pass beats
    when it RUNS, and a continuation cannot run while the eval chain and the
    red-team run it dispatched hold the `runtime` slots it shares with them: on
    the documented solo worker that is both job bounds end to end, five times the
    ceiling-plus-grace this used to return. The guard reaped a chain that was
    still working, the chain's next write landed on the reaped row, and two
    checklists ran on one agent again by a different route.

    So the threshold is the sum of what a pass can be made to wait for: the eval
    invocation bound, the red-team bound, the wait ceiling and the decide grace,
    each read from the module that owns it. Derived rather than configured, for
    the reason BACKLOG 1.33 records: a second number sized by hand beside the
    first drifts away from it.
    """
    return (
        _eval_run_bound_s()
        + _red_team_run_bound_s()
        + settings.CHECKLIST_WAIT_CEILING_S
        + CHECKLIST_DECIDE_GRACE_S
    )


def _still_beating(run: ChecklistRun, now: datetime) -> bool:
    """Whether this row's last beat is recent enough to mean a live chain.

    A row that has never beaten falls back to when it was created, which is what
    a first pass between its insert and its first poll looks like. Reading that
    NULL as "abandoned" would reap a run in the middle of opening its own wait.

    A beat is a worker clock on both ends: `_beat` stamps
    `datetime.now(timezone.utc)` and so does the caller, so the only skew that can
    matter is between two workers. THE FALLBACK IS NOT. `created_at` carries the
    table's `now()` server default, so a row that has never beaten is compared
    across the control database's clock and this worker's, and the threshold
    absorbs that difference the same way it absorbs a lost pass.
    """
    last_beat = run.heartbeat_at or run.created_at
    return (now - last_beat).total_seconds() < _stale_after_s()


def _reap(db, agent_id: str, run: ChecklistRun) -> bool:
    """Close out a 'running' row no continuation is coming back to.

    The guard is the only thing that ever looks at an abandoned row, so it is the
    only place that can end one. Left alone it would block this agent's checklist
    for as long as the row existed, which is the cost of a guard that no longer
    forgets a run after an hour.

    FENCED, BECAUSE TWO TRIGGERS READ THE SAME STALE ROW. The read, the decision
    and the write were three steps, so both triggers found the row 'running',
    both reaped it and both went on to insert. Returning False here says the row
    was already closed by the trigger that got there first, whose own fresh run
    holds the partial unique index by now: the caller treats this agent as
    already having a live checklist rather than starting a second one.
    """
    log.warning(
        "run_deployment_checklist.abandoned_run_reaped",
        agent_id=agent_id,
        run_id=str(run.id),
        created_at=run.created_at.isoformat(),
        last_beat=run.heartbeat_at.isoformat() if run.heartbeat_at else None,
        stale_after_s=_stale_after_s(),
    )
    return _claimed(db, str(run.id), status="failed")


def _a_run_is_already_live(agent_id: str) -> bool:
    """Step 2's idempotency guard, against the CONTROL DB through the ORM.

    KEYED ON THE ROW'S STATE, NOT ON ITS AGE (#129). A 'running' row is a running
    checklist however old it is, for as long as its chain keeps beating; a row
    whose beat has gone quiet is abandoned and is closed out here so the next
    trigger gets through. Widening the old created_at window instead would have
    moved the same defect further out rather than removed it.

    FIRST PASS ONLY. A continuation would find the row this run inserted itself
    and skip, which would abandon its own wait.
    """
    with get_sync_db() as db:
        existing = db.execute(
            select(ChecklistRun)
            .where(
                ChecklistRun.agent_id == agent_id,
                ChecklistRun.status == "running",
            )
            .order_by(ChecklistRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing is None:
            return False
        if _still_beating(existing, datetime.now(timezone.utc)):
            return True
        return not _reap(db, agent_id, existing)


def _claimed(db, run_id: str, **values) -> bool:
    """Write these columns onto this run, and only while the row says 'running'.

    THE CHAIN STOPS OWNING ITS ROW THE MOMENT THE GUARD REAPS IT. Every write the
    chain makes now reads the status it is writing over in the same statement, so
    a row another trigger closed out takes nothing further from this chain: zero
    rows back means the run was reaped. An unconditional UPDATE by primary key
    stamped a beat onto the reaped row and then flipped it to 'complete', which
    is the two-checklists-on-one-agent outcome the guard exists to prevent,
    reached from the other side.

    The fence is the WHERE clause rather than a status read before the write,
    because between that read and that write is exactly where the guard runs.
    """
    claimed = db.execute(
        update(ChecklistRun)
        .where(ChecklistRun.id == run_id, ChecklistRun.status == "running")
        .values(**values)
        .returning(ChecklistRun.id)
    ).first()
    db.commit()
    return claimed is not None


def _log_reaped(agent_id: str, run_id: str, write: str) -> None:
    """The one thing a chain says when it finds its own row already closed."""
    log.warning(
        "run_deployment_checklist.run_reaped_while_live",
        agent_id=agent_id,
        run_id=run_id,
        write=write,
        detail=(
            "the guard read this chain as abandoned and closed its row out; the "
            "chain stops here rather than writing over the run that replaced it"
        ),
    )


def _beat(agent_id: str, run_id: str) -> bool:
    """Say on the row that this pass ran, so the guard can read the chain as live.

    Once per pass, from the worker clock the guard compares against. Returns
    False only when the row is no longer this chain's to write.

    A beat that FAILS is not that, and returns True. It costs freshness and
    nothing else: the row keeps its previous beat and `_stale_after_s` absorbs a
    lost pass, so a control DB blip never turns into a checklist reaped out from
    under itself. A beat that reaches the row and finds it closed is the opposite
    kind of news, and the chain ends on it.
    """
    try:
        with get_sync_db() as db:
            still_ours = _claimed(
                db, run_id, heartbeat_at=datetime.now(timezone.utc)
            )
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.heartbeat_failed",
            agent_id=agent_id,
            run_id=run_id,
            error=str(exc) or repr(exc),
        )
        return True
    if not still_ours:
        _log_reaped(agent_id, run_id, "heartbeat")
    return still_ours


#: What the first pass hands back when this agent already has a live checklist.
#: Identity is what the task tests, so `_opened_or_skipped` returns this object
#: itself and the task returns a copy of it: the result reaches Celery's backend,
#: and a sentinel a caller can mutate is one that stops being a sentinel.
ALREADY_RUNNING: dict = {"status": "already_running"}


def _insert_run(agent_id: str) -> str | None:
    """Step 3. This run's own row, in the CONTROL DB only (T-08-03-04).

    None means another trigger's row got there first. The guard and this insert
    are two transactions, and between them is the window two triggers reading one
    stale row used to both come through. 0021's partial unique index on
    (agent_id) WHERE status = 'running' closes it, and the loser reads the
    refusal as what it is: this agent already has a live checklist, which is the
    answer the guard would have given a moment later.
    """
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    try:
        with get_sync_db() as db:
            run = ChecklistRun(agent_id=agent_id, status="running")
            db.add(run)
            db.commit()
            db.refresh(run)
            run_id = str(run.id)
    except IntegrityError:
        log.info(
            "run_deployment_checklist.insert_lost_the_race",
            agent_id=agent_id,
            detail=(
                "another trigger inserted this agent's live row between the "
                "guard and this insert; the index refused the second"
            ),
        )
        return None
    log.info("run_deployment_checklist.started", agent_id=agent_id, run_id=run_id)
    return run_id


def _opened_or_skipped(agent_id: str, conn_str: str) -> dict | None:
    """Steps 2, 3 and 3b of a first pass, or the skip the guard decided.

    Hands back the opened wait, ALREADY_RUNNING when this agent holds a live
    checklist, or None when the open fell over having already marked its own row.
    """
    if _a_run_is_already_live(agent_id):
        log.info("run_deployment_checklist.idempotency_skip", agent_id=agent_id)
        return ALREADY_RUNNING
    run_id = _insert_run(agent_id)
    if run_id is None:
        return ALREADY_RUNNING
    return _open_first_wait(agent_id, run_id, conn_str)


def _open_first_wait(agent_id: str, run_id: str, conn_str: str) -> dict | None:
    """Step 3b's opened wait, or None with the run marked failed.

    THE CHECKLIST SEQUENCES THE JOBS IT GRADES (#54, decision 19 rule 5). Both
    are dispatched inside `_open_wait`, unconditionally. Step 4 used to run
    first, which is why the first checklist ever executed reported
    eval_signal=no_runs about the eval it had started seconds earlier.

    THE STRETCH FROM THE INSERT TO HERE SAT OUTSIDE EVERY TRY (#125). The row
    exists from the insert onwards, so anything raising across it left the row
    'running' with no terminal update, and the step-2 guard then refused every
    re-run behind it for the rest of the window. The fence is `_continue_wait`'s:
    the run is marked failed and the error type reaches the log.
    """
    try:
        return _open_wait(run_id, agent_id, conn_str)
    except Exception as exc:
        log.error(
            "run_deployment_checklist.wait_unopened",
            agent_id=agent_id,
            run_id=run_id,
            error_type=type(exc).__name__,
            error=str(exc) or repr(exc),
        )
        _persist_failed(agent_id, run_id, exc)
        return None


def _polled(agent_id: str, conn_str: str, state: dict | None) -> dict | None:
    """One fenced look at both runs, or None when this pass has no wait left.

    A None `state` comes from an open or a continuation that already fell over
    and already marked its row, so one place in the task stops a dead pass rather
    than three.

    A poll that raises is the same failure one message later (#125). The tenant
    DB read sits inside `_latest_run_since`'s own except, but the fold around it
    does not, and an exception here used to leave the row 'running'.

    A look that succeeded is also this pass's heartbeat (#129): the guard reads
    that beat to tell a chain still working from one nothing will ever finish,
    and reaching the tenant DB is the strongest thing a pass can say about
    itself.

    AND THE BEAT IS WHERE THE CHAIN LEARNS IT WAS REAPED. The write is fenced on
    the row still saying 'running', so a beat that lands nowhere means the guard
    already closed this run out and something else is running for this agent.
    None stops the pass there: nothing is re-queued, nothing is collected and
    nothing is completed. The one look already taken is spent, which is a tenant
    DB read rather than a wrong report.
    """
    if state is None:
        return None
    try:
        polled = _poll_wait(agent_id, conn_str, state)
    except Exception as exc:
        log.error(
            "run_deployment_checklist.poll_failed",
            agent_id=agent_id,
            run_id=state["run_id"],
            error_type=type(exc).__name__,
            error=str(exc) or repr(exc),
        )
        _persist_failed(agent_id, state["run_id"], exc)
        return None
    if not _beat(agent_id, state["run_id"]):
        return None
    return polled


def _waited_s(state: dict) -> float:
    """How long this wait has actually run, across every continuation of it."""
    started_at = datetime.fromisoformat(state["started_at"])
    return (datetime.now(timezone.utc) - started_at).total_seconds()


def _requeue_wait(agent_id: str, state: dict) -> bool:
    """Hand the wait to the next message and let this worker slot go.

    THE CHECKLIST MUST NOT SLEEP INSIDE THE TASK (#54 review). It used to, on the
    same `runtime` queue as the two jobs it had just dispatched. On the
    documented local topology, one worker with `-Q pipeline,runtime` and a solo
    pool, that is one execution slot: the checklist held it for the whole
    ceiling, neither job could start, and the wait could never be satisfied. The
    countdown is what the loop's `sleep` used to be, except the broker holds it
    and the worker is free in the meantime.
    """
    try:
        run_deployment_checklist.apply_async(
            kwargs={"agent_id": agent_id, "wait_state": state},
            countdown=settings.CHECKLIST_WAIT_POLL_S,
            queue="runtime",
        )
        return True
    except Exception as exc:
        # The dispatch helpers are best-effort because a lost dispatch still
        # expires honestly. A lost RE-QUEUE kills the whole chain, so the
        # caller marks the run failed rather than leaving a 'running' row
        # nothing will ever finish.
        log.error(
            "run_deployment_checklist.requeue_failed",
            agent_id=agent_id,
            run_id=state["run_id"],
            error_type=type(exc).__name__,
            error=str(exc) or repr(exc),
        )
        return False


def _collected(agent_id: str, event: str, fetch, fallback):
    """One collector, whose own failure is substituted rather than raised.

    A collector that raises must not fail a checklist that has already read the
    others and still owes the owner a report. NO COLLECTOR SUBSTITUTES A
    PLAUSIBLE NUMBER any more. All four fall back to an 'unavailable' signal
    carrying nothing, because the zeros nobody read used to look exactly like
    the zeros of a clean run. Eval and red team came first (audit D3), since the
    evidence gate refuses to ship on their signal; verified_qa and corpus gate
    nothing and outlived them by that much, until #131, and what they reached
    instead was the owner's report and derive_quality_warnings, which read an
    unreachable tenant DB as a thin corpus and an empty knowledge base.

    The fallback is a callable rather than a shared dict so a caller that handed
    the module constant itself cannot let a later mutation poison it.

    `event` is spelled in full at the call site so every log name this task can
    emit is greppable in the source.
    """
    try:
        return fetch()
    except Exception as exc:
        log.warning(event, agent_id=agent_id, error=str(exc))
        return fallback()


# A HALF THE WAIT NEVER SAW FINISH IS NOT COLLECTED AT ALL (#54 review), and
# that is the first thing `_collect_signals` decides. `_latest_run` filters
# `status <> 'running'`, so asking the eval collector about a run that is still
# going hands back LAST NIGHT'S run, graded 'measured', inside a report claiming
# every number describes the run this checklist sequenced. The red-team collector
# had no status filter at all, so a still-running row answered "a run exists" and
# it reported zero open findings for an agent nothing had ever probed. One reads
# a stale run and the other a phantom one, and the substitute for both says the
# run did not finish and carries no number.
#
# `eval_dispatched` rides on the eval signal so the owner-facing warning can say
# the platform started the measurement, rather than naming an Evaluation page the
# onboarding flow never routes to. It softens nothing: the gate still blocks on a
# signal that is not 'measured', because a measurement that was STARTED is not a
# measurement.
#
# `red_team_dispatched` rides on the security signal for the other half of that
# sentence. Both did_not_finish warnings told the owner the check "was still
# running when this readiness check gave up waiting", which describes a job the
# broker refused as one that is working, so the instruction to wait a little
# while pointed at nothing.


def _collect_signals(
    agent_id: str, conn_str: str, state: dict, waited_s: float
) -> dict:
    """The five quality signals, read after both runs settled. Step 4.

    Four collectors read the TENANT DB over psycopg2; blast_radius reads the
    CONTROL DB and takes no conn_str (BLR-01), which is the one collector that
    breaks the tenant-DB-only convention the others follow.
    """
    if state["statuses"]["eval"] is None:
        eval_summary = eval_summary_did_not_finish(
            waited_s, dispatched=state["eval_dispatched"]
        )
    else:
        eval_summary = _collected(
            agent_id,
            "run_deployment_checklist.eval_summary_fetch_failed",
            lambda: _fetch_eval_summary_sync(agent_id, conn_str),
            lambda: dict(EVAL_SUMMARY_UNAVAILABLE_SIGNAL),
        )
    eval_summary["eval_dispatched"] = state["eval_dispatched"]

    if state["statuses"]["red_team"] is None:
        red_team_summary = red_team_summary_did_not_finish(
            waited_s, dispatched=state["red_team_dispatched"]
        )
    else:
        red_team_summary = _collected(
            agent_id,
            "run_deployment_checklist.red_team_summary_fetch_failed",
            lambda: _fetch_red_team_summary_sync(agent_id, conn_str),
            lambda: dict(RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL),
        )
    red_team_summary["red_team_dispatched"] = state["red_team_dispatched"]

    return {
        "eval_summary": eval_summary,
        "red_team_summary": red_team_summary,
        "verified_qa_stats": _collected(
            agent_id,
            "run_deployment_checklist.verified_qa_stats_fetch_failed",
            lambda: _fetch_verified_qa_stats_sync(agent_id, conn_str),
            lambda: dict(VERIFIED_QA_STATS_UNAVAILABLE_SIGNAL),
        ),
        "corpus_stats": _collected(
            agent_id,
            "run_deployment_checklist.corpus_stats_fetch_failed",
            lambda: _fetch_corpus_stats_sync(agent_id, conn_str),
            lambda: dict(CORPUS_STATS_UNAVAILABLE_SIGNAL),
        ),
        "blast_radius": _collected(
            agent_id,
            "run_deployment_checklist.blast_radius_fetch_failed",
            lambda: _fetch_blast_radius_sync(agent_id),
            lambda: dict(BLAST_RADIUS_DEFAULT_SIGNAL),
        ),
    }


def _envelope_hash(agent_id: str) -> str | None:
    """The BLR-02 capability-envelope hash. Control DB, no conn_str.

    Kept out of `signals` because it is not a narrative quality signal for the
    orchestrator; it is persisted directly onto the checklist run in step 6. A
    None here is NOT a neutral outcome: envelope_drift treats an absent recorded
    hash as drift, so a run whose hash collector failed can never be approved.
    That is the deliberate fail-closed direction, matching a NULL pre-0019
    historical hash.
    """
    return _collected(
        agent_id,
        "run_deployment_checklist.envelope_hash_failed",
        lambda: _compute_envelope_hash_sync(agent_id),
        lambda: None,
    )


def _awaited_record(status, read_id, read_record, agent_id: str, conn_str: str, since):
    """The record ONE awaited run left behind, or None. Never a substitute.

    Three ways to reach None and all three read the same to `decide()`: the wait
    never saw this half reach terminal, no row exists at or after the dispatch
    boundary, or the run wrote no readable record. Every one of them is an
    unmeasured run, which blocks under `absent_eval_measurement` or
    `absent_red_team_measurement`. THE TYPED ABSENT INPUT IS THE WHOLE POINT: a
    caller that filled the counts in itself would turn an unread run into a clean
    one, and a caller that reached for the newest row of any vintage would grade
    last night's.
    """
    if status is None:
        return None
    run_id = read_id(agent_id, conn_str, since)
    return None if run_id is None else read_record(run_id, conn_str)


def _awaited_records(agent_id: str, conn_str: str, state: dict) -> tuple:
    """(eval record, red-team record) for the two runs this checklist sequenced."""
    since = datetime.fromisoformat(state["since"])
    return (
        _awaited_record(
            state["statuses"]["eval"],
            latest_eval_run_id_since,
            read_eval_result,
            agent_id,
            conn_str,
            since,
        ),
        _awaited_record(
            state["statuses"]["red_team"],
            latest_red_team_run_id_since,
            read_red_team_result,
            agent_id,
            conn_str,
            since,
        ),
    )


def _compute_verdict(agent_id: str, conn_str: str, state: dict) -> Verdict:
    """THE deployment decision (#54, closes #36). Three records in, one Verdict out.

    THIS IS WHERE THE RECOMMENDATION IS DECIDED, and it is decided before the
    model is asked anything. `decide()` is pure and every threshold it turns on
    lives on a constant in `app.domain.verdict`, so the outcome does not depend
    on a completion, on a prompt revision or on which number a model happened to
    quote. The Orchestrator's turn is handed the finished Verdict and writes
    prose from it.

    `block_on_high` is the one piece of configuration that crosses the seam.
    `app.domain` may not import `app.core.config` (the import-linter layers
    contract), so this caller reads the setting and passes the answer in, and the
    Verdict records which way it was set in the `high_breach` reason's threshold
    sentence.

    The calibration identity comes off the eval record, exactly as
    `_calibration_block` takes it: `judge_identity` is run-level and is already
    None when the four metric routes disagree, and no record has no identity to
    ask about at all. Both reach the loader as None and come back as
    `not_calibrated_yet` with reason `no_single_judge_identity`, which blocks.
    """
    eval_record, red_team_record = _awaited_records(agent_id, conn_str, state)
    calibration = load_calibration_status(
        settings.CALIBRATION_ARTIFACT_PATH,
        eval_record.judge_identity if eval_record is not None else None,
    )
    verdict = decide(
        eval_record,
        red_team_record,
        calibration,
        block_on_high=settings.DEP_BLOCK_ON_HIGH_RED_TEAM,
    )
    log.info(
        "run_deployment_checklist.verdict",
        agent_id=agent_id,
        outcome=Outcome(verdict.outcome).value,
        rules=[reason.rule for reason in verdict.reasons],
        rule_version=verdict.rule_version,
        eval_record=eval_record is not None,
        red_team_record=red_team_record is not None,
    )
    return verdict


def _floor_under_the_verdict(
    agent_id: str, run_id: str, verdict: Verdict, eval_summary: dict, red_team_summary: dict
) -> tuple[str, list]:
    """apply_signal_evidence_gate, run on the computed outcome. One-way, always.

    THE GATE STAYS, AND IT STAYS ONE-WAY (#54). It reads the collector payloads
    where `decide()` reads the runs' own frozen records, so the two look at the
    same checklist through different windows and either can see something the
    other cannot. The gate can make the outcome more conservative and can never
    make it less: `ship` over an absent signal becomes `block`, and a `block`
    is never softened.

    A DISAGREEMENT IS LOGGED LOUDLY AND IS NEVER RESOLVED QUIETLY. If the gate
    downgrades what `decide()` reached, one of the two is wrong about the same
    run: either the rule table missed an absence the collector caught, or the
    collector is reading a state the record does not carry. The conservative
    answer stands so the deploy fails closed, and the log line is the defect
    report. It is `log.error` rather than `log.warning` because nothing about it
    is routine.
    """
    outcome = Outcome(verdict.outcome).value
    gated, warnings = apply_signal_evidence_gate(
        outcome, eval_summary, red_team_summary
    )
    if gated != outcome:
        log.error(
            "run_deployment_checklist.evidence_gate_disagrees",
            agent_id=agent_id,
            run_id=run_id,
            verdict_outcome=outcome,
            gated_recommendation=gated,
            verdict_rules=[reason.rule for reason in verdict.reasons],
            eval_signal=eval_summary.get("eval_signal"),
            red_team_signal=red_team_summary.get("signal"),
            detail=(
                "decide() and the evidence gate read one checklist and reached "
                "different answers. The more conservative one is persisted; the "
                "disagreement is a defect in one of them, not a resolution."
            ),
        )
    return gated, warnings


def _merge_warnings(narrated: list, derived: list) -> list:
    """The model's warnings, plus every derived one it did not already raise.

    De-duplicated by warning_id so a narration that happens to use a derived
    slug cannot produce two rows the owner has to acknowledge twice. That keeps
    POST /checklist-runs/{run_id}/acknowledge, which validates submitted ids
    against run.warnings, working unchanged for every new id.

    APPENDED, NEVER REPLACING. Whatever forced the outcome must be visible to the
    owner as a warning with a stated reason, or 'block' arrives unexplained.
    """
    merged = list(narrated)
    seen = {warning.warning_id for warning in merged}
    for warning in derived:
        if warning.warning_id not in seen:
            merged.append(warning)
            seen.add(warning.warning_id)
    return merged


def _narration(agent_id: str, run_id: str, result_container: dict) -> tuple[str, list]:
    """The model's summary and warnings, or the fixed fallback. Step 5's output.

    A FAILED, TIMED-OUT OR MALFORMED NARRATION NO LONGER FAILS THE CHECKLIST
    (#54). The verdict was computed before this turn ran and does not depend on
    it, so persisting 'failed' here would throw away a decision the platform had
    already reached and would leave the owner with nothing. What is missing is
    the prose, and the fallback summary says so in as many words.

    A REPORT THAT ARRIVED IS NOT A REPORT THIS BUILD CAN READ. The tool loop
    validates nothing, so `parse_narration` is what stands between the model's
    raw arguments and `DeploymentReport`. Without it a warnings item missing
    `category` raised a ValidationError one step later, inside the persist block,
    and reached the failed path anyway: the same discarded verdict by a longer
    route.
    """
    report_data = result_container.get("report")
    if report_data:
        narration = parse_narration(report_data)
        if narration.dropped_warnings or narration.summary_replaced:
            # Loud, because the owner-facing outcome no longer shows it. A model
            # emitting a report this build cannot read is a defect in the prompt
            # or in the routing, and this line is where it surfaces.
            log.error(
                "run_deployment_checklist.narration_malformed",
                agent_id=agent_id,
                run_id=run_id,
                dropped_warnings=narration.dropped_warnings,
                summary_replaced=narration.summary_replaced,
                detail=(
                    "the narration turn submitted a report this build cannot "
                    "read; the verdict stands and the unreadable parts are dropped"
                ),
            )
        return narration.summary, narration.warnings
    log.error(
        "run_deployment_checklist.narration_unavailable",
        agent_id=agent_id,
        run_id=run_id,
        detail="the verdict stands; the owner-facing write-up is the fallback",
    )
    return NARRATION_UNAVAILABLE_SUMMARY, []


def _persist_failed(agent_id: str, run_id: str, exc: Exception) -> None:
    """Mark the run failed, and never let that write hide the original failure.

    Fenced like every other write this chain makes: a row the guard already
    reaped is not this chain's to close, and the reap wrote 'failed' onto it
    anyway.
    """
    log.error(
        "run_deployment_checklist.failed",
        agent_id=agent_id,
        run_id=run_id,
        error_type=type(exc).__name__,
        error=str(exc) or repr(exc),
    )
    try:
        with get_sync_db() as db:
            still_ours = _claimed(db, run_id, status="failed")
    except Exception as update_exc:
        log.warning(
            "run_deployment_checklist.update_failed_status_error",
            agent_id=agent_id,
            run_id=run_id,
            error=str(update_exc),
        )
        return
    if not still_ours:
        _log_reaped(agent_id, run_id, "failed")


def _persist_complete(
    run_id: str, report, signals: dict, verdict: Verdict, envelope_hash, warnings: list
) -> bool:
    """One transaction: status, recommendation, report, warnings and the hash.

    Returns False when the row was reaped while this chain was working, in which
    case nothing is written: a run the guard closed out has already been replaced
    by the checklist that started in its place, and completing it would put two
    reports on one agent.

    BLR-02: the hash lands with the rest. Acknowledgement (the sibling timestamp
    column) is never stamped here, because that is the owner's act at approve
    time rather than the platform's at checklist time.

    The Verdict's own payload rides on the report under 'verdict', so a console
    or a later reader can rebuild the decision with `Verdict.from_payload` and
    see every rule that produced it rather than the one word it came to.
    """
    with get_sync_db() as db:
        return _claimed(
            db,
            run_id,
            status="complete",
            recommendation=report.recommendation,
            report={
                **signals,
                "verdict": verdict.payload,
                "summary": report.summary,
                "recommendation": report.recommendation,
            },
            warnings=[warning.model_dump() for warning in warnings],
            envelope_hash=envelope_hash,
        )


def _failed_and_handed_back(task, agent_id: str, run_id: str, exc: Exception) -> dict:
    """Close the run out and hand the message back to the broker if it can be.

    The two places the task's own failures land say exactly this, so they say it
    once. `task.retry()` returns the exception to raise rather than raising it,
    and raising it from here propagates out of the task body unchanged.
    """
    _persist_failed(agent_id, run_id, exc)
    if task.request.retries < task.max_retries:
        raise task.retry(exc=exc, countdown=2 ** task.request.retries)
    return {}


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.deployment.run_deployment_checklist",
)
def run_deployment_checklist(self, agent_id: str, wait_state: dict | None = None) -> dict:
    """Per-agent deployment checklist run.

    Collects quality signals from the tenant DB, runs the orchestrator's turn on
    the owned loop, and records the recommendation in control DB checklist_runs.

    Receives agent_id str — no conn_str in args (CTL-08 / CLAUDE.md non-negotiable).

    THE WAIT SPANS SEVERAL MESSAGES, NOT ONE LONG TASK BODY (#54 review). The
    first pass dispatches both jobs and opens a wait; every pass after it takes
    ONE look at the tenant DB and either re-queues itself with a countdown or
    goes on to collect. `wait_state` is how a continuation knows which runs it is
    waiting on, so a worker slot is never held while nothing is happening.

    Sequence:
        1. Fetch agent from control DB; decrypt conn_str at runtime. Every pass.
        2. Idempotency guard — skip while a 'running' checklist_run for this
           agent is still beating. FIRST PASS ONLY: on a continuation the row it
           would find is this run's own.
        3. Insert checklist_runs row (status='running') in control DB via ORM.
           First pass only, for the same reason.
        3b. First pass: dispatch the eval chain and the red-team run and open the
           wait. Every pass: poll once, and re-queue with CHECKLIST_WAIT_POLL_S
           of countdown while either job is still in flight and
           CHECKLIST_WAIT_CEILING_S has not expired.
        4. Collect all 5 signals synchronously (psycopg2 against tenant DB) plus
           the BLR-02 envelope hash (control DB, own guarded block). A half that
           never reached terminal is NOT collected: its `did_not_finish` payload
           is substituted, because the collector would answer about some other
           run.
        4b. Read both runs' frozen records and the Judge's calibration status,
           and call decide(). THE RECOMMENDATION IS DECIDED HERE, before any
           model is asked anything.
        5. Ask the Orchestrator to narrate the verdict, under
           ORCHESTRATOR_TIMEOUT_S. A failure or timeout costs the prose and
           nothing else.
        6. UPDATE checklist_runs to status='complete', persisting the verdict's
           outcome as the recommendation, the verdict payload on the report, and
           every reason as a warning beside the envelope hash.
        7. On the task's OWN exception: UPDATE to status='failed'; retry if
           possible. Never for a narration that did not arrive.

    Args:
        agent_id: UUID string of the agent to check.
        wait_state: what the previous pass observed, or None on the first pass.
            Never built by a caller outside this module.

    Returns:
        {"status": "complete", "run_id": str, "recommendation": str}  on success.
        {"status": "waiting", "run_id": str, "pending": [str], ...}   while a job runs.
        {"status": "already_running"}                                  on idempotent skip.
        {"status": "reaped", "run_id": str}   when the guard closed this run out
            while it was working and a later checklist holds the agent. Every
            write the chain makes is fenced on the row still saying 'running', so
            this pass writes nothing rather than reopening a closed run.
        {}                                                             on retry exhaustion.
    """
    # ------------------------------------------------------------------
    # Step 1 — Fetch agent from control DB; decrypt conn_str at runtime
    # conn_str is intentionally not logged — CTL-08 constraint.
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_connection_string:
            log.error(
                "run_deployment_checklist.agent_not_found",
                agent_id=agent_id,
            )
            return {}

        conn_str = fernet_decrypt(agent.neon_connection_string)
        tenant_id = str(agent.tenant_id)

    # Steps 2, 3 and 3b, on the first pass only: guard, insert, dispatch both
    # jobs and open the wait. A continuation's state comes off the message
    # instead, and either path can hand back None having already marked its own
    # row failed (#125).
    if wait_state is None:
        state = _opened_or_skipped(agent_id, conn_str)
        if state is ALREADY_RUNNING:
            return dict(ALREADY_RUNNING)
    else:
        state = _continue_wait(agent_id, wait_state)

    # One look, then either hand the wait to the next message or go on. A job
    # that never reaches terminal inside the ceiling reads as an ABSENT record,
    # so the gate blocks on it; the pre-dispatch summary is never read.
    state = _polled(agent_id, conn_str, state)
    if state is None:
        return {}
    run_id = state["run_id"]
    waited_s = _waited_s(state)
    pending = _pending(state)
    if _wait_continues(pending, waited_s):
        return _hand_off(agent_id, run_id, state, pending, waited_s)

    _log_wait_outcome(agent_id, state, waited_s)

    # ------------------------------------------------------------------
    # Step 4 — Collect the five signals and the BLR-02 envelope hash. Always
    # AFTER the wait, so every summary describes the runs this checklist
    # sequenced rather than whatever the tables held when it started.
    # ------------------------------------------------------------------
    signals = _collect_signals(agent_id, conn_str, state, waited_s)
    eval_summary = signals["eval_summary"]
    red_team_summary = signals["red_team_summary"]
    envelope_hash = _envelope_hash(agent_id)

    # ------------------------------------------------------------------
    # Step 4b — THE DECISION (#54 criterion 3, closes #36). Computed here, from
    # the two runs' own frozen records and the Judge's calibration status, before
    # anything is asked of a model. Everything after this narrates it.
    # ------------------------------------------------------------------
    # Inside the step 7 try because a record read that raises, or a decide()
    # refusal, is a failure BEFORE the verdict exists. That is the one thing
    # status='failed' is still for.
    try:
        verdict = _compute_verdict(agent_id, conn_str, state)
    except Exception as exc:
        return _failed_and_handed_back(self, agent_id, run_id, exc)

    # ------------------------------------------------------------------
    # Step 5 - the narration turn, under ORCHESTRATOR_TIMEOUT_S. The shim awaits
    # the service's loop; run_orchestrator would nest a second asyncio.run.
    #
    # The rendered verdict travels WITH the signals, so the model reads the
    # decision it is describing rather than deriving one. It is rendered rather
    # than passed whole: the turn gets the outcome and the reason sentences,
    # which already carry each threshold in words, and no rule slugs.
    # ------------------------------------------------------------------
    signals_json = json.dumps({**signals, "verdict": render_verdict(verdict)})
    result_container: dict = {}
    ledger = _orchestrator_ledger(tenant_id, agent_id, run_id, conn_str)

    try:
        asyncio.run(
            asyncio.wait_for(
                _call_orchestrator_async(signals_json, result_container, ledger=ledger),
                timeout=ORCHESTRATOR_TIMEOUT_S,
            )
        )
    except Exception as exc:
        # BACKLOG 1.30. `error=str(exc)` alone logged an EMPTY STRING for the
        # failure that actually happens: `str(asyncio.TimeoutError())` is `""`,
        # and the timeout is this turn's most likely failure by far. The first
        # real checklist run ever executed (E2E-4, 2026-08-13) reported
        # `orchestrator_failed error=` followed by "Orchestrator did not produce
        # a report" — two lines that name no cause between them.
        log.error(
            "run_deployment_checklist.orchestrator_failed",
            agent_id=agent_id,
            run_id=run_id,
            error_type=type(exc).__name__,
            error=str(exc) or repr(exc),
            timeout_s=ORCHESTRATOR_TIMEOUT_S,
            detail="the verdict was computed before this turn and still stands",
        )
        # NOT a fall-through to step 7 any more. The checklist completes on the
        # verdict it already holds, with the fallback summary in place of prose.

    # ------------------------------------------------------------------
    # Step 6 — persist. The recommendation is the verdict's outcome, floored by
    # the evidence gate, and every reason arrives with it as a warning.
    # ------------------------------------------------------------------
    try:
        gated_recommendation, evidence_warnings = _floor_under_the_verdict(
            agent_id, run_id, verdict, eval_summary, red_team_summary
        )
        summary, narrated_warnings = _narration(agent_id, run_id, result_container)

        # Pydantic validates the recommendation against its literal. The model
        # has no say in it: submit_report has no such field, and this value came
        # from decide() by way of the gate.
        report = DeploymentReport(
            recommendation=gated_recommendation,  # type: ignore[arg-type]  # DeploymentReport validates the literal at construction
            summary=summary,
            warnings=narrated_warnings,
            eval_summary=eval_summary,
            red_team_summary=red_team_summary,
            verified_qa_stats=signals["verified_qa_stats"],
            corpus_stats=signals["corpus_stats"],
            blast_radius=signals["blast_radius"],
        )
        # Four deterministic sources, merged into the model's own by warning_id:
        # every verdict reason, whatever the evidence gate refused on, the
        # blast-radius comparison, and the two quality conditions decide() cannot
        # see. All four are Python, never the model's arithmetic.
        merged_warnings = _merge_warnings(
            report.warnings,
            verdict_warnings(verdict)
            + evidence_warnings
            + derive_blast_radius_warnings(signals["blast_radius"])
            + derive_quality_warnings(signals["verified_qa_stats"], red_team_summary),
        )
        if not _persist_complete(
            run_id, report, signals, verdict, envelope_hash, merged_warnings
        ):
            return {"status": "reaped", "run_id": run_id}

        log.info(
            "run_deployment_checklist.complete",
            agent_id=agent_id,
            run_id=run_id,
            recommendation=report.recommendation,
            narrated=bool(result_container.get("report")),
        )
        return {
            "status": "complete",
            "run_id": run_id,
            "recommendation": report.recommendation,
        }

    except Exception as exc:
        # ------------------------------------------------------------------
        # Step 7 — UPDATE status=failed on exception; retry if retries remain.
        #
        # RESERVED FOR THE TASK'S OWN FAILURES (#54). A narration that timed out,
        # fell over, or came back unreadable does not land here: the verdict
        # exists without the model, and parse_narration drops what it cannot
        # read. What reaches this block is a record read that raised, a decide()
        # refusal, or a control-DB write that failed, and none reached a decision.
        # ------------------------------------------------------------------
        return _failed_and_handed_back(self, agent_id, run_id, exc)


def _orchestrator_ledger(
    tenant_id: str, agent_id: str, run_id: str, conn_str: str
) -> LedgerContext:
    """The orchestrator's turn is billed to the tenant whose agent it assesses.

    The row lands in that tenant's own ledger and the checklist run is the job,
    mirroring red_team.py's _run_ledger. conn_str reaches the recorder and
    nothing else: no carrier and no ledger row has a field for it (rule 1).
    """
    return LedgerContext(
        tenant_id=tenant_id,
        agent_id=agent_id,
        job_id=run_id,
        recorder=ledger_recorder(conn_str),
    )


async def _call_orchestrator_async(
    signals_json: str, result_container: dict, *, ledger: LedgerContext
) -> None:
    """Thin async shim that awaits the service's orchestrator loop.

    The service's run_orchestrator bridge is synchronous and calls asyncio.run
    itself. wait_for needs an awaitable, so this shim awaits the loop directly
    and avoids a nested asyncio.run(), which raises RuntimeError in Python 3.12.
    """
    from app.services.deployment_service import _run_orchestrator_loop
    await _run_orchestrator_loop(signals_json, result_container, ledger=ledger)
