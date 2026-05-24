"""Unit tests for app.services.deployment_service — M8 Pre-deployment Checklist.

Tests:
    TestRunOrchestrator
        test_run_orchestrator_populates_result_container  — DEP-01, DEP-02

    TestDeploymentReport
        test_deployment_report_model_construction         — DEP-02
        test_deployment_report_rejects_invalid_recommendation — Pydantic Literal enforcement

    TestBlockingConditions
        test_block_when_deployment_blocked_true           — DEP-03
        test_block_on_low_eval_metric                     — DEP-03: 0.70 threshold

    TestSignalCollectionFunctions
        test_fetch_eval_summary_sync_returns_correct_shape — signal collection shape
        test_fetch_eval_summary_sync_no_runs              — empty / no-runs branch

Mock strategy:
    - asyncio.run patched at app.services.deployment_service.asyncio.run
    - psycopg2.connect patched at app.services.deployment_service.psycopg2.connect
    - No live Anthropic, Agent SDK, or DB calls in any test
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

import json
import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.services.deployment_service import (
    DeploymentReport,
    DeploymentWarning,
    _DEPLOYMENT_SYSTEM_PROMPT,
    _fetch_eval_summary_sync,
    _make_iframe_snippet,
    run_orchestrator,
)


# ---------------------------------------------------------------------------
# Helper: build a mock psycopg2 connection with controllable cursor
# ---------------------------------------------------------------------------


def _make_psycopg2_conn(fetchone_value=None, fetchall_value=None):
    """Return a mock psycopg2 connection with controllable cursor responses."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = fetchone_value
    mock_cursor.fetchall.return_value = fetchall_value if fetchall_value is not None else []
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn


# ---------------------------------------------------------------------------
# TestRunOrchestrator
# ---------------------------------------------------------------------------


class TestRunOrchestrator:
    """Tests for the Agent SDK Sonnet orchestrator (run_orchestrator)."""

    def test_run_orchestrator_populates_result_container(self):
        """Patch asyncio.run at module boundary; verify result_container["report"] is set.

        DEP-01: run_orchestrator bridges the Celery sync world to async Agent SDK.
        DEP-02: the report dict contains 'recommendation' from the agent's tool call.
        """
        result_container = {}

        def _set_report_side_effect(*args, **kwargs):
            """Simulate the orchestrator setting the report on result_container."""
            result_container["report"] = {
                "recommendation": "ship",
                "summary": "All good.",
                "warnings": [],
            }

        with patch(
            "app.services.deployment_service.asyncio.run",
            side_effect=_set_report_side_effect,
        ) as mock_asyncio_run:
            run_orchestrator(
                json.dumps({"eval_summary": {}, "red_team_summary": {}}),
                result_container,
            )

        assert mock_asyncio_run.called, "asyncio.run should have been called"
        assert result_container["report"]["recommendation"] == "ship"
        assert result_container["report"]["summary"] == "All good."
        assert result_container["report"]["warnings"] == []


# ---------------------------------------------------------------------------
# TestDeploymentReport
# ---------------------------------------------------------------------------


class TestDeploymentReport:
    """Tests for DeploymentReport Pydantic model construction."""

    def test_deployment_report_model_construction(self):
        """Construct a full DeploymentReport and verify field values (DEP-02)."""
        warning = DeploymentWarning(
            warning_id="test",
            category="eval_quality",
            message="Low score",
            severity_level="warning",
        )
        r = DeploymentReport(
            recommendation="ship_with_warnings",
            summary="Some warnings.",
            warnings=[warning],
            eval_summary={},
            red_team_summary={},
            verified_qa_stats={},
            corpus_stats={},
        )
        assert r.recommendation == "ship_with_warnings"
        assert len(r.warnings) == 1
        assert r.warnings[0].warning_id == "test"
        assert r.warnings[0].category == "eval_quality"
        assert r.warnings[0].severity_level == "warning"

    def test_deployment_report_rejects_invalid_recommendation(self):
        """Pydantic Literal enforcement: 'invalid_value' must raise ValidationError."""
        with pytest.raises(ValidationError):
            DeploymentReport(
                recommendation="invalid_value",
                summary="Should fail.",
                warnings=[],
                eval_summary={},
                red_team_summary={},
                verified_qa_stats={},
                corpus_stats={},
            )


# ---------------------------------------------------------------------------
# TestBlockingConditions
# ---------------------------------------------------------------------------


class TestBlockingConditions:
    """Tests for blocking condition logic documented in _DEPLOYMENT_SYSTEM_PROMPT (DEP-03)."""

    def test_block_when_deployment_blocked_true(self):
        """_DEPLOYMENT_SYSTEM_PROMPT documents the deployment_blocked == True gate (DEP-03)."""
        assert "deployment_blocked" in _DEPLOYMENT_SYSTEM_PROMPT, (
            "_DEPLOYMENT_SYSTEM_PROMPT must reference 'deployment_blocked' blocking condition"
        )
        assert "high_count" in _DEPLOYMENT_SYSTEM_PROMPT, (
            "_DEPLOYMENT_SYSTEM_PROMPT must reference DEP_BLOCK_ON_HIGH_RED_TEAM / high_count logic"
        )

    def test_block_on_low_eval_metric(self):
        """_DEPLOYMENT_SYSTEM_PROMPT documents the 0.70 eval threshold (DEP-03)."""
        assert "0.70" in _DEPLOYMENT_SYSTEM_PROMPT, (
            "_DEPLOYMENT_SYSTEM_PROMPT must document the 0.70 eval pass_rate threshold"
        )


# ---------------------------------------------------------------------------
# TestSignalCollectionFunctions
# ---------------------------------------------------------------------------


class TestSignalCollectionFunctions:
    """Tests for _fetch_eval_summary_sync signal collection helper."""

    def test_fetch_eval_summary_sync_returns_correct_shape(self):
        """Mock psycopg2 returns two metric rows; verify result shape and values (DEP-01)."""
        run_id = uuid.uuid4()
        run_ts = datetime(2026, 5, 23, 2, 0, 0)

        mock_conn = _make_psycopg2_conn(
            fetchone_value=(run_id, run_ts),
            fetchall_value=[
                ("faithfulness", Decimal("0.92")),
                ("answer_relevance", Decimal("0.88")),
            ],
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["pass_rates"]["faithfulness"] == pytest.approx(0.92)
        assert result["pass_rates"]["answer_relevance"] == pytest.approx(0.88)
        assert result["scenario_count"] == 2
        assert result["last_run_at"] == run_ts.isoformat()

    def test_fetch_eval_summary_sync_no_runs(self):
        """When fetchone returns None (no eval runs), result is the empty-state dict."""
        mock_conn = _make_psycopg2_conn(fetchone_value=None)

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result == {
            "last_run_at": None,
            "scenario_count": 0,
            "pass_rates": {},
            "failing_scenarios": 0,
        }
