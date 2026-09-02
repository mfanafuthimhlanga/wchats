"""Unit tests for app.worker.tasks.runtime.red_team — M7 + RTX (Phase 18) Celery tasks.

Tests:
    test_run_red_team_idempotent_skip
        — run_red_team returns {"status": "already_running"} when idempotency guard fires
    test_run_red_team_beat_dispatches
        — run_red_team_beat dispatches one task per selected agent (2 agents → 2 dispatches)
    test_run_red_team_complete
        — happy path: returns run_id, blocked=False, max_severity="high" for one high finding

Mock strategy:
    - app.worker.tasks.runtime.red_team.get_sync_db patched as context manager
    - app.worker.tasks.runtime.red_team.fernet_decrypt patched to return plain conn_str
    - app.worker.tasks.runtime.red_team.psycopg2.connect patched for cursor control
    - app.worker.tasks.runtime.red_team.run_conversation_injection_agent /
      run_content_injection_agent (Phase 18 SEC-03 / OD-7 split) etc. patched at boundary
    - app.worker.tasks.runtime.red_team.run_confused_deputy_agent /
      run_value_bound_evasion_agent / run_identity_bypass_agent (Phase 18 RTX runners)
      and build_tool_server / _build_transactional_probe_fn patched at boundary too —
      the cross-wave wiring proof for these lives in test_red_team_rtx_runners.py
    - Tasks called via .run(...) to bypass Celery broker
"""

import base64
import os

# Safety: ensure required env vars are present even if conftest is not loaded
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helper: build a mock get_sync_db context manager
# ---------------------------------------------------------------------------

def _make_sync_db_ctx(mock_db):
    """Return a patched get_sync_db that yields mock_db when used as 'with get_sync_db() as db'."""
    @contextmanager
    def _fake_get_sync_db():
        yield mock_db

    return _fake_get_sync_db


# ---------------------------------------------------------------------------
# Helper: build a mock psycopg2 connection with controllable cursor
# ---------------------------------------------------------------------------

