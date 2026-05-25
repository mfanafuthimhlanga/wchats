"""Unit tests for app.services.alert_service — M10 threshold alerts (OPS-04).

De-xfailed in Phase 10-05. Tests cover:
    test_eval_regression_triggers_alert     — faithfulness below threshold writes eval_regression alert
    test_red_team_critical_triggers_alert   — critical findings >= threshold writes red_team_critical alert
    test_no_alert_when_thresholds_met       — metrics above threshold → no new alert written
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


@pytest.mark.xfail(strict=True, reason="alert_service not yet implemented — de-xfail in 10-05")
def test_eval_regression_triggers_alert():
    """Faithfulness below ALERT_FAITHFULNESS_THRESHOLD writes an eval_regression alert row."""
    from unittest.mock import MagicMock, patch
    from uuid import uuid4

    agent_id = str(uuid4())
    mock_db = MagicMock()
    # Simulate no existing active alert
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    with patch("app.services.alert_service.get_sync_db") as mock_ctx, \
         patch("app.core.config.settings") as mock_settings:
        mock_settings.ALERT_FAITHFULNESS_THRESHOLD = 0.6
        mock_settings.ALERT_RED_TEAM_CRITICAL_COUNT = 1
        mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from app.services.alert_service import check_and_write_alerts
        check_and_write_alerts(
            agent_id=agent_id,
            faithfulness=0.4,       # below threshold → triggers
            critical_red_team_count=0,
        )

    # db.add() was called with an Alert row
    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.alert_type == "eval_regression"


@pytest.mark.xfail(strict=True, reason="alert_service not yet implemented — de-xfail in 10-05")
def test_red_team_critical_triggers_alert():
    """Critical red_team findings >= ALERT_RED_TEAM_CRITICAL_COUNT writes a red_team_critical alert."""
    from unittest.mock import MagicMock, patch
    from uuid import uuid4

    agent_id = str(uuid4())
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    with patch("app.services.alert_service.get_sync_db") as mock_ctx, \
         patch("app.core.config.settings") as mock_settings:
        mock_settings.ALERT_FAITHFULNESS_THRESHOLD = 0.6
        mock_settings.ALERT_RED_TEAM_CRITICAL_COUNT = 1
        mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from app.services.alert_service import check_and_write_alerts
        check_and_write_alerts(
            agent_id=agent_id,
            faithfulness=0.9,       # above threshold → no eval_regression
            critical_red_team_count=2,  # >= 1 → triggers red_team_critical
        )

    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.alert_type == "red_team_critical"


@pytest.mark.xfail(strict=True, reason="alert_service not yet implemented — de-xfail in 10-05")
def test_no_alert_when_thresholds_met():
    """Metrics above all thresholds — no alert row written."""
    from unittest.mock import MagicMock, patch
    from uuid import uuid4

    agent_id = str(uuid4())
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    with patch("app.services.alert_service.get_sync_db") as mock_ctx, \
         patch("app.core.config.settings") as mock_settings:
        mock_settings.ALERT_FAITHFULNESS_THRESHOLD = 0.6
        mock_settings.ALERT_RED_TEAM_CRITICAL_COUNT = 1
        mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from app.services.alert_service import check_and_write_alerts
        check_and_write_alerts(
            agent_id=agent_id,
            faithfulness=0.95,      # above threshold
            critical_red_team_count=0,  # below threshold
        )

    mock_db.add.assert_not_called()
