"""Unit tests for app.services.digest_service — M10 weekly digest (OPS-02).

De-xfailed in Phase 10-05. Tests cover:
    test_collect_digest_stats_shape     — _collect_digest_stats returns expected keys
    test_send_digest_email_calls_smtp   — send_digest_email calls smtplib.SMTP when configured
    test_digest_beat_skips_when_disabled — run_weekly_digest_beat returns {dispatched:0} when DIGEST_ENABLED=False
    test_digest_idempotency_within_7d   — run_weekly_digest skips if digest_runs row exists within 7 days
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

import pytest
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
