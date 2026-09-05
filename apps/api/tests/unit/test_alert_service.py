"""Unit tests for app.services.alert_service — M10 threshold alerts (OPS-04).

De-xfailed in Phase 10-05. Tests cover:
    test_eval_regression_triggers_alert     — faithfulness below threshold writes eval_regression alert
    test_red_team_critical_triggers_alert   — critical findings >= threshold writes red_team_critical alert
    test_no_alert_when_thresholds_met       — metrics above threshold -> no new alert written

TestLatestFaithfulnessReadsTheRecord.    #51 slice 4: the numbers are lifted off
    `eval_runs.result`, never averaged here, and a run without a record reads as
    unmeasured rather than falling back to an older run that has a number.
    #175: they are read PER DATASET, so a run whose two datasets both scored has
    two readings instead of none.

TestEvalRegressionMessage.               #175: each half is compared to the
    threshold on its own and the message names the half that fell.

TestTwoDatasetRunReachesTheAlert.        #175: the whole path, from a per-dataset
    reading to a written alert row.
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
        faithfulness_by_dataset={"exploratory": 0.4},  # below default 0.6 -> eval_regression
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
        faithfulness_by_dataset={"exploratory": 0.95},  # above threshold -> no eval_regression
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
        faithfulness_by_dataset={"exploratory": 0.95},  # above threshold
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
    """One dataset that measured faithfulness, or one that measured nothing.

    Every scored scenario is UNMEASURED, because the only dimension this fixture
    reports is faithfulness and a scenario needs both gated verdicts to pass or
    fail. The three verdict counts have to add up to `scored` either way.
    """
    from app.domain.eval_result import DatasetOutcome, Measurement

    metrics = {}
    if faithfulness is not None:
        metrics["faithfulness"] = Measurement(
            value=faithfulness, observations=scored, measured=True
        )
    return DatasetOutcome(
        attempted=attempted,
        valid=valid,
        scored=scored,
        metrics=metrics,
        scenarios_unmeasured=scored,
    )


def _run_row_conn(row, status="complete"):
    """psycopg2 connection double answering the latest-FINISHED-run SELECT.

    The row is (id, result, status). A caller handing over the first two gets a
    complete run, which is what a test about a record wants; a test about a run
    that did not complete says so with `status`.
    """
    if row is not None and len(row) == 2:
        row = (*row, status)
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

    def _read(self, row, status="complete"):
        from app.services.alert_service import latest_faithfulness_readings

        with patch(
            "app.services.alert_service.psycopg2.connect",
            return_value=_run_row_conn(row, status),
        ):
            return latest_faithfulness_readings("agent-1", "postgresql://test/tenant")

    def test_one_scoring_dataset_gives_its_number_and_names_it(self):
        """The one-dataset rule, which is the ordinary tenant's shape."""
        from uuid import uuid4

        record = _eval_record({"exploratory": _scored(0.42)})

        assert self._read((str(uuid4()), record.payload)) == {"exploratory": 0.42}, (
            "a faithfulness number nobody attributed is one a reader will "
            "attribute to the wrong half of the run"
        )

    def test_a_run_without_a_record_is_unmeasured_not_an_older_number(self):
        """The recordless run. Reading a missing measurement as the last one
        that existed is reading missing data as passing data, and it would fire
        or suppress an alert on a run that never reported."""
        from uuid import uuid4

        assert self._read((str(uuid4()), None)) == {}

    def test_a_failed_latest_run_is_unmeasured_and_not_an_older_number(self):
        """The newest finished run failed, and it carries a full record.

        The query said `status = 'complete'`, so it reached back PAST this run to
        an older one that completed and reported that older number as the agent's
        current faithfulness. A run that did not reach the end of its own body
        has no reading, and last week's reading is not this morning's.
        """
        from uuid import uuid4

        record = _eval_record({"exploratory": _scored(0.42)})
        assert self._read((str(uuid4()), record.payload), status="failed") == {}

    def test_the_latest_run_query_excludes_only_the_ones_in_flight(self):
        """The same predicate the deploy collector selects on.

        Two readers of "the latest run" that disagree about which run that is
        will disagree about the agent, and only one of them will be looking at
        the run the owner just watched fail.
        """
        from uuid import uuid4

        from app.services.alert_service import latest_faithfulness_readings

        conn = _run_row_conn((str(uuid4()), None))
        with patch("app.services.alert_service.psycopg2.connect", return_value=conn):
            latest_faithfulness_readings("agent-1", "postgresql://test/tenant")

        sql = " ".join(c.args[0] for c in conn.cursor.return_value.execute.call_args_list)
        assert "status <> 'running'" in sql
        assert "status = 'complete'" not in sql, (
            "filtering to complete runs in SQL reaches back past a failed run "
            "to an older one and reports its number as the current reading"
        )

    def test_the_query_selects_the_record_and_aggregates_nothing(self):
        """Read out of the SQL that ran. `AVG(er.score)` was the fourth
        derivation of a number the run had already computed."""
        from uuid import uuid4

        from app.services.alert_service import latest_faithfulness_readings

        conn = _run_row_conn((str(uuid4()), None))
        with patch("app.services.alert_service.psycopg2.connect", return_value=conn):
            latest_faithfulness_readings("agent-1", "postgresql://test/tenant")

        sql = " ".join(c.args[0] for c in conn.cursor.return_value.execute.call_args_list)
        assert "AVG(" not in sql, "the alert is averaging eval_results again"
        assert "eval_results" not in sql
        assert "result, status FROM eval_runs" in sql

    def test_a_two_dataset_run_reports_both_halves_separately(self):
        """#175. Refusing to pool is right; dropping the reading was not.

        The previous rule asked `run_level_metrics` for one number, which a run
        whose two datasets both scored does not have, and returned (None, None).
        So the alert went silent for exactly the tenants who curated a golden
        set: an exploratory sample at 0.31 raised nothing. Both halves come back
        under their own names now, and neither is an average of the other.
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
        readings = self._read((str(uuid4()), record.payload))

        assert readings == {"golden": 0.95, "exploratory": 0.31}
        assert 0.63 not in readings.values(), (
            "0.63 is the mean of the two halves. A pooled figure moves whenever "
            "the exploratory draw moves while looking like a quality change"
        )

    def test_a_run_that_scored_no_faithfulness_reads_as_nothing(self):
        """An empty mapping is the absence, and the caller must not read it as a pass."""
        from uuid import uuid4

        record = _eval_record({"exploratory": _scored(None)})

        assert self._read((str(uuid4()), record.payload)) == {}


class TestEvalRegressionMessage:
    """Which datasets fell, said in the message rather than pooled away (#175)."""

    def test_no_reading_raises_nothing(self):
        from app.services.alert_service import eval_regression_message

        assert eval_regression_message({}) is None, (
            "an alert that could not read a measurement must not fire, and must "
            "not report a pass either"
        )

    def test_every_half_above_the_threshold_raises_nothing(self):
        from app.services.alert_service import eval_regression_message

        assert eval_regression_message({"golden": 0.91, "exploratory": 0.88}) is None

    def test_one_half_below_the_threshold_fires_and_names_that_half(self):
        """The shape the old rule was silent on: a golden regression beside a
        healthy exploratory draw. Pooling 0.42 and 0.88 gives 0.65, above the
        0.6 default, so an averaging alert would have stayed silent too."""
        from app.services.alert_service import eval_regression_message

        msg = eval_regression_message({"golden": 0.42, "exploratory": 0.88})

        assert msg is not None
        assert "0.42" in msg
        assert "golden set" in msg
        assert "exploratory" not in msg, (
            "a half that held is not part of a regression message about the half "
            "that did not"
        )
        assert "0.65" not in msg

    def test_both_halves_below_the_threshold_name_both_numbers(self):
        from app.services.alert_service import eval_regression_message

        msg = eval_regression_message({"golden": 0.42, "exploratory": 0.31})

        assert msg is not None
        assert "0.42" in msg and "golden set" in msg
        assert "0.31" in msg and "exploratory sample" in msg


class TestTwoDatasetRunReachesTheAlert:
    """The whole path, from a per-dataset reading to a written alert row."""

    def _run(self, readings):
        from uuid import uuid4

        from app.services.alert_service import check_and_write_alerts

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None
        check_and_write_alerts(
            agent_id=str(uuid4()),
            agent_name="Test",
            faithfulness_by_dataset=readings,
            critical_red_team_count=0,
            db=mock_db,
        )
        return mock_db

    def test_a_golden_regression_writes_one_alert_row(self):
        """One row per alert type, whichever halves fell. `_active_alert_exists`
        dedupes by type, so two failing datasets are one open eval_regression."""
        mock_db = self._run({"golden": 0.42, "exploratory": 0.31})

        mock_db.add.assert_called_once()
        written = mock_db.add.call_args[0][0]
        assert written.alert_type == "eval_regression"
        assert "golden set" in written.message
        assert "exploratory sample" in written.message

    def test_a_healthy_golden_set_beside_a_failing_sample_still_alerts(self):
        mock_db = self._run({"golden": 0.95, "exploratory": 0.31})

        mock_db.add.assert_called_once()
        assert "0.31" in mock_db.add.call_args[0][0].message

    def test_an_unreadable_run_writes_nothing(self):
        assert not self._run({}).add.called
