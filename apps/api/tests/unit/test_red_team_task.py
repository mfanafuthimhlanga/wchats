"""Unit tests for app.worker.tasks.runtime.red_team — M7 Celery tasks.

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
    - app.worker.tasks.runtime.red_team.run_prompt_injection_agent etc. patched at boundary
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
            attack_vector="prompt_injection",
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
            "app.worker.tasks.runtime.red_team.run_prompt_injection_agent",
            return_value=[high_finding],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_data_leakage_agent",
            return_value=[],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_hallucination_agent",
            return_value=[],
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
