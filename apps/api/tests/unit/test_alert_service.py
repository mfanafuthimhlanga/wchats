"""Unit tests for app.services.alert_service — M10 threshold alerts (OPS-04).

De-xfailed in Phase 10-05. Tests cover:
    test_eval_regression_triggers_alert     — faithfulness below threshold writes eval_regression alert
    test_red_team_critical_triggers_alert   — critical findings >= threshold writes red_team_critical alert
    test_no_alert_when_thresholds_met       — metrics above threshold -> no new alert written

TestLatestFaithfulnessReadsTheRecord     — #51 slice 4: the number is lifted off
    `eval_runs.result` through `run_level_metrics`, never averaged here, and a
    run without a record reads as unmeasured rather than falling back to an
    older run that has a number.
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

from unittest.mock import MagicMock, patch


def test_eval_regression_triggers_alert():
    """Faithfulness below ALERT_FAITHFULNESS_THRESHOLD writes an eval_regression alert row."""
    from uuid import uuid4

    from app.services.alert_service import check_and_write_alerts

    agent_id = str(uuid4())
    mock_db = MagicMock()
    # _active_alert_exists uses db.execute(...).fetchone() — None = no existing active alert
    mock_db.execute.return_value.fetchone.return_value = None

    check_and_write_alerts(
        agent_id=agent_id,
        agent_name="Test",
        faithfulness=0.4,           # below default threshold 0.6 -> triggers eval_regression
        critical_red_team_count=0,
        db=mock_db,
    )

    mock_db.add.assert_called_once()
    assert mock_db.add.call_args[0][0].alert_type == "eval_regression"


def test_red_team_critical_triggers_alert():
    """Critical red_team findings >= ALERT_RED_TEAM_CRITICAL_COUNT writes a red_team_critical alert."""
    from uuid import uuid4

    from app.services.alert_service import check_and_write_alerts

    agent_id = str(uuid4())
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None

    check_and_write_alerts(
        agent_id=agent_id,
        agent_name="Test",
        faithfulness=0.95,          # above threshold -> no eval_regression
        critical_red_team_count=2,  # >= 1 -> triggers red_team_critical
        db=mock_db,
    )

    mock_db.add.assert_called_once()
    assert mock_db.add.call_args[0][0].alert_type == "red_team_critical"


def test_no_alert_when_thresholds_met():
    """Metrics above all thresholds — no alert row written."""
    from uuid import uuid4

    from app.services.alert_service import check_and_write_alerts

    agent_id = str(uuid4())
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None

    check_and_write_alerts(
        agent_id=agent_id,
        agent_name="Test",
        faithfulness=0.95,          # above threshold
        critical_red_team_count=0,  # below threshold
        db=mock_db,
    )

    assert not mock_db.add.called


# ---------------------------------------------------------------------------
# The record the two readers lift "latest faithfulness" off (#51 slice 4)
# ---------------------------------------------------------------------------


def _eval_record(datasets, *, attempted=30, valid=30, scored=30):
    """An EvalResult whose datasets the caller chooses."""
    from uuid import uuid4

    from app.domain.eval_result import Cost, EvalResult, Invocation

    return EvalResult(
        run_id=str(uuid4()),
        agent_id=str(uuid4()),
        invocation=Invocation(
            status="measured",
            valid=valid,
            attempted=valid,
            responded=scored,
            scorable=scored,
            failed=valid - scored,
            empty=0,
        ),
        datasets=datasets,
        requested_model="gpt-5.6-luna",
        cost=Cost(input_tokens=10, output_tokens=5, usd=0.01, zar=0.2, measured=True),
    )


def _scored(faithfulness, *, attempted=30, valid=30, scored=30):
    """One dataset that measured faithfulness, or one that measured nothing."""
    from app.domain.eval_result import DatasetOutcome, Measurement

    metrics = {}
    if faithfulness is not None:
        metrics["faithfulness"] = Measurement(
            value=faithfulness, observations=scored, measured=True
        )
    return DatasetOutcome(
        attempted=attempted, valid=valid, scored=scored, metrics=metrics
    )


def _run_row_conn(row):
    """psycopg2 connection double answering the latest-complete-run SELECT."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.fetchone.return_value = row
    cursor.fetchall.return_value = []
    conn.cursor.return_value = cursor
    return conn


class TestLatestFaithfulnessReadsTheRecord:
    """The alert reads the run's own record. No AVG anywhere in this module."""

    def _read(self, row):
        from app.services.alert_service import latest_faithfulness_reading

        with patch(
            "app.services.alert_service.psycopg2.connect",
            return_value=_run_row_conn(row),
        ):
            return latest_faithfulness_reading("agent-1", "postgresql://test/tenant")

    def test_one_scoring_dataset_gives_its_number_and_names_it(self):
        """The one-dataset rule, which is the ordinary tenant's shape."""
        from uuid import uuid4

        record = _eval_record({"exploratory": _scored(0.42)})
        value, dataset = self._read((str(uuid4()), record.payload))

        assert value == 0.42
        assert dataset == "exploratory", (
            "a faithfulness number nobody attributed is one a reader will "
            "attribute to the wrong half of the run"
        )

    def test_a_run_without_a_record_is_unmeasured_not_an_older_number(self):
        """The recordless run. Reading a missing measurement as the last one
        that existed is reading missing data as passing data, and it would fire
        or suppress an alert on a run that never reported."""
        from uuid import uuid4

        assert self._read((str(uuid4()), None)) == (None, None)

    def test_the_query_selects_the_record_and_aggregates_nothing(self):
        """Read out of the SQL that ran. `AVG(er.score)` was the fourth
        derivation of a number the run had already computed."""
        from uuid import uuid4

        from app.services.alert_service import latest_faithfulness_reading

        conn = _run_row_conn((str(uuid4()), None))
        with patch("app.services.alert_service.psycopg2.connect", return_value=conn):
            latest_faithfulness_reading("agent-1", "postgresql://test/tenant")

        sql = " ".join(c.args[0] for c in conn.cursor.return_value.execute.call_args_list)
        assert "AVG(" not in sql, "the alert is averaging eval_results again"
        assert "eval_results" not in sql
        assert "result FROM eval_runs" in sql

    def test_a_two_dataset_run_has_no_number_to_alert_on(self):
        """The cost of refusing to pool, stated where it lands.

        A tenant with a designated golden set has two faithfulness measurements
        and no honest way to combine them, so the alert does not fire. Averaging
        them would produce a figure that moves whenever the exploratory draw
        moves, and an alert that fires on the draw is worse than one that does
        not fire.
        """
        from uuid import uuid4

        record = _eval_record(
            {
                "golden": _scored(0.95, attempted=12, valid=12, scored=12),
                "exploratory": _scored(0.31),
            },
            attempted=42,
            valid=42,
            scored=42,
        )
        assert self._read((str(uuid4()), record.payload)) == (None, None)
