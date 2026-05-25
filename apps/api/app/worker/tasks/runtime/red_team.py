"""
M7 Red Team Celery tasks: run_red_team_beat and run_red_team.

Both tasks live in the `runtime` queue.

Architecture constraints (CLAUDE.md — non-negotiable):
    - acks_late=True AND idempotency guard on every Celery task (both always required)
    - run_red_team receives only `agent_id` — NEVER a conn_str in task args (CTL-08)
    - conn_str is fetched from the control DB and decrypted at runtime via fernet_decrypt
    - asyncio.run(asyncio.wait_for(...)) bridge — never loop.run_until_complete (broken Python 3.12)
    - worker_pool=solo means NO Celery chord — all three agent runners execute sequentially
      inside run_red_team

Flow (run_red_team):
    1. Fetch agent from control DB; decrypt conn_str
    2. Idempotency guard — skip if a running red_team_run for this agent exists within 30 min
    3. Insert red_team_run row (status='running')
    4. Build probe_fn closure (calls the deployed agent via direct Claude API)
    5. Run PromptInjection → DataLeakage → Hallucination agents sequentially
    6. Compute max_severity and deployment_blocked
    7. Update red_team_run row to 'complete' with findings JSONB
    8. Return result dict
"""

from __future__ import annotations

import asyncio
import json
import uuid

import anthropic
import psycopg2
import structlog
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.red_team_service import (
    run_prompt_injection_agent,
    run_data_leakage_agent,
    run_hallucination_agent,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Probe function builder — wraps a direct Anthropic API call so it has the
# signature expected by the red_team_service runner functions: (str) -> str.
#
# Note: _run_sdk_turn from agent.py is intentionally NOT used here. That
# function is tightly coupled to the SSE infrastructure (job_id, db, redis,
# emit) designed for customer-facing conversations. The probe_fn only needs
# to send one message to the deployed agent and return the response text —
# a direct Anthropic API call is the correct and minimal implementation.
# ---------------------------------------------------------------------------

_ANTHROPIC_CLIENT = anthropic.Anthropic()


def _build_probe_fn(agent: "Agent", conn_str: str):
    """Return a probe_fn closure for the given agent.

    The closure captures the agent's system prompt fields so the red-team agents
    probe the same persona that is served to real customers. conn_str is captured
    for future extension (retrieval context injection) but is not logged.

    Args:
        agent: Agent ORM instance (soul fields, name).
        conn_str: Decrypted Neon connection string — NEVER logged (CTL-08).

    Returns:
        Callable[[str], str] — sends message to the deployed agent persona and
        returns the text response.
    """
    # Build a minimal system prompt from agent soul fields (matches what
    # build_system_prompt produces for real customer conversations).
    system_lines = [f"You are {agent.name}, a customer service agent."]
    if getattr(agent, "soul_voice", None):
        system_lines.append(f"Voice: {agent.soul_voice}")
    if getattr(agent, "soul_role", None):
        system_lines.append(f"Role: {agent.soul_role}")
    if getattr(agent, "soul_do_list", None):
        do_items = agent.soul_do_list if isinstance(agent.soul_do_list, list) else []
        if do_items:
            system_lines.append("Do: " + "; ".join(str(i) for i in do_items))
    if getattr(agent, "soul_donot_list", None):
        donot_items = agent.soul_donot_list if isinstance(agent.soul_donot_list, list) else []
        if donot_items:
            system_lines.append("Do not: " + "; ".join(str(i) for i in donot_items))
    system_prompt = "\n".join(system_lines)

    async def _async_probe(message: str) -> str:
        """Send one probe message to the agent persona and return the response text."""
        try:
            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: _ANTHROPIC_CLIENT.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=512,
                    system=system_prompt,
                    messages=[{"role": "user", "content": message}],
                ),
            )
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""
        except Exception as exc:
            log.warning("probe_fn.failed", error=str(exc))
            return ""

    def probe_fn(message: str) -> str:
        """Synchronous probe: bridge async _async_probe into sync context.

        Uses asyncio.run(asyncio.wait_for(..., timeout=60.0)) per CLAUDE.md rule —
        never loop.run_until_complete (broken in Python 3.12).
        """
        try:
            return asyncio.run(asyncio.wait_for(_async_probe(message), timeout=60.0))
        except Exception as exc:
            log.warning("probe_fn.timeout_or_error", error=str(exc))
            return ""

    return probe_fn


