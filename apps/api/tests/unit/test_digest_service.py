"""Unit tests for app.services.digest_service — M10 weekly digest (OPS-02).

De-xfailed in Phase 10-05. Tests cover:
    test_collect_digest_stats_shape     — _collect_digest_stats returns expected keys
    test_send_digest_email_calls_smtp   — send_digest_email calls smtplib.SMTP when configured
    test_digest_beat_skips_when_disabled — run_weekly_digest_beat returns {dispatched:0} when DIGEST_ENABLED=False
    test_digest_idempotency_within_7d   — run_weekly_digest skips if digest_runs row exists within 7 days

TestDigestFaithfulnessReadsTheRecord    — #51 slice 4: the number is lifted off
    `eval_runs.result` through `run_level_metrics`, the digest names the dataset
    it quotes, and a run without a record prints "not measured".
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


def test_collect_digest_stats_shape():
    """_collect_digest_stats returns dict with required keys."""
    from app.services.digest_service import _collect_digest_stats

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None

    with patch("app.services.digest_service.psycopg2.connect") as mock_connect:
        mock_connect.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        stats = _collect_digest_stats(agent_id="fake-id", conn_str="fake", db=mock_db)

    assert "conversation_count" in stats and "faithfulness_score" in stats
    assert set(stats.keys()) >= {
        "conversation_count",
        "faithfulness_score",
        "critical_red_team_count",
        "escalation_count",
    }


def test_send_digest_email_calls_smtp(monkeypatch):
    """send_digest_email calls smtplib.SMTP when SMTP is configured."""
    from app.core.config import settings
    from app.services.digest_service import send_digest_email

    monkeypatch.setattr(settings, "SMTP_HOST", "localhost")
    monkeypatch.setattr(settings, "SMTP_FROM", "test@test.com")
    monkeypatch.setattr(settings, "OWNER_EMAIL", "owner@test.com")
    monkeypatch.setattr(settings, "DIGEST_ENABLED", True)
    monkeypatch.setattr(settings, "SMTP_PORT", 587)

    mock_smtp_cls = MagicMock()
    mock_smtp_instance = MagicMock()
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.services.digest_service.smtplib.SMTP", mock_smtp_cls):
        send_digest_email(
            agent_name="Test Agent",
            agent_id="agent-123",
            stats={
                "conversation_count": 5,
                "faithfulness_score": 0.85,
                "critical_red_team_count": 0,
                "escalation_count": 1,
            },
        )

    mock_smtp_instance.sendmail.assert_called_once()


def test_digest_beat_skips_when_disabled():
    """run_weekly_digest_beat returns {dispatched: 0} when DIGEST_ENABLED=False."""
    from app.core.config import settings
    from app.worker.tasks.runtime.digest import run_weekly_digest_beat

    with patch.object(settings, "DIGEST_ENABLED", False):
        result = run_weekly_digest_beat.run()

    assert result == {"dispatched": 0}


def test_digest_idempotency_within_7d():
    """run_weekly_digest skips if a digest_runs row exists for this agent within 7 days."""
    from uuid import uuid4

    from app.worker.tasks.runtime.digest import run_weekly_digest

    agent_id = str(uuid4())
    mock_db = MagicMock()
    # Simulate a recent digest_runs row existing (fetchone returns a row — skip condition)
    mock_db.execute.return_value.fetchone.return_value = MagicMock()

    with patch("app.worker.tasks.runtime.digest.get_sync_db", _make_sync_db_ctx(mock_db)):
        with patch("app.services.digest_service.send_digest_email") as mock_send:
            result = run_weekly_digest.run(agent_id=agent_id)
            mock_send.assert_not_called()

    assert result == {"status": "already_sent"}


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


class TestDigestFaithfulnessReadsTheRecord:
    """The digest reads the run's own record. No AVG anywhere in this module."""

    def _stats(self, row):
        from app.services.digest_service import _collect_digest_stats

        conn = _run_row_conn(row)
        conn.cursor.return_value.fetchone.side_effect = [row, None, (0,), (0,)]
        with patch("app.services.digest_service.psycopg2.connect", return_value=conn):
            stats = _collect_digest_stats(
                agent_id="agent-1", conn_str="postgresql://test/tenant", db=MagicMock()
            )
        return stats, conn

    def test_one_scoring_dataset_gives_its_number_and_names_it(self):
        from uuid import uuid4

        record = _eval_record({"exploratory": _scored(0.77)})
        stats, conn = self._stats((str(uuid4()), record.payload))

        assert stats["faithfulness_score"] == 0.77
        assert stats["faithfulness_dataset"] == "exploratory"

        sql = " ".join(
            c.args[0] for c in conn.cursor.return_value.execute.call_args_list
        )
        assert "AVG(" not in sql, "the digest is averaging eval_results again"

    def test_a_run_without_a_record_reads_as_unmeasured(self):
        """Not zero, and not last week's number. The email says so."""
        from uuid import uuid4

        from app.services.digest_service import send_digest_email

        stats, _ = self._stats((str(uuid4()), None))
        assert stats["faithfulness_score"] is None
        assert stats["faithfulness_dataset"] is None

        sent = {}
        smtp = MagicMock()
        instance = MagicMock()
        smtp.return_value.__enter__ = MagicMock(return_value=instance)
        smtp.return_value.__exit__ = MagicMock(return_value=False)
        instance.sendmail.side_effect = lambda _f, _t, body: sent.update(body=body)

        with patch("app.services.digest_service.smtplib.SMTP", smtp), patch.object(
            __import__("app.core.config", fromlist=["settings"]).settings,
            "SMTP_HOST",
            "localhost",
        ), patch.object(
            __import__("app.core.config", fromlist=["settings"]).settings,
            "SMTP_FROM",
            "a@b.c",
        ), patch.object(
            __import__("app.core.config", fromlist=["settings"]).settings,
            "OWNER_EMAIL",
            "o@b.c",
        ), patch.object(
            __import__("app.core.config", fromlist=["settings"]).settings,
            "DIGEST_ENABLED",
            True,
        ):
            send_digest_email("Test", "agent-1", stats)

        import email as _email

        body = (
            _email.message_from_string(sent["body"])
            .get_payload(decode=True)
            .decode()
        )
        assert "not measured" in body, (
            "an unmeasured faithfulness printed as a number, or as nothing at "
            "all, is a reading the run never produced"
        )
