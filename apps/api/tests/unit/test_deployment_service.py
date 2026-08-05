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

import psycopg2
import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.services.deployment_service import (
    DeploymentReport,
    DeploymentWarning,
    BLAST_RADIUS_DEFAULT_SIGNAL,
    EVAL_SIGNAL_MEASURED,
    EVAL_SIGNAL_NO_RUNS,
    EVAL_SIGNAL_NO_VALID_SCORES,
    EVAL_SIGNAL_UNAVAILABLE,
    EVAL_SUMMARY_UNAVAILABLE_SIGNAL,
    COVERAGE_SOURCE_CURRENT_BUILD,
    COVERAGE_SOURCE_RUN,
    DENOMINATOR_SOURCE_EVAL_RESULTS,
    DENOMINATOR_SOURCE_RUN_CONFIG,
    RED_TEAM_SIGNAL_MEASURED,
    RED_TEAM_SIGNAL_NO_RUNS,
    RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL,
    _DEPLOYMENT_SYSTEM_PROMPT,
    _compute_envelope_hash_sync,
    _fetch_blast_radius_sync,
    _fetch_eval_summary_sync,
    _fetch_red_team_summary_sync,
    _make_iframe_snippet,
    _resolve_blast_radius_thresholds,
    apply_signal_evidence_gate,
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


def _make_envelope_mock_db(rows):
    """Build a MagicMock db whose db.execute(...).mappings().all() returns rows.

    Matches _fetch_envelope_rows_sync's exact call shape:
    with get_sync_db() as db: db.execute(text(...), {...}).mappings().all().
    """
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = rows
    mock_db.execute.return_value = mock_result
    return mock_db


# ---------------------------------------------------------------------------
# test_envelope_hash_stability (module scope — 18-VALIDATION.md pins this node id)
# ---------------------------------------------------------------------------


def test_envelope_hash_stability():
    """T-18-BLR-02: the sync envelope-hash reader is stable under a no-op
    re-save (row order change), sensitive to a semantic field change, and
    structurally excludes id/agent_id/updated_at at the query layer."""
    rows_a = [
        {
            "skill": "issue_refund",
            "enabled": True,
            "rate_limit": "5/hour",
            "constraints": {"max_amount_cents": 5000},
            "requires_confirmation": False,
            "requires_identity_verification": False,
            "actor_mode": "always-on",
        },
        {
            "skill": "place_order",
            "enabled": True,
            "rate_limit": "10/hour",
            "constraints": {"max_amount_cents": 100000},
            "requires_confirmation": False,
            "requires_identity_verification": False,
            "actor_mode": "always-on",
        },
    ]

    mock_db_1 = _make_envelope_mock_db(rows_a)
    with patch("app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_1)):
        hash_1 = _compute_envelope_hash_sync("agent-a")

    mock_db_2 = _make_envelope_mock_db(rows_a)
    with patch("app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_2)):
        hash_2 = _compute_envelope_hash_sync("agent-a")

    assert hash_1 == hash_2
    assert len(hash_1) == 64
    assert all(c in "0123456789abcdef" for c in hash_1)

    # Row order change (the SELECT is ORDER BY skill, but the canonicaliser
    # is order-independent regardless — assert that independence directly).
    rows_reordered = list(reversed(rows_a))
    mock_db_3 = _make_envelope_mock_db(rows_reordered)
    with patch("app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_3)):
        hash_reordered = _compute_envelope_hash_sync("agent-a")
    assert hash_reordered == hash_1

    # A semantic field change (constraints.max_amount_cents) yields a
    # different hash.
    rows_changed = [dict(rows_a[0]), dict(rows_a[1])]
    rows_changed[0] = dict(rows_changed[0])
    rows_changed[0]["constraints"] = {"max_amount_cents": 9999}
    mock_db_4 = _make_envelope_mock_db(rows_changed)
    with patch("app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_4)):
        hash_changed = _compute_envelope_hash_sync("agent-a")
    assert hash_changed != hash_1

    # Structural Pitfall-2 guard: the SELECT column list contains none of
    # id / agent_id / updated_at (agent_id legitimately appears in the WHERE
    # clause as the filter parameter name — only the SELECT projection is
    # asserted here).
    sql_str = str(mock_db_4.execute.call_args[0][0])
    select_clause = sql_str.upper().split("FROM", 1)[0]
    projected_columns = {
        c.strip() for c in select_clause.replace("SELECT", "", 1).split(",")
    }
    assert "ID" not in projected_columns
    assert "AGENT_ID" not in projected_columns
    assert "UPDATED_AT" not in projected_columns


# ---------------------------------------------------------------------------
# TestEnvelopeHashSync
# ---------------------------------------------------------------------------


class TestEnvelopeHashSync:
    """Tests pinning that _compute_envelope_hash_sync delegates to the single
    shared canonicaliser rather than re-implementing hashing, that its
    signature carries no conn_str, and that an empty envelope hashes to a
    stable, non-empty value."""

    def test_compute_envelope_hash_sync_delegates_to_capability_service(self):
        rows = [
            {
                "skill": "issue_refund",
                "enabled": True,
                "rate_limit": "5/hour",
                "constraints": {},
                "requires_confirmation": False,
                "requires_identity_verification": False,
                "actor_mode": "always-on",
            }
        ]
        mock_db = _make_envelope_mock_db(rows)
        with patch(
            "app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db)
        ), patch(
            "app.services.deployment_service.canonical_envelope_hash"
        ) as mock_hash:
            mock_hash.return_value = "deadbeef"
            result = _compute_envelope_hash_sync("agent-x")

        mock_hash.assert_called_once_with(rows)
        assert result == "deadbeef"

    def test_compute_envelope_hash_sync_takes_no_conn_str(self):
        assert list(inspect.signature(_compute_envelope_hash_sync).parameters) == [
            "agent_id"
        ]

    def test_empty_envelope_rows_hash_is_deterministic(self):
        mock_db_1 = _make_envelope_mock_db([])
        with patch(
            "app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_1)
        ):
            result_1 = _compute_envelope_hash_sync("agent-empty")

        mock_db_2 = _make_envelope_mock_db([])
        with patch(
            "app.services.deployment_service.get_sync_db", _make_sync_db_ctx(mock_db_2)
        ):
            result_2 = _compute_envelope_hash_sync("agent-empty")

        assert result_1 is not None
        assert len(result_1) == 64
        assert result_1 == result_2


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


def _make_eval_conn(run_row, metric_rows=None, count_row=(0, 0), raise_on=None):
    """psycopg2 connection double for _fetch_eval_summary_sync.

    The function issues three statements — the latest run, the per-metric
    aggregate, then the denominator counts — and fetchone is called twice, so a
    single-value double cannot drive it.

    `run_row` is the four-column shape the collector selects since the P2
    review: (id, finished_at, status, config). A three-tuple is accepted and
    padded with a NULL config, which is the pre-0013 row the narrow fallback
    reads — the tests that pass one are asserting behaviour that does not
    depend on the run's own denominator.

    raise_on: substring of the SQL that should raise UndefinedColumn, which is
    how audit D3 presented itself in production (`metric_name` / `run_id`
    against a table whose columns are `metric` / `eval_run_id`).
    """
    if run_row is not None and len(run_row) == 3:
        run_row = (*run_row, None)
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)

    state = {"fetchone": [run_row, count_row], "fetchall": metric_rows or []}
    executed: list[str] = []

    def _execute(sql, params=None):
        executed.append(sql)
        if raise_on is not None and raise_on in sql:
            raise psycopg2.errors.UndefinedColumn(f"column does not exist: {raise_on}")

    def _fetchone():
        return state["fetchone"].pop(0) if state["fetchone"] else None

    cursor.execute.side_effect = _execute
    cursor.fetchone.side_effect = _fetchone
    cursor.fetchall.side_effect = lambda: state["fetchall"]
    conn.cursor.return_value = cursor
    conn.executed = executed
    return conn


class TestSignalCollectionFunctions:
    """Tests for _fetch_eval_summary_sync signal collection helper."""

    def test_fetch_eval_summary_sync_returns_correct_shape(self):
        """Mock psycopg2 returns two metric rows; verify result shape and values (DEP-01)."""
        run_id = uuid.uuid4()
        run_ts = datetime(2026, 5, 23, 2, 0, 0)

        mock_conn = _make_eval_conn(
            (run_id, run_ts, "complete"),
            metric_rows=[
                ("faithfulness", Decimal("0.92"), 30),
                ("answer_relevance", Decimal("0.88"), 30),
            ],
            count_row=(30, 30),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        assert result["pass_rates"]["faithfulness"] == pytest.approx(0.92)
        assert result["pass_rates"]["answer_relevance"] == pytest.approx(0.88)
        assert result["scenario_count"] == 30, "attempted"
        assert result["scored_scenario_count"] == 30, "valid"
        assert result["last_run_at"] == run_ts.isoformat()
        assert result["last_run_status"] == "complete"

    def test_fetch_eval_summary_sync_no_runs(self):
        """No eval run at all is 'no_runs' with a NULL pass_rates, not an empty dict."""
        mock_conn = _make_eval_conn(None)

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_NO_RUNS
        assert result["pass_rates"] is None, (
            "an empty dict reads as 'no metric is failing' to anything that "
            "iterates it — audit D3's fail-open"
        )
        assert result["failing_scenarios"] is None
        assert result["scenario_count"] == 0
        assert result["scored_scenario_count"] == 0


# ---------------------------------------------------------------------------
# TestEvalSummaryD3 — the repaired query and its distinguishable absence
# ---------------------------------------------------------------------------


class TestEvalSummaryD3:
    """Audit D3: the deploy gate's eval query could not execute.

    `SELECT metric_name, AVG(score) ... WHERE run_id = %s` against a table whose
    columns are `metric` and `eval_run_id` raised UndefinedColumn on every
    invocation. The Celery task caught it, substituted `pass_rates: {}`, and the
    blocking condition "any eval metric pass_rate < 0.70" then evaluated over an
    empty dict — which cannot fire. Both halves are tested here: the names, and
    the behaviour when the query fails anyway.
    """

    def test_the_query_uses_the_schema_column_names(self):
        """Read out of the SQL that actually ran, not out of the source text.

        A source-level assertion would pass against a second, unreached copy of
        the query; this one is about the statement the function issued.
        """
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete"),
            metric_rows=[("faithfulness", Decimal("0.92"), 30)],
            count_row=(30, 30),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        results_sql = [s for s in mock_conn.executed if "eval_results" in s]
        assert results_sql, "no statement was issued against eval_results"
        joined = " ".join(results_sql)
        assert "eval_run_id" in joined
        assert "GROUP BY metric" in joined
        assert "metric_name" not in joined, (
            "metric_name is audit D3's column — the schema (0001:165-174) says "
            "`metric`"
        )
        assert "run_id = " not in joined.replace("eval_run_id = ", ""), (
            "run_id is audit D3's column — the schema says `eval_run_id`"
        )

    def test_a_failing_query_is_unavailable_not_clean(self):
        """The exact D3 failure, reproduced: the query raises.

        The old behaviour returned `pass_rates: {}` from the caller and the gate
        read it as 'nothing is failing'. The repaired behaviour has to be
        DISTINGUISHABLE from a measured-and-clean run, or repairing the column
        names simply moves the fail-open one layer up.
        """
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete"),
            raise_on="eval_results",
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_UNAVAILABLE
        assert result["pass_rates"] is None
        assert result["failing_scenarios"] is None

        # And the gate refuses to ship on it — the half that makes the
        # distinguishable value worth having.
        recommendation, warnings = apply_signal_evidence_gate(
            "ship", result, _measured_red_team()
        )
        assert recommendation == "block"
        assert any(w.warning_id == "eval_signal_unavailable" for w in warnings)

    def test_a_run_that_scored_nothing_is_unknown_not_passing(self):
        """Every score NULL — a judge outage — is 'no_valid_scores'.

        Zero valid observations is unknown quality. Reporting it as an empty
        pass_rates dict would make a run that measured nothing satisfy "all
        eval metrics >= 0.85" vacuously.
        """
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete"),
            metric_rows=[("faithfulness", None, 0), ("answer_relevancy", None, 0)],
            count_row=(30, 0),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_NO_VALID_SCORES
        assert result["pass_rates"] is None
        assert apply_signal_evidence_gate("ship", result, _measured_red_team())[0] == (
            "block"
        )

    def test_the_run_is_selected_by_kind_so_a_sibling_agent_is_not_read(self):
        """`kind` is 'm6:{agent_id}'; without the filter a second agent in the
        same tenant DB has its run reported as this agent's."""
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete"),
            metric_rows=[("faithfulness", Decimal("0.92"), 30)],
            count_row=(30, 30),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            _fetch_eval_summary_sync("agent-42", "postgresql://test/tenant")

        runs_sql = [s for s in mock_conn.executed if "FROM eval_runs" in s]
        assert runs_sql and "kind = %s" in runs_sql[0]

    def test_a_failed_run_is_reported_as_failed(self):
        """Since the P1 persistence split a FAILED run lands a terminal status
        on production, so `last_run_at` alone can describe a run that produced
        nothing. The status travels with it."""
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "failed"),
            metric_rows=[],
            count_row=(0, 0),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["last_run_status"] == "failed"
        assert result["eval_signal"] == EVAL_SIGNAL_NO_VALID_SCORES


class TestEvalAttemptedCount:
    """The attempted count is the RUN's, not its results' (P2 review).

    `scenario_count` was COUNT(DISTINCT scenario_id) over eval_results — the
    scenarios the judge came BACK about. write_eval_results only ever writes a
    row per score the judge produced, so a scenario the judge dropped entirely
    leaves no trace there: attempted could not exceed scored except in the
    all-NULL case, and the orchestrator's instruction — "a pass rate over a
    handful of scored scenarios out of many attempted is a weak signal and you
    must say so" — compared two numbers derived from the same five rows.
    """

    def test_a_partial_judge_outage_is_visible_in_the_denominators(self):
        """The failing input, exactly as filed.

        A run fetches 40 valid scenarios; a Ragas/judge partial outage returns
        5, all scored 0.95. Under the old collector the gate saw
        scenario_count=5, scored=5, faithfulness=0.95 — a clean measurement of
        an agent whose other 35 scenarios were never scored at all.
        """
        run_config = {"dataset": {"attempted": 40, "valid": 40}}
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", run_config),
            metric_rows=[("faithfulness", Decimal("0.95"), 5)],
            count_row=(5, 5),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_MEASURED
        assert result["scenario_count"] == 40, (
            "attempted must come from the run's own record of what it covered"
        )
        assert result["valid_scenario_count"] == 40
        assert result["scored_scenario_count"] == 5
        assert result["denominator_source"] == DENOMINATOR_SOURCE_RUN_CONFIG
        assert result["scored_scenario_count"] < result["scenario_count"], (
            "the pair the orchestrator is told to compare must be able to differ"
        )

    def test_a_run_without_a_recorded_composition_labels_its_floor(self):
        """A pre-0013 run, or one inserted before the composition was stamped.

        The eval_results-derived count is still reported — it is better than
        nothing — but it is LABELLED, because its equality with the scored count
        is an artefact of where it came from rather than evidence of full
        coverage, and `valid` is None rather than a number nobody measured.
        """
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", None),
            metric_rows=[("faithfulness", Decimal("0.95"), 5)],
            count_row=(5, 5),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["denominator_source"] == DENOMINATOR_SOURCE_EVAL_RESULTS
        assert result["scenario_count"] == 5
        assert result["valid_scenario_count"] is None, (
            "how many fetched rows carried a label is unrecorded here — not zero, "
            "and not the scored count wearing another name"
        )

    def test_a_run_that_scored_nothing_still_reports_what_it_attempted(self):
        """40 attempted, every score NULL. 'No valid scores' and 'nothing was
        attempted' are different events and used to report identical zeros."""
        run_config = {"dataset": {"attempted": 40, "valid": 40}}
        mock_conn = _make_eval_conn(
            (uuid.uuid4(), datetime(2026, 5, 23, 2, 0, 0), "complete", run_config),
            metric_rows=[("faithfulness", None, 0)],
            count_row=(40, 0),
        )

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            return_value=mock_conn,
        ):
            result = _fetch_eval_summary_sync("test-agent", "postgresql://test/tenant")

        assert result["eval_signal"] == EVAL_SIGNAL_NO_VALID_SCORES
        assert result["scenario_count"] == 40
        assert result["scored_scenario_count"] == 0
        assert result["pass_rates"] is None

    def test_the_prompt_tells_the_orchestrator_where_the_denominator_came_from(self):
        """A labelled floor the reader cannot see the label of is just a number."""
        assert "denominator_source" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "valid_scenario_count" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "coverage_source" in _DEPLOYMENT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# TestEvidenceGate — 'ship' is not available over an absent signal
# ---------------------------------------------------------------------------


def _measured_eval(pass_rates=None) -> dict:
    return {
        "eval_signal": EVAL_SIGNAL_MEASURED,
        "signal_detail": None,
        "last_run_at": "2026-05-23T02:00:00",
        "last_run_status": "complete",
        "scenario_count": 30,
        "valid_scenario_count": 30,
        "scored_scenario_count": 30,
        "denominator_source": DENOMINATOR_SOURCE_RUN_CONFIG,
        "pass_rates": pass_rates or {"faithfulness": 0.92},
        "failing_scenarios": 0,
    }


def _measured_red_team(coverage_complete=True) -> dict:
    return {
        "signal": RED_TEAM_SIGNAL_MEASURED,
        "signal_detail": None,
        "last_run_at": "2026-05-23T03:00:00",
        "deployment_blocked": False,
        "critical_count": 0,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "vectors_attempted": 7,
        "vectors_valid": 7 if coverage_complete else 3,
        "invalid_vectors": [] if coverage_complete else ["hallucination"],
        "coverage_complete": coverage_complete,
        "coverage_source": COVERAGE_SOURCE_RUN,
    }


class TestEvidenceGate:
    """apply_signal_evidence_gate — deterministic, one-way, fail-closed.

    The gate exists because the blocking conditions live in an LLM prompt and a
    gate that depends on a model correctly reading a state field is a gate that
    fails open the first time the model is confident and wrong. Same division of
    labour as derive_blast_radius_warnings: the orchestrator narrates, the
    platform decides.
    """

    @pytest.mark.parametrize(
        ("signal", "warning_id"),
        [
            # 'never evaluated' and 'could not be read' block identically and
            # are reported differently: the remedies are different, and telling
            # a day-1 owner their results "could not be read" describes a
            # transient outage where the truth is a permanent absence.
            (EVAL_SIGNAL_NO_RUNS, "eval_never_run"),
            (EVAL_SIGNAL_NO_VALID_SCORES, "eval_signal_unavailable"),
            (EVAL_SIGNAL_UNAVAILABLE, "eval_signal_unavailable"),
        ],
    )
    def test_ship_is_refused_over_any_absent_eval_signal(self, signal, warning_id):
        summary = _measured_eval()
        summary["eval_signal"] = signal
        summary["pass_rates"] = None

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", summary, _measured_red_team()
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == [warning_id]

    def test_ship_with_warnings_is_also_refused(self):
        """ship_with_warnings is a SHIPPABLE state — the approve route lets it
        through once the owner acknowledges — so routing an unmeasured agent
        there would still permit the deploy."""
        summary = _measured_eval()
        summary["eval_signal"] = EVAL_SIGNAL_UNAVAILABLE

        recommendation, _ = apply_signal_evidence_gate(
            "ship_with_warnings", summary, _measured_red_team()
        )
        assert recommendation == "block"

    def test_a_missing_state_field_fails_closed(self):
        """A summary dict built by hand without the state key must not ship.

        The absence of a claim is not the claim. This is the shape of every
        future caller that constructs a signal payload and forgets a field.
        """
        recommendation, warnings = apply_signal_evidence_gate(
            "ship", {"pass_rates": {"faithfulness": 0.99}}, {"critical_count": 0}
        )
        assert recommendation == "block"
        assert {w.warning_id for w in warnings} == {
            "eval_signal_unavailable",
            "red_team_signal_unavailable",
        }

    def test_a_measured_signal_leaves_the_recommendation_alone(self):
        """The gate is a floor, not a second opinion. With both signals
        measured, the orchestrator's verdict is untouched — including a verdict
        the gate would never itself produce."""
        for verdict in ("ship", "ship_with_warnings", "block"):
            recommendation, warnings = apply_signal_evidence_gate(
                verdict, _measured_eval(), _measured_red_team()
            )
            assert recommendation == verdict
            assert warnings == []

    def test_the_gate_never_upgrades_a_block(self):
        """One-way. A block over a missing signal stays a block, and so does a
        block the orchestrator reached on evidence."""
        summary = _measured_eval()
        summary["eval_signal"] = EVAL_SIGNAL_UNAVAILABLE
        assert apply_signal_evidence_gate("block", summary, _measured_red_team())[0] == (
            "block"
        )
        assert apply_signal_evidence_gate(
            "block", _measured_eval(), _measured_red_team()
        )[0] == "block"

    def test_an_unreadable_red_team_signal_also_refuses_to_ship(self):
        """Zeros nobody read are not zeros. The substituted red-team fallback
        carries deployment_blocked=False, which on its own reads as 'no critical
        findings' — the identical fail-open shape D3 had on the eval side."""
        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), dict(RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL)
        )
        assert recommendation == "block"
        assert any(w.warning_id == "red_team_signal_unavailable" for w in warnings)

    def test_a_run_that_recorded_incomplete_coverage_refuses_to_ship(self):
        """P4 review. This used to warn and ship, and the warning could not fire.

        `red_team_coverage_incomplete` was the only deterministic Python-side
        coverage control in the system, and it was fed by red_team_coverage(),
        which has returned complete=True for every run in every environment
        since SDK_ATTACKERS_CAN_PROBE was flipped. Now the run records its own
        coverage — and a clean result over 3 of 7 vectors is not a clean result.
        The remedy is in the owner's hands (run the check again), which is what
        makes refusing actionable rather than a dead end.
        """
        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), _measured_red_team(coverage_complete=False)
        )
        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_coverage_incomplete"]
        assert "3 of 7" in warnings[0].message

    def test_unrecorded_coverage_warns_rather_than_blocking(self):
        """'current_build' means no run-level figure exists at all — a tenant DB
        provisioned before migration 0015, or a run written before the task
        stored it. Nothing the owner does produces one, so blocking would be a
        permanent unfixable refusal. It is still not evidence, and it says so."""
        summary = _measured_red_team()
        summary["coverage_source"] = COVERAGE_SOURCE_CURRENT_BUILD

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "ship"
        assert [w.warning_id for w in warnings] == ["red_team_coverage_unrecorded"]

    def test_a_null_coverage_flag_fails_the_same_way_as_false(self):
        """`is not True`, not `is False`: a summary that carries no coverage
        claim has not made one."""
        summary = _measured_red_team()
        summary["coverage_complete"] = None

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_coverage_incomplete"]

    def test_an_open_critical_finding_refuses_to_ship(self):
        """`red_team_summary.deployment_blocked == True` was the first blocking
        condition in the orchestrator prompt and was enforced in no Python at
        all — the gate returned 'ship' over an open critical finding."""
        summary = _measured_red_team()
        summary["critical_count"] = 1
        summary["deployment_blocked"] = True

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_critical_finding"]

    def test_open_high_findings_refuse_to_ship_while_the_flag_is_set(self):
        """DEP_BLOCK_ON_HIGH_RED_TEAM occurred exactly twice in the codebase:
        its definition in config.py and one sentence of a system prompt. The
        four `high` INVALID findings a transport-less run produces went straight
        past it."""
        summary = _measured_red_team()
        summary["high_count"] = 4

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_high_finding"]
        assert "4 serious" in warnings[0].message

    def test_the_high_block_honours_its_flag(self):
        """The flag is a real switch, not decoration — otherwise this gate would
        be enforcing something the config says is optional."""
        summary = _measured_red_team()
        summary["high_count"] = 4

        with patch.object(settings, "DEP_BLOCK_ON_HIGH_RED_TEAM", False):
            recommendation, warnings = apply_signal_evidence_gate(
                "ship", _measured_eval(), summary
            )

        assert recommendation == "ship"
        assert warnings == []

    def test_containing_the_findings_does_not_clear_the_coverage_refusal(self):
        """The scenario the P4 review reconstructed end to end.

        Four `high` findings whose console fields name no vulnerability lead the
        owner to contain them (PATCH /red-team/findings/{id}, open -> contained).
        The counts then read 0/0 — but containment does not make the four
        vectors run, and the run's own coverage row still says they did not.
        """
        summary = _measured_red_team(coverage_complete=False)
        summary["critical_count"] = 0
        summary["high_count"] = 0

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), summary
        )

        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_coverage_incomplete"]

    def test_the_unavailable_substitute_cannot_be_mistaken_for_a_clean_run(self):
        """The module constant itself, not a hand-built dict: this is the value
        the Celery task substitutes when the collector raises."""
        assert EVAL_SUMMARY_UNAVAILABLE_SIGNAL["pass_rates"] is None
        assert EVAL_SUMMARY_UNAVAILABLE_SIGNAL["failing_scenarios"] is None
        assert (
            apply_signal_evidence_gate(
                "ship", dict(EVAL_SUMMARY_UNAVAILABLE_SIGNAL), _measured_red_team()
            )[0]
            == "block"
        )

    def test_the_prompt_states_the_evidence_rule_it_no_longer_enforces(self):
        """The prompt still has to SAY it, or the model's summary contradicts
        the recommendation the platform imposed."""
        assert "eval_signal" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "'measured'" in _DEPLOYMENT_SYSTEM_PROMPT
        assert "scored_scenario_count" in _DEPLOYMENT_SYSTEM_PROMPT


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


