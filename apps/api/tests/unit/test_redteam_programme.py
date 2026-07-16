"""Unit tests for OPS-13: red-team programme (strategy/probe writes + GET /red-team/programme).

Tests:
    Writes (Task 2, run_red_team) — select via `-k writes`:
        - one red_team_strategies INSERT per distinct attack_vector, using
          ON CONFLICT DO NOTHING
        - one red_team_probes INSERT per finding's probe_message
        - the existing red_team_runs status UPDATE (Step 7) is unaffected

    Service + route tests (Task 3) are appended below this section once
    redteam_programme_service.py and the GET /red-team/programme route exist.
"""

from __future__ import annotations

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
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_sync_db_ctx(mock_db):
    @contextmanager
    def _fake_get_sync_db():
        yield mock_db

    return _fake_get_sync_db


def _make_psycopg2_conn(fetchone_value=None, fetchall_value=None):
    """Return a mock psycopg2 connection whose cursor is controllable."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = fetchone_value
    mock_cursor.fetchall.return_value = fetchall_value or []
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


# ===========================================================================
# Task 2 — run_red_team writes first-class strategy + probe rows (-k writes)
# ===========================================================================


class TestRunRedTeamProgrammeWrites:
    def test_writes_one_strategy_per_distinct_attack_vector_and_one_probe_per_finding(self):
        """Two findings share an attack_vector, one has a different vector ->
        2 distinct strategy upserts, 3 probe inserts (one per finding)."""
        from app.worker.tasks.runtime.red_team import run_red_team
        from app.services.red_team_service import RedTeamFinding

        agent_id = str(uuid.uuid4())

        mock_agent = MagicMock()
        mock_agent.neon_connection_string = b"encrypted_conn"
        mock_agent.neon_project_id = "proj_789"
        mock_agent.name = "Programme Test Agent"
        mock_agent.soul_voice = None
        mock_agent.soul_role = None
        mock_agent.soul_do_list = None
        mock_agent.soul_donot_list = None

        mock_db = MagicMock()
        mock_db.get.return_value = mock_agent

        mock_check_conn, _ = _make_psycopg2_conn(fetchone_value=None)
        mock_insert_conn, _ = _make_psycopg2_conn(fetchone_value=None)
        # strategy_id lookups after ON CONFLICT DO NOTHING — return a fixed id
        mock_agents_conn, mock_agents_cursor = _make_psycopg2_conn(
            fetchone_value=("11111111-1111-1111-1111-111111111111",)
        )

        connect_side_effects = [mock_check_conn, mock_insert_conn, mock_agents_conn]

        findings = [
            RedTeamFinding(
                severity="high",
                description="Injection A",
                attack_vector="prompt_injection",
                probe_message="probe-1",
                agent_response="resp-1",
                turn_count=1,
            ),
            RedTeamFinding(
                severity="medium",
                description="Injection B",
                attack_vector="prompt_injection",
                probe_message="probe-2",
                agent_response="resp-2",
                turn_count=2,
            ),
            RedTeamFinding(
                severity="low",
                description="Leakage A",
                attack_vector="data_leakage",
                probe_message="probe-3",
                agent_response="resp-3",
                turn_count=1,
            ),
        ]

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
            return_value=findings[:2],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_data_leakage_agent",
            return_value=findings[2:],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_hallucination_agent",
            return_value=[],
        ):
            result = run_red_team.run(agent_id=agent_id)

        assert "run_id" in result

        executed_sql = [c.args[0] for c in mock_agents_cursor.execute.call_args_list]

        strategy_inserts = [s for s in executed_sql if "INSERT INTO red_team_strategies" in s]
        assert len(strategy_inserts) == 2, (
            f"Expected 2 strategy upserts (one per distinct attack_vector), got {len(strategy_inserts)}"
        )
        for s in strategy_inserts:
            assert "ON CONFLICT" in s, "Strategy upsert must use ON CONFLICT DO NOTHING"
            assert "DO NOTHING" in s

        probe_inserts = [s for s in executed_sql if "INSERT INTO red_team_probes" in s]
        assert len(probe_inserts) == 3, (
            f"Expected 3 probe inserts (one per finding), got {len(probe_inserts)}"
        )

        # The existing run-row UPDATE (Step 7) must still occur.
        run_updates = [s for s in executed_sql if "UPDATE red_team_runs" in s]
        assert len(run_updates) == 1, "The existing run-row status UPDATE must still occur"
        assert "SET status = 'complete'" in run_updates[0]

    def test_no_findings_skips_strategy_and_probe_writes_but_run_row_still_updates(self):
        """Zero findings -> no strategy/probe writes, but the run-row UPDATE still fires."""
        from app.worker.tasks.runtime.red_team import run_red_team

        agent_id = str(uuid.uuid4())

        mock_agent = MagicMock()
        mock_agent.neon_connection_string = b"encrypted_conn"
        mock_agent.neon_project_id = "proj_000"
        mock_agent.name = "No Findings Agent"
        mock_agent.soul_voice = None
        mock_agent.soul_role = None
        mock_agent.soul_do_list = None
        mock_agent.soul_donot_list = None

        mock_db = MagicMock()
        mock_db.get.return_value = mock_agent

        mock_check_conn, _ = _make_psycopg2_conn(fetchone_value=None)
        mock_insert_conn, _ = _make_psycopg2_conn(fetchone_value=None)
        mock_agents_conn, mock_agents_cursor = _make_psycopg2_conn(fetchone_value=None)

        connect_side_effects = [mock_check_conn, mock_insert_conn, mock_agents_conn]

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
            return_value=[],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_data_leakage_agent",
            return_value=[],
        ), patch(
            "app.worker.tasks.runtime.red_team.run_hallucination_agent",
            return_value=[],
        ):
            result = run_red_team.run(agent_id=agent_id)

        assert result["max_severity"] == "none"

        executed_sql = [c.args[0] for c in mock_agents_cursor.execute.call_args_list]
        assert not [s for s in executed_sql if "INSERT INTO red_team_strategies" in s]
        assert not [s for s in executed_sql if "INSERT INTO red_team_probes" in s]
        run_updates = [s for s in executed_sql if "UPDATE red_team_runs" in s]
        assert len(run_updates) == 1
