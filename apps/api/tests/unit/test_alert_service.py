"""Unit tests for app.services.alert_service — M10 threshold alerts (OPS-04).

De-xfailed in Phase 10-05. Tests cover:
    test_eval_regression_triggers_alert     — faithfulness below threshold writes eval_regression alert
    test_red_team_critical_triggers_alert   — critical findings >= threshold writes red_team_critical alert
    test_no_alert_when_thresholds_met       — metrics above threshold -> no new alert written
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

from unittest.mock import MagicMock


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