# ---------------------------------------------------------------------------
# TestRedTeamSummarySignal (P2) — the security half carries its own state
# ---------------------------------------------------------------------------


class TestRedTeamSummarySignal:
    """The collector's payload must say it was READ, and how much it covers."""

    def _fetch(self, run_row=(datetime(2026, 5, 23, 3, 0, 0), None), raise_on=None):
        """psycopg2 double for _fetch_red_team_summary_sync.

        `run_row` is (started_at, coverage) since migration 0015 — the run's own
        record of how much of the attack surface it covered. None for coverage
        is a run written before 0015.
        """
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)

        def _execute(sql, params=None):
            if raise_on is not None and raise_on in sql:
                raise psycopg2.errors.UndefinedColumn(f"no column: {raise_on}")

        cursor.execute.side_effect = _execute
        # The pre-0015 fallback selects started_at alone, so the double answers
        # with the narrower row once the wide SELECT has raised.
        cursor.fetchone.side_effect = lambda: (
            None
            if run_row is None
            else (run_row[:1] if raise_on == "coverage" else run_row)
        )
        cursor.fetchall.return_value = [("medium", 2)]
        conn.cursor.return_value = cursor

        with patch(
            "app.services.deployment_service.psycopg2.connect", return_value=conn
        ):
            return _fetch_red_team_summary_sync("test-agent", "postgresql://test/t")

    def test_a_read_signal_is_marked_measured(self):
        result = self._fetch()
        assert result["signal"] == RED_TEAM_SIGNAL_MEASURED
        assert result["medium_count"] == 2
        assert result["deployment_blocked"] is False

    def test_the_coverage_denominator_travels_with_the_counts(self):
        """Zero open findings is not a result on its own.

        The same row set means "seven vectors probed and none succeeded" or
        "three probed and four could not" (audit D4), and only the denominator
        separates them.
        """
        from app.services.red_team_service import red_team_coverage

        result = self._fetch()
        coverage = red_team_coverage()

        assert result["vectors_attempted"] == coverage["vectors_attempted"]
        assert result["vectors_valid"] == coverage["vectors_valid"]
        assert result["coverage_complete"] is coverage["complete"]
        assert result["invalid_vectors"] == coverage["invalid_vectors"]
        assert result["coverage_source"] == COVERAGE_SOURCE_CURRENT_BUILD, (
            "a run that recorded no coverage must say the figures describe "
            "today's build, not the run"
        )

    def test_an_agent_that_was_never_attacked_is_not_measured(self):
        """THE DAY-1 LIE (P2 review).

        The failing input: a brand-new agent. red_team_runs is empty and
        red_team_findings is empty, so every count is zero — which is also what
        a genuinely clean run produces. The collector logged
        `red_team_summary.no_runs` and then returned signal='measured' anyway,
        so apply_signal_evidence_gate (which refuses only a signal that is not
        'measured') let `ship` through, and the platform asserted the security
        surface had been measured on the one day it certainly had not.
        """
        result = self._fetch(run_row=None)

        assert result["signal"] == RED_TEAM_SIGNAL_NO_RUNS
        assert result["last_run_at"] is None
        for key in ("critical_count", "high_count", "medium_count", "low_count"):
            assert result[key] is None, (
                f"{key} is a count of findings from zero runs — a zero nobody "
                "measured is not a zero"
            )
        assert result["vectors_attempted"] is None
        assert result["coverage_source"] is None

        recommendation, warnings = apply_signal_evidence_gate(
            "ship", _measured_eval(), result
        )
        assert recommendation == "block"
        assert [w.warning_id for w in warnings] == ["red_team_never_run"]

    def test_a_run_reports_the_coverage_it_had_not_the_readers(self):
        """Stored coverage wins, and says so.

        The failing input is time-shifted: P4 flips SDK_ATTACKERS_CAN_PROBE and
        every stored three-of-seven run is suddenly described to the deploy gate
        as seven-of-seven, because red_team_coverage() only ever describes the
        code that is running now. A run that recorded its own numbers must be
        read back with those numbers.
        """
        stored = {
            "vectors_attempted": 7,
            "vectors_valid": 3,
            "invalid_vectors": ["hallucination"],
            "complete": False,
        }
        result = self._fetch(run_row=(datetime(2026, 5, 23, 3, 0, 0), stored))

        assert result["vectors_valid"] == 3
        assert result["invalid_vectors"] == ["hallucination"]
        assert result["coverage_complete"] is False
        assert result["coverage_source"] == COVERAGE_SOURCE_RUN

    def test_a_pre_0015_tenant_degrades_to_the_current_build_and_labels_it(self):
        """UndefinedColumn on `coverage` costs the run's own figures, nothing
        else — and the substitution is named rather than silent."""
        result = self._fetch(raise_on="coverage")

        assert result["signal"] == RED_TEAM_SIGNAL_MEASURED
        assert result["coverage_source"] == COVERAGE_SOURCE_CURRENT_BUILD
        assert result["vectors_attempted"] is not None

    def test_a_malformed_stored_coverage_is_absent_not_partial(self):
        """A payload missing a key is not a coverage claim.

        Half a denominator would be worse than none: `vectors_valid` without
        `vectors_attempted` is a numerator wearing a denominator's name.
        """
        result = self._fetch(
            run_row=(datetime(2026, 5, 23, 3, 0, 0), {"vectors_valid": 3})
        )

        assert result["coverage_source"] == COVERAGE_SOURCE_CURRENT_BUILD

    def test_the_unavailable_substitute_is_not_a_clean_run(self):
        """deployment_blocked=False in the fallback is 'we could not ask', and
        the signal field is what stops it reading as 'no critical findings'."""
        assert RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL["deployment_blocked"] is False
        assert RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL["critical_count"] is None
        assert (
            apply_signal_evidence_gate(
                "ship", _measured_eval(), dict(RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL)
            )[0]
            == "block"
        )
