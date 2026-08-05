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

import os
import base64

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
import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, call


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
        from app.worker.tasks.runtime.red_team import run_red_team
        from app.services.red_team_service import RedTeamFinding

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
            "app.worker.tasks.runtime.red_team.build_tool_server",
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
# ---------------------------------------------------------------------------


class TestRunRedTeamReportsValidity:
    """An empty findings list is unreadable without a denominator.

    "Seven vectors probed and none succeeded" and "three probed, four could not"
    produce the identical empty list. Audit D4 says this build is the second
    case, and every red-team run has been reporting it as the first.
    """

    def _drive(self, findings_by_vector=None):
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

        connect_side_effects = [
            _make_psycopg2_conn(fetchone_value=None),
            _make_psycopg2_conn(fetchone_value=None),
            _make_psycopg2_conn(fetchone_value=None),
        ]

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
                        return_value=findings_by_vector.get(vector, []),
                    )
                )
            stack.enter_context(
                patch(
                    "app.worker.tasks.runtime.red_team.build_tool_server",
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
        result = self._drive()

        assert result["findings_count"] == 0
        assert result["vectors_attempted"] == 7
        assert result["vectors_valid"] == 3, (
            "four conversational attackers cannot probe in this build (D4)"
        )
        assert result["coverage_complete"] is False
        assert result["invalid_vectors"], (
            "a run with silent attackers must name them — 'clean' over an "
            "unnamed subset is not a result anyone can act on"
        )

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