# ---------------------------------------------------------------------------
# run_red_team_beat — weekly beat dispatcher
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=1,
    default_retry_delay=60,
    queue="runtime",
    name="app.worker.tasks.runtime.red_team.run_red_team_beat",
)
def run_red_team_beat(self) -> dict:
    """Beat-triggered dispatcher: find all deployed agents and dispatch run_red_team per agent.

    Queries the control DB for agents with is_deployed=True and fans out one
    run_red_team task per agent. No conn_str is passed — the per-agent task
    fetches and decrypts at runtime (CTL-08).

    No idempotency guard needed here — the weekly beat fires once at 03:00 UTC Monday;
    duplicate dispatches are harmless because run_red_team itself is idempotent.

    Returns:
        {"dispatched": int} — number of per-agent tasks dispatched.
    """
    with get_sync_db() as db:
        agents = db.execute(
            select(Agent).where(Agent.is_deployed == True)  # noqa: E712
        ).scalars().all()

    dispatched = 0
    for agent in agents:
        run_red_team.apply_async(
            kwargs={"agent_id": str(agent.id)},
            queue="runtime",
        )
        dispatched += 1

    log.info("run_red_team_beat.dispatched", count=dispatched)
    return {"dispatched": dispatched}


# ---------------------------------------------------------------------------
# run_red_team — per-agent red team execution
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.red_team.run_red_team",
)
def run_red_team(self, agent_id: str) -> dict:
    """Per-agent red team run. Executes all three adversarial agents sequentially,
    classifies findings by severity, writes results to red_team_runs, and sets
    deployment_blocked if any critical finding is present.

    Receives agent_id str — no conn_str in args (CTL-08 / CLAUDE.md non-negotiable).

    Sequence:
        1. Fetch agent from control DB; decrypt conn_str at runtime.
        2. Idempotency guard — skip if a 'running' red_team_run for this agent
           was created within the last 30 minutes.
        3. Insert red_team_run row (status='running').
        4. Build probe_fn closure.
        5. Run PromptInjection → DataLeakage → Hallucination agents sequentially.
        6. Compute max_severity and deployment_blocked flag.
        7. Update red_team_run row to 'complete' with findings JSONB.
        8. Return result dict.

    Args:
        agent_id: UUID string of the agent to red-team.

    Returns:
        {"run_id": str, "blocked": bool, "max_severity": str,
         "critical_count": int, "high_count": int}  on success.
        {"status": "already_running"}                on idempotent skip.
        {}                                            on retry exhaustion.
    """
    # ------------------------------------------------------------------
    # Step 1 — Fetch agent from control DB; decrypt conn_str at runtime
    # conn_str is intentionally not logged — CTL-08 constraint.
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_connection_string:
            log.error(
                "run_red_team.agent_not_found_or_unconfigured",
                agent_id=agent_id,
            )
            return {}

        conn_str = fernet_decrypt(agent.neon_connection_string)

    # ------------------------------------------------------------------
    # Step 2 — Idempotency guard: check red_team_runs for a recent running row
    # Uses kind = f"m7:{agent_id}" as the per-agent idempotency key.
    # Guard is best-effort — if the check fails, proceed.
    # ------------------------------------------------------------------
    try:
        _check_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with _check_conn.cursor() as _cur:
                _cur.execute(
                    """
                    SELECT id FROM red_team_runs
                    WHERE kind = %s
                      AND status = 'running'
                      AND started_at > NOW() - INTERVAL '30 minutes'
                    LIMIT 1
                    """,
                    (f"m7:{agent_id}",),
                )
                _existing = _cur.fetchone()
        finally:
            _check_conn.close()

        if _existing:
            log.info("run_red_team.idempotent_skip", agent_id=agent_id)
            return {"status": "already_running"}
    except Exception as exc:
        # Idempotency guard is best-effort — proceed on any check failure
        log.warning(
            "run_red_team.idempotency_check_failed",
            agent_id=agent_id,
            error=str(exc),
        )

    # ------------------------------------------------------------------
    # Step 3 — Insert red_team_run row (status='running')
    # ------------------------------------------------------------------
    run_id = str(uuid.uuid4())
    _run_conn = None
    try:
        _run_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with _run_conn.cursor() as _cur:
                _cur.execute(
                    """
                    INSERT INTO red_team_runs (id, kind, started_at, status)
                    VALUES (%s, %s, NOW(), 'running')
                    """,
                    (run_id, f"m7:{agent_id}"),
                )
            _run_conn.commit()
        except Exception as exc:
            log.error(
                "run_red_team.insert_run_failed",
                agent_id=agent_id,
                error=str(exc),
            )
            if self.request.retries < self.max_retries:
                raise self.retry(exc=exc, countdown=2 ** self.request.retries)
            return {}
    finally:
        if _run_conn is not None:
            _run_conn.close()

    # ------------------------------------------------------------------
    # Step 4 — Build probe_fn closure
    # probe_fn sends one message to the deployed agent persona and returns
    # the response text. Wraps a direct Anthropic API call (not _run_sdk_turn
    # from agent.py — that function is coupled to SSE infrastructure).
    # ------------------------------------------------------------------
    probe_fn = _build_probe_fn(agent, conn_str)

    # ------------------------------------------------------------------
    # Step 5 — Run three agents sequentially (no chord — worker_pool=solo)
    # Wrapped in a single try so a partial failure can update status='failed'.
    # ------------------------------------------------------------------
    _agents_conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        injection_findings = run_prompt_injection_agent(
            probe_fn,
            max_turns=settings.RED_TEAM_MAX_TURNS,
            attack_sequences=settings.RED_TEAM_ATTACK_SEQUENCES,
        )
        leakage_findings = run_data_leakage_agent(
            probe_fn,
            max_turns=settings.RED_TEAM_MAX_TURNS,
            attack_sequences=settings.RED_TEAM_ATTACK_SEQUENCES,
        )
        hallucination_findings = run_hallucination_agent(
            probe_fn,
            max_turns=settings.RED_TEAM_MAX_TURNS,
            attack_sequences=settings.RED_TEAM_ATTACK_SEQUENCES,
        )
        all_findings = injection_findings + leakage_findings + hallucination_findings

        # ------------------------------------------------------------------
        # Step 6 — Compute max_severity and deployment_blocked
        # deployment_blocked is True iff max_severity == "critical" (RED-06 gate).
        # ------------------------------------------------------------------
        SEVERITY_ORDER = ["low", "medium", "high", "critical"]
        severities = [f.severity for f in all_findings if f.severity in SEVERITY_ORDER]
        max_severity = (
            max(severities, key=lambda s: SEVERITY_ORDER.index(s))
            if severities
            else "none"
        )
        deployment_blocked = (max_severity == "critical")
        critical_count = sum(1 for f in all_findings if f.severity == "critical")
        high_count = sum(1 for f in all_findings if f.severity == "high")

        # ------------------------------------------------------------------
        # Step 7 — Update red_team_run row to 'complete'
        # ------------------------------------------------------------------
        try:
            with _agents_conn.cursor() as _cur:
                _cur.execute(
                    """
                    UPDATE red_team_runs
                    SET status = 'complete',
                        finished_at = NOW(),
                        findings = %s,
                        max_severity = %s,
                        deployment_blocked = %s
                    WHERE id = %s
                    """,
                    (
                        json.dumps([f.model_dump() for f in all_findings]),
                        max_severity,
                        deployment_blocked,
                        run_id,
                    ),
                )
            _agents_conn.commit()
        except Exception as update_exc:
            log.warning(
                "run_red_team.update_complete_failed",
                agent_id=agent_id,
                run_id=run_id,
                error=str(update_exc),
            )

        # ------------------------------------------------------------------
        # Step 8 — Return result
        # ------------------------------------------------------------------
        log.info(
            "run_red_team.complete",
            agent_id=agent_id,
            run_id=run_id,
            max_severity=max_severity,
            blocked=deployment_blocked,
            critical_count=critical_count,
            high_count=high_count,
        )
        return {
            "run_id": run_id,
            "blocked": deployment_blocked,
            "max_severity": max_severity,
            "critical_count": critical_count,
            "high_count": high_count,
        }

    except Exception as exc:
        log.error(
            "run_red_team.agents_failed",
            agent_id=agent_id,
            run_id=run_id,
            error=str(exc),
        )
        # Mark run as failed before retry
        try:
            with _agents_conn.cursor() as _cur:
                _cur.execute(
                    """
                    UPDATE red_team_runs
                    SET status = 'failed',
                        finished_at = NOW()
                    WHERE id = %s
                    """,
                    (run_id,),
                )
            _agents_conn.commit()
        except Exception as fail_upd_exc:
            log.warning(
                "run_red_team.update_failed_status_error",
                agent_id=agent_id,
                run_id=run_id,
                error=str(fail_upd_exc),
            )

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)
        return {}

    finally:
        _agents_conn.close()
