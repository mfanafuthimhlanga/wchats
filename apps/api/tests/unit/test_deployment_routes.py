"""Unit tests for app.api.v1.deployment — M8 FastAPI deployment routes.

Tests (xfail stubs — de-xfail in Plan 08-06 when deployment.py routes are implemented):
    TestGetChecklistRun
        test_get_detail_returns_report              — DEP-04: GET returns report + all signal sections

    TestAcknowledge
        test_acknowledge_updates_warning_acknowledgments — DEP-05: POST acknowledge updates JSONB

    TestApproveDeployment
        test_approve_sets_is_deployed_true          — DEP-06: POST approve sets is_deployed=True
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
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# TestGetChecklistRun
# ---------------------------------------------------------------------------


class TestGetChecklistRun:
    """Tests for GET /agents/{agent_id}/checklist-runs/{run_id} detail route."""

    @pytest.mark.xfail(strict=True, reason="deployment routes not yet implemented — de-xfail in Plan 08-06")
    def test_get_detail_returns_report(self):
        assert False, "xfail stub"


# ---------------------------------------------------------------------------
# TestAcknowledge
# ---------------------------------------------------------------------------


class TestAcknowledge:
    """Tests for POST /agents/{agent_id}/checklist-runs/{run_id}/acknowledge route."""

    @pytest.mark.xfail(strict=True, reason="deployment routes not yet implemented — de-xfail in Plan 08-06")
    def test_acknowledge_updates_warning_acknowledgments(self):
        assert False, "xfail stub"


# ---------------------------------------------------------------------------
# TestApproveDeployment
# ---------------------------------------------------------------------------


class TestApproveDeployment:
    """Tests for POST /agents/{agent_id}/approve-deployment route."""

    @pytest.mark.xfail(strict=True, reason="deployment routes not yet implemented — de-xfail in Plan 08-06")
    def test_approve_sets_is_deployed_true(self):
        assert False, "xfail stub"
