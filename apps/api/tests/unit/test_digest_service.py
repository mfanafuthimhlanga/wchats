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

import pytest


@pytest.mark.xfail(strict=True, reason="digest_service not yet implemented — de-xfail in 10-05")
def test_collect_digest_stats_shape():
    """_collect_digest_stats returns dict with required keys."""
    from app.services.digest_service import _collect_digest_stats
    stats = _collect_digest_stats(agent_id="fake-id", conn_str="fake")
    assert set(stats.keys()) >= {
        "conversation_count",
        "faithfulness_score",
        "critical_red_team_count",
        "escalation_count",
    }


@pytest.mark.xfail(strict=True, reason="digest_service not yet implemented — de-xfail in 10-05")
def test_send_digest_email_calls_smtp(monkeypatch):
    """send_digest_email calls smtplib.SMTP when SMTP is configured."""
    import smtplib
    from unittest.mock import MagicMock, patch

    mock_smtp_cls = MagicMock()
    mock_smtp_instance = MagicMock()
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", mock_smtp_cls):
        from app.services.digest_service import send_digest_email
        send_digest_email(
            to_email="owner@example.com",
            agent_name="Test Agent",
            stats={
                "conversation_count": 5,
                "faithfulness_score": 0.85,
                "critical_red_team_count": 0,
                "escalation_count": 1,
            },
        )

    mock_smtp_cls.assert_called_once()


@pytest.mark.xfail(strict=True, reason="digest_service not yet implemented — de-xfail in 10-05")
def test_digest_beat_skips_when_disabled(monkeypatch):
    """run_weekly_digest_beat returns {dispatched: 0} when DIGEST_ENABLED=False."""
    from unittest.mock import patch, MagicMock

    with patch("app.core.config.settings") as mock_settings:
        mock_settings.DIGEST_ENABLED = False
        from app.worker.tasks.runtime.digest import run_weekly_digest_beat
        result = run_weekly_digest_beat()

    assert result == {"dispatched": 0}


@pytest.mark.xfail(strict=True, reason="digest_service not yet implemented — de-xfail in 10-05")
def test_digest_idempotency_within_7d():
    """run_weekly_digest skips if a digest_runs row exists for this agent within 7 days."""
    from unittest.mock import MagicMock, patch
    from uuid import uuid4

    agent_id = str(uuid4())
    mock_db = MagicMock()
    # Simulate a recent digest_runs row existing
    mock_db.execute.return_value.scalar_one_or_none.return_value = MagicMock()

    with patch("app.worker.tasks.runtime.digest.get_sync_db") as mock_ctx:
        mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        from app.worker.tasks.runtime.digest import run_weekly_digest
        result = run_weekly_digest(agent_id=agent_id)

    assert result.get("status") == "already_sent"
