"""Unit tests for app.worker.tasks.runtime.red_team — M7 + RTX (Phase 18) Celery tasks.

Tests:
    test_run_red_team_idempotent_skip
        — run_red_team returns {"status": "already_running"} when idempotency guard fires
    test_run_red_team_beat_dispatches
        — run_red_team_beat dispatches one task per ready agent (2 agents → 2 dispatches)
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
    """Verify the beat dispatcher fans out one task per ready agent."""

    def test_run_red_team_beat_dispatches(self):
        """run_red_team_beat dispatches 2 apply_async calls for 2 ready agents."""
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
        assert result["high_count"] == 1, (
            f"Expected high_count=1, got {result.get('high_count')}"
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
    ):
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
                stack.enter_context(
                    patch(
                        f"app.worker.tasks.runtime.red_team.{runner}",
                        _fake_runner(
                            vector,
                            findings_by_vector.get(vector, []),
                            observed=vector not in (silent_vectors or set()),
                            truncated=vector in (truncated_vectors or set()),
                        ),
                    )
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

        assert dirty["findings_count"] == 1 and clean["findings_count"] == 0
        assert dirty["vectors_valid"] == clean["vectors_valid"]
        assert dirty["vectors_attempted"] == clean["vectors_attempted"]


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

    def test_a_pre_0015_tenant_still_completes_its_run(self):
        """UndefinedColumn on `coverage` costs the run its denominator, never
        its terminal status — a run stuck at 'running' forever is worse."""
        import psycopg2 as _psycopg2

        agents_conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = None

        def _execute(sql, params=None):
            if "coverage" in sql:
                raise _psycopg2.errors.UndefinedColumn("column coverage does not exist")

        cursor.execute.side_effect = _execute
        agents_conn.cursor.return_value = cursor

        driver = TestRunRedTeamReportsValidity()
        result = driver._drive(agents_conn=agents_conn)

        statements = self._completion_sql(agents_conn)
        assert len(statements) == 2, (
            "expected the wide UPDATE to raise and the pre-0015 UPDATE to follow"
        )
        assert "coverage" not in statements[1]
        assert agents_conn.rollback.called, (
            "the aborted transaction must be rolled back before the fallback "
            "statement, or psycopg2 refuses it"
        )
        assert result["vectors_valid"] == 7, (
            "the return value still reports coverage even when the row cannot "
            "store it"
        )
