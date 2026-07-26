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

import inspect
import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.services.deployment_service import (
    DeploymentReport,
    DeploymentWarning,
    BLAST_RADIUS_DEFAULT_SIGNAL,
    _DEPLOYMENT_SYSTEM_PROMPT,
    _fetch_blast_radius_sync,
    _fetch_eval_summary_sync,
    _make_iframe_snippet,
    _resolve_blast_radius_thresholds,
    derive_blast_radius_warnings,
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
# Helper: build a mock get_sync_db context manager (control-DB collector)
# ---------------------------------------------------------------------------


def _make_sync_db_ctx(mock_db):
    """Return a patched get_sync_db that yields mock_db when used as 'with get_sync_db() as db'."""
    @contextmanager
    def _fake_get_sync_db():
        yield mock_db

    return _fake_get_sync_db


def _make_scripted_db(script):
    """Build a MagicMock db whose sequential db.execute(...) results are scripted.

    Each item in `script` is a dict with one of "scalar"/"fetchall"/"first"
    mapping to the value db.execute(...).<method>() should return for that
    call, in call order — matching _fetch_blast_radius_sync's /
    _resolve_blast_radius_thresholds's exact query sequence.
    """
    mock_db = MagicMock()
    results = []
    for item in script:
        mock_result = MagicMock()
        if "scalar" in item:
            mock_result.scalar.return_value = item["scalar"]
        if "fetchall" in item:
            mock_result.fetchall.return_value = item["fetchall"]
        if "first" in item:
            mock_result.first.return_value = item["first"]
        results.append(mock_result)
    mock_db.execute.side_effect = results
    return mock_db


# ---------------------------------------------------------------------------
# test_fetch_blast_radius_sync (module scope — T-18-BLR-01 pins this node id)
# ---------------------------------------------------------------------------


def test_fetch_blast_radius_sync():
    """T-18-BLR-01 / OD-1: the configured and observed single-action figures
    are reported under their own distinct keys, with deliberately different
    fixture values (5000 vs 9999) so a transposition bug fails this test.
    """
    mock_db = _make_scripted_db(
        [
            {"scalar": 5000},                    # configured_max_row
            {"scalar": 0},                        # unbounded_single_count
            {"fetchall": [("1/hour", "5000")]},   # enabled_rows
            {"scalar": 9999},                     # observed_single_row (!= 5000)
            {"scalar": 15000},                    # observed_hourly_row
            {"first": (None, None)},              # threshold row -> platform defaults
        ]
    )

    with patch(
        "app.services.deployment_service.get_sync_db",
        _make_sync_db_ctx(mock_db),
    ):
        result = _fetch_blast_radius_sync("test-agent")

    assert set(result.keys()) == {
        "configured_max_single_action_cents",
        "configured_max_hourly_aggregate_cents",
        "observed_max_single_action_cents",
        "observed_max_hourly_aggregate_cents",
        "observed_window_days",
        "warn_threshold_single_cents",
        "warn_threshold_hourly_cents",
        "enabled_skill_count",
    }
    assert result["configured_max_single_action_cents"] == 5000
    assert result["observed_max_single_action_cents"] == 9999
    assert (
        result["configured_max_single_action_cents"]
        != result["observed_max_single_action_cents"]
    )
    assert result["observed_window_days"] == settings.BLAST_RADIUS_OBSERVED_WINDOW_DAYS
    assert result["warn_threshold_single_cents"] == settings.BLAST_RADIUS_WARN_SINGLE_CENTS
    assert result["warn_threshold_hourly_cents"] == settings.BLAST_RADIUS_WARN_HOURLY_CENTS
    assert result["enabled_skill_count"] == 1


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


# ---------------------------------------------------------------------------
# TestBlastRadiusCollector
# ---------------------------------------------------------------------------


class TestBlastRadiusCollector:
    """Tests for _fetch_blast_radius_sync's honest-empty behaviour, its
    unbounded-configuration handling, its per-skill hourly derivation, its
    tenant-vs-platform threshold resolution, and its no-conn_str signature.
    """

    def test_no_qualifying_audit_rows_yields_none_not_zero(self):
        """T-18-BLR-01: NULL observed queries yield None, never 0 (OD-1)."""
        mock_db = _make_scripted_db(
            [
                {"scalar": None},        # configured_max_row: no enabled rows
                {"scalar": 0},           # unbounded_single_count
                {"fetchall": []},        # enabled_rows: none
                {"scalar": None},        # observed_single_row: no qualifying rows
                {"scalar": None},        # observed_hourly_row: no qualifying rows
                {"first": (None, None)},
            ]
        )
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _fetch_blast_radius_sync("test-agent")

        assert result["observed_max_single_action_cents"] is None
        assert result["observed_max_hourly_aggregate_cents"] is None
        assert result["observed_max_single_action_cents"] != 0
        assert result["observed_max_hourly_aggregate_cents"] != 0

    def test_unbounded_enabled_skill_forces_configured_none(self):
        """T-18-BLR-01: one unbounded enabled row makes the whole configured
        ceiling honestly None, even when other enabled rows are bounded
        (a partially-bounded configuration is not a ceiling)."""
        mock_db = _make_scripted_db(
            [
                {"scalar": 5000},   # configured_max_row: max of the bounded rows
                {"scalar": 1},      # unbounded_single_count: one enabled row has no max
                {"fetchall": [("1/hour", "5000"), ("2/hour", None)]},
                {"scalar": None},
                {"scalar": None},
                {"first": (None, None)},
            ]
        )
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _fetch_blast_radius_sync("test-agent")

        assert result["configured_max_single_action_cents"] is None

    def test_configured_hourly_aggregate_sums_per_skill_ceiling_times_rate(self):
        """5000 cents at 2/hour + 10000 cents at 5/hour = 10000 + 50000 = 60000."""
        mock_db = _make_scripted_db(
            [
                {"scalar": 10000},
                {"scalar": 0},
                {"fetchall": [("2/hour", "5000"), ("5/hour", "10000")]},
                {"scalar": None},
                {"scalar": None},
                {"first": (None, None)},
            ]
        )
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _fetch_blast_radius_sync("test-agent")

        assert result["configured_max_hourly_aggregate_cents"] == 60000

    def test_configured_hourly_none_when_any_rate_limit_null(self):
        """A NULL rate_limit on any enabled skill forces the hourly ceiling to None."""
        mock_db = _make_scripted_db(
            [
                {"scalar": 10000},
                {"scalar": 0},
                {"fetchall": [("2/hour", "5000"), (None, "10000")]},
                {"scalar": None},
                {"scalar": None},
                {"first": (None, None)},
            ]
        )
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _fetch_blast_radius_sync("test-agent")

        assert result["configured_max_hourly_aggregate_cents"] is None

    def test_threshold_resolution_prefers_tenant_column_over_platform_default(self):
        mock_db = _make_scripted_db([{"first": (12345, 67890)}])
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _resolve_blast_radius_thresholds("test-agent")

        assert result == (12345, 67890)

    def test_threshold_resolution_falls_back_to_settings_when_null(self):
        mock_db = _make_scripted_db([{"first": (None, None)}])
        with patch(
            "app.services.deployment_service.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ):
            result = _resolve_blast_radius_thresholds("test-agent")

        assert result == (
            settings.BLAST_RADIUS_WARN_SINGLE_CENTS,
            settings.BLAST_RADIUS_WARN_HOURLY_CENTS,
        )

    def test_collector_takes_no_conn_str(self):
        """A future refactor must not quietly reintroduce a connection string
        into this control-DB-only collector (CLAUDE.md rule 4)."""
        assert list(inspect.signature(_fetch_blast_radius_sync).parameters) == ["agent_id"]


# ---------------------------------------------------------------------------
# TestBlastRadiusWarnings
# ---------------------------------------------------------------------------


class TestBlastRadiusWarnings:
    """Tests for derive_blast_radius_warnings — pure, no DB, no LLM (OD-1b)."""

    def test_zero_enabled_skills_returns_empty_list(self):
        """An agent with no enabled transactional skill has no blast radius to warn about."""
        assert derive_blast_radius_warnings({"enabled_skill_count": 0}) == []

    def test_no_ceiling_configured_warning(self):
        blast_radius = {
            "enabled_skill_count": 3,
            "configured_max_single_action_cents": None,
            "configured_max_hourly_aggregate_cents": None,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        result = derive_blast_radius_warnings(blast_radius)
        assert len(result) == 1
        assert result[0].warning_id == "blast_radius_no_ceiling_configured"

    def test_single_action_above_threshold_warning(self):
        blast_radius = {
            "enabled_skill_count": 1,
            "configured_max_single_action_cents": 60000,
            "configured_max_hourly_aggregate_cents": 100000,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        result = derive_blast_radius_warnings(blast_radius)
        warning = next(
            w for w in result if w.warning_id == "blast_radius_single_action_above_threshold"
        )
        assert "600.00" in warning.message

    def test_hourly_aggregate_above_threshold_warning(self):
        blast_radius = {
            "enabled_skill_count": 1,
            "configured_max_single_action_cents": 10000,
            "configured_max_hourly_aggregate_cents": 250000,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        result = derive_blast_radius_warnings(blast_radius)
        ids = {w.warning_id for w in result}
        assert "blast_radius_hourly_aggregate_above_threshold" in ids

    def test_both_above_threshold_warnings_fire_together(self):
        blast_radius = {
            "enabled_skill_count": 2,
            "configured_max_single_action_cents": 60000,
            "configured_max_hourly_aggregate_cents": 250000,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        result = derive_blast_radius_warnings(blast_radius)
        ids = {w.warning_id for w in result}
        assert ids == {
            "blast_radius_single_action_above_threshold",
            "blast_radius_hourly_aggregate_above_threshold",
        }

    def test_at_threshold_boundary_emits_no_warning(self):
        """Strictly-exceeds semantics: equal-to-threshold is not a warning."""
        blast_radius = {
            "enabled_skill_count": 1,
            "configured_max_single_action_cents": 50000,
            "configured_max_hourly_aggregate_cents": 200000,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        assert derive_blast_radius_warnings(blast_radius) == []

    def test_high_observed_maximum_with_within_threshold_configured_ceiling_emits_no_warning(self):
        """History never drives a warning — only the configured ceiling does."""
        blast_radius = {
            "enabled_skill_count": 1,
            "configured_max_single_action_cents": 1000,
            "configured_max_hourly_aggregate_cents": 1000,
            "observed_max_single_action_cents": 999999999,
            "observed_max_hourly_aggregate_cents": 999999999,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
        }
        assert derive_blast_radius_warnings(blast_radius) == []

    def test_no_warning_derived_from_observed_figures(self):
        """T-18-BLR-01: the derivation source must never reference an
        observed_max_ key — no warning is derived from history."""
        source = inspect.getsource(derive_blast_radius_warnings)
        assert "observed_max_" not in source