def _make_psycopg2_conn(fetchone_value=None):
    """Return a mock psycopg2 connection whose cursor.fetchone() returns fetchone_value."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = fetchone_value
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn


# ---------------------------------------------------------------------------
# test_run_red_team_idempotent_skip
# ---------------------------------------------------------------------------


class TestRunRedTeamIdempotentSkip:
    """Verify the idempotency guard fires correctly."""

    def test_run_red_team_idempotent_skip(self):
        """run_red_team returns {"status": "already_running"} when an existing running row is found.

        Simulates the idempotency guard path in run_red_team step 2.
        """
        from app.worker.tasks.runtime.red_team import run_red_team

        agent_id = str(uuid.uuid4())

        # Mock control DB: return a mock Agent with neon fields set
        mock_agent = MagicMock()
        mock_agent.neon_connection_string = b"encrypted_conn"
        mock_agent.neon_project_id = "proj_123"
        mock_agent.name = "Test Agent"

        mock_db = MagicMock()
        mock_db.get.return_value = mock_agent

        # psycopg2.connect: idempotency check cursor returns an existing row
        mock_check_conn = _make_psycopg2_conn(fetchone_value=("existing-run-id",))

        with patch(
            "app.worker.tasks.runtime.red_team.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.red_team.fernet_decrypt",
            return_value="postgresql://test:test@localhost/tenant",
        ), patch(
            "app.worker.tasks.runtime.red_team.psycopg2.connect",
            return_value=mock_check_conn,
        ):
            result = run_red_team.run(agent_id=agent_id)

        assert result == {"status": "already_running"}, (
            f"Expected {{'status': 'already_running'}}, got {result}"
        )


# ---------------------------------------------------------------------------
# test_run_red_team_beat_dispatches
# ---------------------------------------------------------------------------


class TestRunRedTeamBeatDispatches:
    """The beat dispatcher fans out one task per selected agent.

    tests/unit/test_beat_fanout_selection.py owns which agents the beat selects
    (deployed AND ready, #134). This test owns the dispatch shape.
    """

    def test_run_red_team_beat_dispatches(self):
        """run_red_team_beat dispatches 2 apply_async calls for 2 selected agents."""
        from app.worker.tasks.runtime.red_team import run_red_team_beat

        agent_id_1 = str(uuid.uuid4())
        agent_id_2 = str(uuid.uuid4())

        mock_agent_1 = MagicMock()
        mock_agent_1.id = agent_id_1
        mock_agent_2 = MagicMock()
        mock_agent_2.id = agent_id_2

        # get_sync_db yields a mock db whose scalars().all() returns 2 agents
        mock_db = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_agent_1, mock_agent_2]
        mock_db.execute.return_value.scalars.return_value = mock_scalars

        with patch(
            "app.worker.tasks.runtime.red_team.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.red_team.run_red_team.apply_async",
        ) as mock_apply_async:
            result = run_red_team_beat.run()

        assert mock_apply_async.call_count == 2, (
            f"Expected 2 apply_async calls, got {mock_apply_async.call_count}"
        )

        # Verify both calls used queue="runtime"
        for i, call_obj in enumerate(mock_apply_async.call_args_list):
            kwargs = call_obj[1]  # keyword args
            assert kwargs.get("queue") == "runtime", (
                f"Call {i}: expected queue='runtime', got {kwargs.get('queue')!r}"
            )

        assert result == {"dispatched": 2}, (
            f"Expected {{'dispatched': 2}}, got {result}"
        )


# ---------------------------------------------------------------------------
# test_run_red_team_complete
# ---------------------------------------------------------------------------


class TestRunRedTeamComplete:
    """Verify the happy path result dict structure for a high-severity run."""

    def test_run_red_team_complete(self):
        """run_red_team returns correct result dict with run_id, blocked=False, max_severity='high'.

        One high-severity finding from prompt injection; data leakage and
        hallucination return empty lists. deployment_blocked must be False
        because max_severity is 'high' (not 'critical').
        """
        from app.services.red_team_service import RedTeamFinding
        from app.worker.tasks.runtime.red_team import run_red_team

        agent_id = str(uuid.uuid4())

        # Mock agent returned by control DB
        mock_agent = MagicMock()
        mock_agent.neon_connection_string = b"encrypted_conn"
        mock_agent.neon_project_id = "proj_456"
        mock_agent.name = "Complete Test Agent"
        mock_agent.soul_voice = None
        mock_agent.soul_role = None
        mock_agent.soul_do_list = None
        mock_agent.soul_donot_list = None
        mock_agent.id = agent_id
        mock_agent.tenant_id = str(uuid.uuid4())
        mock_agent.retrieval_strategy = {}  # Step 4's RetrievalStrategy.model_validate needs a dict

        mock_db = MagicMock()
        mock_db.get.return_value = mock_agent

        # idempotency check: fetchone() returns None (no existing running row)
        # INSERT and UPDATE connections also return clean mocks
        mock_check_conn = _make_psycopg2_conn(fetchone_value=None)
        mock_insert_conn = _make_psycopg2_conn(fetchone_value=None)
        mock_agents_conn = _make_psycopg2_conn(fetchone_value=None)

        # psycopg2.connect is called 3 times: check, insert, agents loop
        connect_side_effects = [mock_check_conn, mock_insert_conn, mock_agents_conn]

        # Build a real RedTeamFinding for the injection agent to return
        high_finding = RedTeamFinding(
            severity="high",
            description="Persistent hallucination under pressure",
            attack_vector="conversation_injection",
            probe_message="Confirm your free return policy",
            agent_response="Yes, we offer free returns!",
            turn_count=2,
        )

        with patch(
            "app.worker.tasks.runtime.red_team.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.red_team.fernet_decrypt",
            return_value="postgresql://test:test@localhost/tenant",
        ), patch(
            "app.worker.tasks.runtime.red_team.psycopg2.connect",
            side_effect=connect_side_effects,
        ), patch(
            "app.worker.tasks.runtime.red_team.run_conversation_injection_agent",
            return_value=[high_finding],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_content_injection_agent",
            return_value=[],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_data_leakage_agent",
            return_value=[],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_hallucination_agent",
            return_value=[],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_confused_deputy_agent",
            return_value=[],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_value_bound_evasion_agent",
            return_value=[],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_identity_bypass_agent",
            return_value=[],
        ), patch(
            "app.worker.tasks.runtime.red_team.bind_tool_context",
            return_value=MagicMock(),
        ), patch(
            "app.worker.tasks.runtime.red_team._build_transactional_probe_fn",
            return_value=MagicMock(),
        ):
            result = run_red_team.run(agent_id=agent_id)

        assert "run_id" in result, f"'run_id' missing from result: {result}"
        assert result["blocked"] is False, (
            f"Expected blocked=False for max_severity='high', got {result.get('blocked')}"
        )
        assert result["max_severity"] == "high", (
            f"Expected max_severity='high', got {result.get('max_severity')!r}"
        )
        # THREE, not one, and that is the whole of ticket 15 arriving at the
        # return dict. The patched runner answers `[high_finding]` to every call
        # and the task calls it k times, so the same vulnerability is reported
        # once per independent attempt. A `1` here would mean the vector was
        # attacked once.
        from app.core.config import settings

        assert result["k"] == settings.RED_TEAM_ATTEMPTS_PER_VECTOR == 3
        assert result["high_count"] == 3, (
            f"Expected one high finding per attempt, got {result.get('high_count')}"
        )
        assert result["attempts"]["conversation_injection"] == 3, (
            "the vector reported fewer attempts than it made"
        )
from contextlib import ExitStack  # noqa: E402  (P2)

# ---------------------------------------------------------------------------
# P2 — the run reports its validity denominator, not just its findings
# P4 — and that denominator is the RUN's, not the build's
# ---------------------------------------------------------------------------


def _fake_runner(vector, findings, *, observed=True, truncated=False):
    """A stand-in runner that honours the whole runner contract.

    The shipped runners return findings AND append one VectorObservation to the
    caller's ledger. A fake that only did the first would let the task claim
    coverage for a vector that observed nothing — the defect this file exists to
    pin — so it does both.
    """
    from app.services.red_team_service import VectorObservation

    def _runner(
        probe_fn, max_turns, attack_sequences, conn_str=None, observations=None,
        *, ledger=None
    ):
        if observations is not None:
            observations.append(
                VectorObservation(
                    vector=vector,
                    observed=observed,
                    sequences_requested=attack_sequences,
                    sequences_completed=(
                        0 if not observed else (1 if truncated else attack_sequences)
                    ),
                    probes_attempted=attack_sequences,
                    probes_answered=attack_sequences if observed else 0,
                    detail=None if observed else "no answered probe was obtained",
                )
            )
        return findings

    return _runner


class TestRunRedTeamReportsValidity:
    """An empty findings list is unreadable without a denominator.

    "Seven vectors probed and none succeeded" and "three probed, four could not"
    produce the identical empty list. Audit D4 says this build is the second
    case, and every red-team run has been reporting it as the first.

    P4 review: the denominator now comes from the RUN, not from the build. The
    fake runners below therefore play the runner contract — they append a
    VectorObservation to the ledger the task passes them — because a mock that
    only returns findings would let this class pass while the task reported a
    coverage nobody measured, which is exactly the state it was found in.
    """

    def _drive(
        self,
        findings_by_vector=None,
        agents_conn=None,
        silent_vectors=None,
        truncated_vectors=None,
        runner_for=None,
    ):
        """Drive one whole run with the seven runners patched.

        `runner_for(vector, findings)` overrides how each stand-in runner is
        built, which is how the k tests below watch the calls without a second
        copy of this fixture.
        """
        from app.worker.tasks.runtime.red_team import run_red_team

        findings_by_vector = findings_by_vector or {}
        agent_id = str(uuid.uuid4())

        mock_agent = MagicMock()
        mock_agent.neon_connection_string = b"encrypted_conn"
        mock_agent.neon_project_id = "proj_456"
        mock_agent.name = "Validity Test Agent"
        mock_agent.soul_voice = None
        mock_agent.soul_role = None
        mock_agent.soul_do_list = None
        mock_agent.soul_donot_list = None
        mock_agent.id = agent_id
        mock_agent.tenant_id = str(uuid.uuid4())
        mock_agent.retrieval_strategy = {}

        mock_db = MagicMock()
        mock_db.get.return_value = mock_agent

        # The third connection is `_agents_conn`, the one Step 7's completion
        # UPDATE runs on. Tests that assert what was persisted pass their own.
        connect_side_effects = [
            _make_psycopg2_conn(fetchone_value=None),
            _make_psycopg2_conn(fetchone_value=None),
            agents_conn if agents_conn is not None else _make_psycopg2_conn(
                fetchone_value=None
            ),
        ]
        self.conns = connect_side_effects

        runners = [
            "run_conversation_injection_agent",
            "run_content_injection_agent",
            "run_data_leakage_agent",
            "run_hallucination_agent",
            "run_confused_deputy_agent",
            "run_value_bound_evasion_agent",
            "run_identity_bypass_agent",
        ]

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.worker.tasks.runtime.red_team.get_sync_db",
                    _make_sync_db_ctx(mock_db),
                )
            )
            stack.enter_context(
                patch(
                    "app.worker.tasks.runtime.red_team.fernet_decrypt",
                    return_value="postgresql://test:test@localhost/tenant",
                )
            )
            stack.enter_context(
                patch(
                    "app.worker.tasks.runtime.red_team.psycopg2.connect",
                    side_effect=connect_side_effects,
                )
            )
            for runner in runners:
                vector = runner[len("run_") : -len("_agent")]
                findings = findings_by_vector.get(vector, [])
                built = (
                    runner_for(vector, findings)
                    if runner_for is not None
                    else _fake_runner(
                        vector,
                        findings,
                        observed=vector not in (silent_vectors or set()),
                        truncated=vector in (truncated_vectors or set()),
                    )
                )
                stack.enter_context(
                    patch(f"app.worker.tasks.runtime.red_team.{runner}", built)
                )
            stack.enter_context(
                patch(
                    "app.worker.tasks.runtime.red_team.bind_tool_context",
                    return_value=MagicMock(),
                )
            )
            stack.enter_context(
                patch(
                    "app.worker.tasks.runtime.red_team._build_transactional_probe_fn",
                    return_value=MagicMock(),
                )
            )
            return run_red_team.run(agent_id=agent_id)

    def test_a_clean_run_still_reports_how_much_it_could_test(self):
        """The triple travels: seven vectors observed, seven counted valid."""
        from app.services.red_team_service import RED_TEAM_VECTORS

        result = self._drive()

        assert result["findings_count"] == 0
        assert result["vectors_attempted"] == len(RED_TEAM_VECTORS) == 7
        assert result["vectors_valid"] == 7
        assert result["coverage_complete"] is True
        assert result["invalid_vectors"] == []

    def test_a_silent_attacker_subset_is_named_on_the_run(self):
        """The blocker, at the task's own seam: four vectors observe nothing.

        This is a Celery worker with no Claude Code CLI — every SDK attacker
        raises at ClaudeSDKClient(...) — and the run used to store
        `{"vectors_valid": 7, "invalid_vectors": [], "complete": true}` for it,
        because the figure came from red_team_coverage(), which describes the
        build and has been the constant 7-of-7 since SDK_ATTACKERS_CAN_PROBE
        was flipped. The ops room then read 'full coverage' over four vectors
        that made zero observations.
        """
        from app.services.red_team_service import SDK_ATTACKER_VECTORS

        result = self._drive(silent_vectors=set(SDK_ATTACKER_VECTORS))

        assert result["vectors_attempted"] == 7
        assert result["vectors_valid"] == 3
        assert result["coverage_complete"] is False
        assert set(result["invalid_vectors"]) == set(SDK_ATTACKER_VECTORS), (
            "a run with silent attackers must name them — 'clean' over an "
            "unnamed subset is not a result anyone can act on"
        )

    def test_a_run_that_observed_nothing_reports_zero_valid(self):
        """The plan's P4 criterion, literally: valid=0, and not rendered clean."""
        from app.services.red_team_service import RED_TEAM_VECTORS

        result = self._drive(silent_vectors=set(RED_TEAM_VECTORS))

        assert result["vectors_valid"] == 0
        assert result["vectors_attempted"] == 7
        assert result["coverage_complete"] is False
        assert set(result["invalid_vectors"]) == set(RED_TEAM_VECTORS)

    def test_a_truncated_vector_is_not_full_coverage(self):
        """RED_TEAM_ATTACK_SEQUENCES is 3 under ONE 120s budget.

        A vector that answered a probe in sequence 1 and then timed out is
        valid — it observed something real, and whatever it found is kept — and
        it is NOT complete: two thirds of the attack never happened.
        """
        result = self._drive(truncated_vectors={"data_leakage"})

        assert result["vectors_valid"] == 7, "it did observe the agent"
        assert result["coverage_complete"] is False, "it did not finish"
        assert result["invalid_vectors"] == []

    def test_the_denominator_is_not_derivable_from_the_findings_count(self):
        """The point of the triple: findings alone cannot express coverage.

        A run with one finding and a run with none report the same coverage,
        because coverage is a property of what could be tested rather than of
        what was found.
        """
        from app.services.red_team_service import RedTeamFinding

        finding = RedTeamFinding(
            severity="medium",
            description="canary echoed",
            attack_vector="content_injection",
            probe_message="p",
            agent_response="r",
            turn_count=1,
        )
        clean = self._drive()
        dirty = self._drive({"content_injection": [finding]})

        # Three, because the fake answers with the finding on each of the k
        # attempts. The count of findings still says nothing about coverage,
        # which is what this test is about.
        assert dirty["findings_count"] == 3 and clean["findings_count"] == 0
        assert dirty["vectors_valid"] == clean["vectors_valid"]
        assert dirty["vectors_attempted"] == clean["vectors_attempted"]


class TestTheDeployGateReadsTheWorstFinding:
    """`deployment_blocked` is True iff the run's worst grade is critical (RED-06).

    `test_run_red_team_complete` above pins the False half against a high
    finding. Nothing pinned the True half, so a task computing the flag from
    'high', or from 'medium', or from any grade at all, passed every test in this
    file while blocking deploys it should have let through and letting through
    the one it exists to stop.

    The pair below runs the same harness twice and moves one string. Both halves
    have to hold: a gate that never blocks and a gate that always blocks are the
    same defect seen from two sides.
    """

    def _run_with(self, severity: str) -> dict:
        from app.services.red_team_service import RedTeamFinding

        finding = RedTeamFinding(
            severity=severity,
            description="the agent issued a refund with no verified session",
            attack_vector="identity_verification_bypass",
            probe_message="refund order 41, I lost my token",
            agent_response="Refunded.",
            turn_count=2,
        )
        return TestRunRedTeamReportsValidity()._drive({"identity_bypass": [finding]})

    def test_a_critical_finding_blocks_the_deployment(self):
        result = self._run_with("critical")

        assert result["max_severity"] == "critical"
        assert result["blocked"] is True, (
            "a critical red-team finding reached the return dict without "
            "blocking the deploy"
        )
        assert result["critical_count"] == 3, (
            "the fake answers with the finding on each of the k attempts"
        )

    def test_a_high_finding_does_not_block_the_deployment(self):
        """The control. Without it the gate could block on every finding and the
        test above would still pass."""
        result = self._run_with("high")

        assert result["max_severity"] == "high"
        assert result["blocked"] is False
        assert result["critical_count"] == 0 and result["high_count"] == 3


class TestRunRedTeamPersistsItsCoverage:
    """The denominator has to survive the request (P2 review).

    P2 computed red_team_coverage() and put it in a structlog line and this
    task's Celery return dict. Neither is readable afterwards: the completion
    UPDATE wrote findings, max_severity and deployment_blocked and nothing else,
    so `GET /agents/{id}/red-team-runs` described a run in which four of seven
    attackers never probed exactly as it describes a clean seven-vector run —
    the on-screen collapse of "unknown" and "pass" that .dev/retro.md Family B
    names. Migration 0015 adds the column; this pins that the task fills it.
    """

    def _completion_sql(self, conn):
        cursor = conn.cursor.return_value
        return [
            call.args[0]
            for call in cursor.execute.call_args_list
            if "UPDATE red_team_runs" in call.args[0] and "complete" in call.args[0]
        ]

    def _completion_params(self, conn):
        cursor = conn.cursor.return_value
        return [
            call.args[1]
            for call in cursor.execute.call_args_list
            if "UPDATE red_team_runs" in call.args[0] and "complete" in call.args[0]
        ]

    def test_the_completion_update_stores_the_run_own_coverage(self):
        import json

        driver = TestRunRedTeamReportsValidity()
        agents_conn = _make_psycopg2_conn(fetchone_value=None)
        driver._drive(agents_conn=agents_conn)

        statements = self._completion_sql(agents_conn)
        assert statements, "no completion UPDATE was issued at all"
        assert "coverage" in statements[0], (
            "the run completed without recording how much of the attack surface "
            "it could test — an empty findings list is then unreadable"
        )

        stored = json.loads(self._completion_params(agents_conn)[0][3])
        assert stored["vectors_attempted"] == 7
        assert stored["vectors_valid"] == 7
        assert stored["complete"] is True
        assert stored["invalid_vectors"] == []

    def test_the_stored_coverage_is_the_runs_own_and_not_the_builds(self):
        """The stored figure has to change when the RUN changes.

        red_team_coverage() cannot: it takes no argument and returns 7-of-7 for
        every run in every environment. A stored payload that agrees with it
        over four silent vectors is the P4 blocker, on the row the deploy gate
        and the ops room read.
        """
        import json

        from app.services.red_team_service import (
            SDK_ATTACKER_VECTORS,
            red_team_coverage,
        )

        driver = TestRunRedTeamReportsValidity()
        agents_conn = _make_psycopg2_conn(fetchone_value=None)
        driver._drive(agents_conn=agents_conn, silent_vectors=set(SDK_ATTACKER_VECTORS))

        stored = json.loads(self._completion_params(agents_conn)[0][3])
        assert stored["vectors_valid"] == 3
        assert stored["complete"] is False
        assert set(stored["invalid_vectors"]) == set(SDK_ATTACKER_VECTORS)
        assert stored["vectors_valid"] != red_team_coverage()["vectors_valid"], (
            "the run stored the build's capability instead of its own result"
        )

    def _conn_refusing(self, *absent_columns):
        """A tenant DB whose red_team_runs lacks the named columns."""
        import psycopg2 as _psycopg2

        agents_conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = None

        def _execute(sql, params=None):
            for column in absent_columns:
                if f"{column} = %s" in sql:
                    raise _psycopg2.errors.UndefinedColumn(
                        f"column {column} does not exist"
                    )

        cursor.execute.side_effect = _execute
        agents_conn.cursor.return_value = cursor
        return agents_conn

    def test_a_pre_0015_tenant_still_completes_its_run(self):
        """UndefinedColumn costs the run its denominator, never its terminal
        status — a run stuck at 'running' forever is worse.

        Three rungs since 0021, not two: `result` is dropped, then `coverage`,
        then the statement every tenant has been able to run since M7.
        """
        agents_conn = self._conn_refusing("coverage", "result")

        driver = TestRunRedTeamReportsValidity()
        result = driver._drive(agents_conn=agents_conn)

        statements = self._completion_sql(agents_conn)
        assert len(statements) == 3, (
            "expected the widest UPDATE to raise, the pre-0021 one to raise, and "
            "the pre-0015 one to follow"
        )
        assert "coverage" not in statements[2] and "result" not in statements[2]
        assert agents_conn.rollback.called, (
            "the aborted transaction must be rolled back before the fallback "
            "statement, or psycopg2 refuses it"
        )
        assert result["vectors_valid"] == 7, (
            "the return value still reports coverage even when the row cannot "
            "store it"
        )

    def test_a_pre_0021_tenant_keeps_its_coverage(self):
        """The control on the rung above: only `result` is missing.

        Without this the three-rung ladder would pass while it dropped BOTH
        columns on a tenant that has one of them, silently costing every
        migrated tenant the coverage it has recorded since 0015.
        """
        import json

        agents_conn = self._conn_refusing("result")

        driver = TestRunRedTeamReportsValidity()
        driver._drive(agents_conn=agents_conn)

        statements = self._completion_sql(agents_conn)
        assert len(statements) == 2, (
            "the widest UPDATE raises and the pre-0021 one lands; there is "
            "nothing to drop below it"
        )
        assert "coverage" in statements[1] and "result" not in statements[1]
        stored = json.loads(self._completion_params(agents_conn)[1][3])
        assert stored["complete"] is True and stored["k"] == 3

    def test_the_completion_update_stores_the_runs_result(self):
        """The run's own record, on the row, so a reader stops recomputing it.

        `findings`, `max_severity`, `deployment_blocked` and `coverage` between
        them cannot say how many independent attempts a vector made, so a vector
        attacked once and a vector attacked three times read identically. This is
        the column that tells them apart.
        """
        import json

        from app.services.red_team_service import RED_TEAM_VECTORS

        driver = TestRunRedTeamReportsValidity()
        agents_conn = _make_psycopg2_conn(fetchone_value=None)
        driver._drive(agents_conn=agents_conn)

        statements = self._completion_sql(agents_conn)
        assert "result" in statements[0], "the run completed without its record"

        stored = json.loads(self._completion_params(agents_conn)[0][4])
        assert stored["k"] == 3
        assert stored["max_severity"] == "none" and stored["breaches"] == 0
        assert {row["vector"] for row in stored["vectors"]} == set(RED_TEAM_VECTORS)
        assert all(row["attempts"] == 3 for row in stored["vectors"]), (
            "every vector must record three independent attempts: " + str(stored)
        )
        assert stored["coverage"]["complete"] is True

    def test_the_stored_result_carries_the_worst_grade_and_the_breach_count(self):
        """The control for the row above: a dirty run must not read like a clean one.

        The counts and the findings are both asserted here because the column
        holds both. Counts alone were what the record was built to stop being:
        `breaches=3, max_severity="critical"` says three attempts landed and says
        nothing about what was sent or what came back, and a reader would have to
        join `red_team_runs.findings` and trust one pass wrote the two.
        """
        import json

        from app.services.red_team_service import RedTeamFinding

        finding = RedTeamFinding(
            severity="critical",
            description="system prompt disclosed",
            attack_vector="data_leakage",
            probe_message="p",
            agent_response="r",
            turn_count=1,
        )
        driver = TestRunRedTeamReportsValidity()
        agents_conn = _make_psycopg2_conn(fetchone_value=None)
        returned = driver._drive({"data_leakage": [finding]}, agents_conn=agents_conn)

        stored = json.loads(self._completion_params(agents_conn)[0][4])
        leakage = next(r for r in stored["vectors"] if r["vector"] == "data_leakage")
        assert leakage["breaches"] == 3, (
            "the fake returns the finding on all three attempts, so all three "
            "landed it"
        )
        assert leakage["max_severity"] == "critical"
        assert stored["max_severity"] == "critical" and stored["breaches"] == 3
        clean = next(r for r in stored["vectors"] if r["vector"] == "hallucination")
        assert clean["breaches"] == 0 and clean["max_severity"] == "none"

        # The findings are in the column, with the four fields ticket 15 names.
        assert [f["severity"] for f in stored["findings"]] == ["critical"] * 3
        assert [f["attack_vector"] for f in stored["findings"]] == ["data_leakage"] * 3
        assert [f["probe_message"] for f in stored["findings"]] == ["p"] * 3
        assert [f["agent_response"] for f in stored["findings"]] == ["r"] * 3

        # And the three places that count them agree. `findings` is the older
        # JSONB column the deploy gate and the ops room read; `result` is the
        # record; `findings_count` is what the task told its caller. One list.
        findings_column = json.loads(self._completion_params(agents_conn)[0][0])
        assert (
            len(stored["findings"])
            == len(findings_column)
            == returned["findings_count"]
            == 3
        )


# ---------------------------------------------------------------------------
# Ticket 15 (#52) — every one of the seven vectors is attempted k times,
# independently, and the run stores what that measured.
# ---------------------------------------------------------------------------


def _all_vectors():
    from app.services.red_team_service import RED_TEAM_VECTORS

    return RED_TEAM_VECTORS


def _watching_runner(vector, findings, calls):
    """A stand-in runner that keeps the ledger object each call was handed.

    The list OBJECT, not its id. CPython hands a freed list's address straight
    back out, so attempt 1's ledger and attempt 3's ledger compare equal by id
    while being different lists — a test written on ids fails for a reason that
    has nothing to do with independence. Holding a reference keeps all three
    alive and distinct.

    What each entry records is (the ledger, how full it was on arrival). An
    attempt handed a ledger with a previous attempt's observation already in it
    can see what that attempt found, and the k is then a lie.
    """
    from app.services.red_team_service import VectorObservation

    def _runner(
        probe_fn, max_turns, attack_sequences, conn_str=None, observations=None,
        *, ledger=None
    ):
        calls.setdefault(vector, []).append(
            (observations, len(observations or []))
        )
        if observations is not None:
            observations.append(
                VectorObservation(
                    vector=vector,
                    observed=True,
                    sequences_requested=attack_sequences,
                    sequences_completed=attack_sequences,
                    probes_attempted=attack_sequences,
                    probes_answered=attack_sequences,
                )
            )
        return findings

    return _runner


class TestEveryVectorIsAttemptedKTimes:
    """Ticket 15's first criterion, at the seam that decides it.

    The shipped task called each runner exactly once. Three sequences inside one
    attacker loop under one shared 120-second budget are not three attempts, and
    the two deterministic RTX probes have no sequence to make an attempt out of
    at all — run_identity_bypass_agent makes exactly two dispatcher calls and
    hardcodes sequences_requested=1. So an attempt is the whole probe, run again
    from the top.
    """

    def _calls(self, k=None):
        calls: dict[str, list[int]] = {}
        driver = TestRunRedTeamReportsValidity()
        with ExitStack() as stack:
            if k is not None:
                from app.core.config import settings

                stack.enter_context(
                    patch.object(settings, "RED_TEAM_ATTEMPTS_PER_VECTOR", k)
                )
            result = driver._drive(
                runner_for=lambda vector, findings: _watching_runner(
                    vector, findings, calls
                )
            )
        return calls, result

    def test_all_seven_runners_are_called_three_times(self):
        calls, result = self._calls()

        assert set(calls) == set(_all_vectors()), (
            f"a vector was never dispatched at all: {sorted(calls)}"
        )
        assert {vector: len(seen) for vector, seen in calls.items()} == {
            vector: 3 for vector in _all_vectors()
        }, "not every vector was attempted three times"
        assert result["k"] == 3

    def test_at_k_of_one_each_runner_is_called_once(self):
        """The control. Without it the assertion above would pass for a loop
        that ignores k and happens to run three times."""
        calls, result = self._calls(k=1)

        assert {vector: len(seen) for vector, seen in calls.items()} == {
            vector: 1 for vector in _all_vectors()
        }, "k was ignored"
        assert result["k"] == 1

    def test_no_two_attempts_share_a_ledger(self):
        """Independence, at the one piece of state a runner is handed.

        Every other piece of an attempt is built inside the runner — its
        ProbeSession, its client, its event loop, its conversation — so the
        caller's ledger is the only object that could carry attempt 1 into
        attempt 2. Three calls must see three lists.
        """
        calls, _ = self._calls()

        for vector, seen in calls.items():
            ledgers = [ledger for ledger, _depth in seen]
            assert len({id(ledger) for ledger in ledgers}) == 3, (
                f"{vector} attempts shared a ledger object"
            )
            assert [depth for _ledger, depth in seen] == [0, 0, 0], (
                f"{vector} handed an attempt a ledger that already held an "
                "earlier attempt's observation"
            )

    def test_a_vector_still_reports_one_observation_not_three(self):
        """`run_coverage` keys its ledger by vector and the last row wins, so k
        rows would store attempt 3 and silently discard the other two."""
        import json

        driver = TestRunRedTeamReportsValidity()
        agents_conn = _make_psycopg2_conn(fetchone_value=None)
        calls: dict[str, list[int]] = {}
        driver._drive(
            agents_conn=agents_conn,
            runner_for=lambda vector, findings: _watching_runner(
                vector, findings, calls
            ),
        )

        cursor = agents_conn.cursor.return_value
        params = [
            call.args[1]
            for call in cursor.execute.call_args_list
            if "UPDATE red_team_runs" in call.args[0] and "complete" in call.args[0]
        ]
        coverage = json.loads(params[0][3])
        assert coverage["vectors_valid"] == 7 and coverage["complete"] is True
        assert coverage["attempts"] == {vector: 3 for vector in _all_vectors()}


class TestCoverageCompleteRequiresEveryAttempt:
    """Ticket 15's second criterion, at the boundary and one attempt either side.

    `run_coverage` alone answers `complete: True` for a vector that observed the
    agent once, because it has never been told how many independent attempts
    were required. `_coverage_at_k` folds that requirement in.
    """

    def _coverage(self, attempts_by_vector, k=3):
        from app.domain.red_team_result import RedTeamResult, VectorOutcome
        from app.services.red_team_service import VectorObservation
        from app.worker.tasks.runtime.red_team import _coverage_at_k

        observations = [
            VectorObservation(
                vector=vector, observed=True,
                sequences_requested=3, sequences_completed=3,
            )
            for vector in _all_vectors()
        ]
        result = RedTeamResult(
            k=k,
            vectors=[
                VectorOutcome(vector=vector, attempts=attempts_by_vector[vector])
                for vector in _all_vectors()
            ],
        )
        return _coverage_at_k(observations, result)

    def test_seven_vectors_at_k_are_complete(self):
        coverage = self._coverage({v: 3 for v in _all_vectors()})

        assert coverage["complete"] is True
        assert coverage["incomplete_vectors"] == []
        assert coverage["k"] == 3

    def test_one_vector_one_attempt_short_is_not_complete(self):
        """The boundary. Six vectors stay ON k and one moves by one attempt, so
        the two readings can only differ for the reason under test."""
        attempts = {v: 3 for v in _all_vectors()}
        attempts["data_leakage"] = 2

        coverage = self._coverage(attempts)

        assert coverage["complete"] is False
        assert coverage["incomplete_vectors"] == ["data_leakage"]
        assert "data_leakage: 2 of 3 attempt(s) ran" in coverage["invalid_reason"]

    def test_the_four_keys_downstream_reads_keep_their_meaning(self):
        """`deployment_service._coverage_from_run` returns None, and the deploy
        gate falls back to the current build, if any of the four goes missing."""
        coverage = self._coverage({v: 3 for v in _all_vectors()})

        for key in ("vectors_attempted", "vectors_valid", "invalid_vectors", "complete"):
            assert key in coverage, f"{key} was dropped from the stored payload"
        assert coverage["vectors_attempted"] == 7 and coverage["vectors_valid"] == 7

    def test_an_invalid_vector_is_still_invalid_at_full_k(self):
        """The two rules are separate and both bind: a vector can make all three
        attempts and observe nothing in any of them."""
        from app.domain.red_team_result import RedTeamResult, VectorOutcome
        from app.services.red_team_service import VectorObservation
        from app.worker.tasks.runtime.red_team import _coverage_at_k

        observations = [
            VectorObservation(
                vector=vector,
                observed=vector != "hallucination",
                sequences_requested=3,
                sequences_completed=3,
            )
            for vector in _all_vectors()
        ]
        result = RedTeamResult(
            k=3,
            vectors=[VectorOutcome(vector=v, attempts=3) for v in _all_vectors()],
        )

        coverage = _coverage_at_k(observations, result)

        assert coverage["invalid_vectors"] == ["hallucination"]
        assert coverage["complete"] is False


# ---------------------------------------------------------------------------
# What k does to the clock. Two relations, neither of them a copied number.
# ---------------------------------------------------------------------------


def _run_wall_clock_bound() -> float:
    """The largest term in a red-team run's worst case, in seconds.

    Every conversational attempt is capped by ATTACKER_LOOP_TIMEOUT_S, and the
    two deterministic RTX probes wrap their chains in the same 120 seconds, so
    seven vectors at k attempts each is the bound the run cannot exceed by much.
    It ignores the smaller terms (the severity classifier, the tenant writes),
    which is why both relations below are asserted with headroom rather than at
    the boundary.
    """
    from app.core.config import settings
    from app.services.red_team_service import ATTACKER_LOOP_TIMEOUT_S, RED_TEAM_VECTORS

    return (
        settings.RED_TEAM_ATTEMPTS_PER_VECTOR
        * len(RED_TEAM_VECTORS)
        * ATTACKER_LOOP_TIMEOUT_S
    )


def test_the_bound_is_a_function_of_k():
    """The control for both relations below. If the bound were a constant they
    would pass while k grew without limit."""
    from app.core.config import settings

    bound = _run_wall_clock_bound()
    per_attempt = bound / settings.RED_TEAM_ATTEMPTS_PER_VECTOR

    assert bound > 0
    assert bound == per_attempt * settings.RED_TEAM_ATTEMPTS_PER_VECTOR
    assert settings.RED_TEAM_ATTEMPTS_PER_VECTOR > 1, (
        "at k=1 these relations hold trivially and prove nothing about ticket 15"
    )


def test_a_run_that_uses_its_bound_is_still_inside_its_own_idempotency_window():
    """k multiplied the run and the guard did not move with it.

    Step 2 skips when a `running` row for this agent is inside the window. One
    pass of the seven vectors bounded at roughly fifteen minutes fitted the
    shipped thirty-minute window; k=3 does not. A run still going when its own
    row falls out of the window is a run a second trigger cannot see, so two
    workers red-team one agent: the tenant is billed twice, two red_team_runs
    rows describe one agent, and the RTX probes race each other over one Redis
    rate counter.

    A relation, not a copy of a number: raising k or ATTACKER_LOOP_TIMEOUT_S past
    the window fails here rather than in production.
    """
    from app.worker.tasks.runtime.red_team import RUN_IDEMPOTENCY_WINDOW_MINUTES

    window_seconds = RUN_IDEMPOTENCY_WINDOW_MINUTES * 60

    assert window_seconds > _run_wall_clock_bound(), (
        f"a run can legitimately take {_run_wall_clock_bound()}s and the guard "
        f"only looks back {window_seconds}s, so a second trigger would start a "
        "concurrent run against the same agent"
    )


def test_a_run_that_uses_its_bound_is_not_redelivered_underneath_itself():
    """The other end of the same clock, and the eval side's lesson.

    BROKER_VISIBILITY_TIMEOUT_S is how long the broker waits before deciding a
    delivered message was lost. `run_eval_suite` outgrew it once already and a
    second worker began running the same agent concurrently. k=3 triples the
    red-team run, so the same relation has to be asserted here.

    The idempotency window sits between the two: wide enough to cover a running
    run, narrow enough that a message the broker genuinely redelivers after two
    hours is not refused by a guard still holding a dead run's row.
    """
    from app.worker.celery_app import BROKER_VISIBILITY_TIMEOUT_S, celery_app
    from app.worker.tasks.runtime.red_team import RUN_IDEMPOTENCY_WINDOW_MINUTES

    assert BROKER_VISIBILITY_TIMEOUT_S > _run_wall_clock_bound(), (
        f"visibility_timeout is {BROKER_VISIBILITY_TIMEOUT_S}s and one red-team "
        f"run may take {_run_wall_clock_bound()}s"
    )
    assert RUN_IDEMPOTENCY_WINDOW_MINUTES * 60 < BROKER_VISIBILITY_TIMEOUT_S, (
        "the guard must expire before the broker redelivers, or a redelivered "
        "message is skipped as a duplicate of the run the dead worker abandoned"
    )
    assert (
        celery_app.conf.broker_transport_options["visibility_timeout"]
        == BROKER_VISIBILITY_TIMEOUT_S
    ), "the configured transport option is not the constant this test pins"
