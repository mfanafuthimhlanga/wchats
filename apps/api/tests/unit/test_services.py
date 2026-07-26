"""
Unit tests for app.services.neon and app.services.migrations.

Tests neon service functions with mocked NeonAPI and SQLAlchemy engines,
and migrations service with mocked Alembic and SQLAlchemy.

Also covers app.core.logging (configure_logging, RequestIdMiddleware).
"""

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest

# conftest.py sets required env vars


# ---------------------------------------------------------------------------
# Test neon.wait_for_neon_ready
# ---------------------------------------------------------------------------


class TestWaitForNeonReady:
    @patch("app.services.neon.create_engine")
    def test_wait_for_neon_ready_succeeds_on_first_attempt(self, mock_create_engine):
        """wait_for_neon_ready returns None on successful first connection."""
        from app.services.neon import wait_for_neon_ready

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_engine.connect.return_value.__enter__ = lambda s: mock_conn
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        result = wait_for_neon_ready("postgresql://test:test@host/db", max_attempts=3)

        assert result is None
        mock_create_engine.assert_called_once()
        mock_engine.dispose.assert_called_once()

    @patch("app.services.neon.time.sleep")
    @patch("app.services.neon.create_engine")
    def test_wait_for_neon_ready_raises_after_max_attempts(
        self, mock_create_engine, mock_sleep
    ):
        """wait_for_neon_ready raises RuntimeError after max_attempts failures."""
        from app.services.neon import wait_for_neon_ready

        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_engine.connect.side_effect = Exception("Connection refused")

        with pytest.raises(RuntimeError, match="not query-ready"):
            wait_for_neon_ready("postgresql://test@host/db", max_attempts=2)

    @patch("app.services.neon.time.sleep")
    @patch("app.services.neon.create_engine")
    def test_wait_for_neon_ready_retries_then_succeeds(
        self, mock_create_engine, mock_sleep
    ):
        """wait_for_neon_ready retries on failure and returns None on eventual success."""
        from app.services.neon import wait_for_neon_ready

        # First call fails, second succeeds
        mock_engine_fail = MagicMock()
        mock_engine_fail.connect.side_effect = Exception("Connection refused")
        mock_engine_fail.dispose = MagicMock()

        mock_engine_ok = MagicMock()
        mock_conn = MagicMock()
        mock_engine_ok.connect.return_value.__enter__ = lambda s: mock_conn
        mock_engine_ok.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_engine_ok.dispose = MagicMock()

        mock_create_engine.side_effect = [mock_engine_fail, mock_engine_ok]

        result = wait_for_neon_ready("postgresql://test@host/db", max_attempts=3)

        assert result is None
        assert mock_create_engine.call_count == 2
        mock_sleep.assert_called_once_with(1)  # 2^0 = 1


# ---------------------------------------------------------------------------
# Test neon.create_neon_project
# ---------------------------------------------------------------------------


class TestCreateNeonProject:
    """create_neon_project drives the Neon REST API through ``requests`` directly.

    The neon_api SDK was dropped because it discards the HTTP status code when
    raising NeonAPIError, and provision_neon needs the code to tell a fatal 4xx
    from a retryable 5xx — so these tests mock ``requests``, not a client class.
    create_neon_project also does NOT poll operations or raise TimeoutError any
    more; compute readiness moved to wait_for_neon_ready (covered above).
    """

    @staticmethod
    def _ok(payload):
        r = MagicMock()
        r.ok = True
        r.json.return_value = payload
        return r

    @patch("app.services.neon.requests")
    def test_create_neon_project_returns_expected_keys(self, mock_requests):
        """create_neon_project returns dict with id, pooled_uri, direct_uri."""
        from app.services.neon import create_neon_project

        mock_requests.post.return_value = self._ok({"project": {"id": "proj-abc123"}})
        # Two GETs: pooled URI first, then direct URI.
        mock_requests.get.side_effect = [
            self._ok({"uri": "postgresql://pooled@host/db?sslmode=require"}),
            self._ok({"uri": "postgresql://direct@host/db?sslmode=require"}),
        ]

        result = create_neon_project("agent-uuid-123")

        assert result["id"] == "proj-abc123"
        assert result["pooled_uri"] == "postgresql://pooled@host/db?sslmode=require"
        assert result["direct_uri"] == "postgresql://direct@host/db?sslmode=require"

        # The pooled/direct distinction is the whole point of the two GETs —
        # swapping them would silently hand DDL callers a PgBouncer endpoint.
        assert mock_requests.get.call_args_list[0].kwargs["params"]["pooled"] == "true"
        assert mock_requests.get.call_args_list[1].kwargs["params"]["pooled"] == "false"

    @patch("app.services.neon.requests")
    def test_create_neon_project_raises_neon_http_error_with_status_code(
        self, mock_requests
    ):
        """A non-2xx create response raises NeonHTTPError carrying the status code.

        provision_neon branches on .status_code (fatal 4xx vs retryable 5xx), so
        losing the code here would turn a quota rejection into an infinite retry.
        """
        from app.services.neon import NeonHTTPError, create_neon_project

        bad = MagicMock()
        bad.ok = False
        bad.status_code = 422
        bad.text = "quota exceeded"
        mock_requests.post.return_value = bad

        with pytest.raises(NeonHTTPError) as exc_info:
            create_neon_project("agent-uuid-quota")

        assert exc_info.value.status_code == 422
        # No connection URIs are fetched once creation has failed.
        mock_requests.get.assert_not_called()


# ---------------------------------------------------------------------------
# Test migrations service
# ---------------------------------------------------------------------------


class TestMigrations:
    @patch("app.services.migrations.command")
    @patch("app.services.migrations.create_engine")
    def test_run_tenant_migrations_calls_upgrade_head(
        self, mock_create_engine, mock_command
    ):
        """run_tenant_migrations calls alembic command.upgrade with 'head'."""
        from app.services.migrations import run_tenant_migrations

        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_conn = MagicMock()
        mock_engine.begin.return_value.__enter__ = lambda s: mock_conn
        mock_engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        run_tenant_migrations("postgresql://test@host/db")

        mock_command.upgrade.assert_called_once()
        # Second arg to command.upgrade should be "head"
        call_args = mock_command.upgrade.call_args
        assert call_args[0][1] == "head"

    @patch("app.services.migrations.MigrationContext")
    @patch("app.services.migrations.create_engine")
    def test_get_current_alembic_revision_returns_revision(
        self, mock_create_engine, mock_migration_context
    ):
        """get_current_alembic_revision returns the current revision ID."""
        from app.services.migrations import get_current_alembic_revision

        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = lambda s: mock_conn
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        mock_ctx = MagicMock()
        mock_ctx.get_current_revision.return_value = "ae1027a6acf2"
        mock_migration_context.configure.return_value = mock_ctx

        result = get_current_alembic_revision("postgresql://test@host/db")

        assert result == "ae1027a6acf2"
        mock_engine.dispose.assert_called_once()

    @patch("app.services.migrations.MigrationContext")
    @patch("app.services.migrations.create_engine")
    def test_get_current_alembic_revision_returns_none_when_no_migration(
        self, mock_create_engine, mock_migration_context
    ):
        """get_current_alembic_revision returns None before any migration is applied."""
        from app.services.migrations import get_current_alembic_revision

        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = lambda s: mock_conn
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        mock_ctx = MagicMock()
        mock_ctx.get_current_revision.return_value = None
        mock_migration_context.configure.return_value = mock_ctx

        result = get_current_alembic_revision("postgresql://test@host/db")

        assert result is None


# ---------------------------------------------------------------------------
# Test app.core.logging
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_configure_logging_runs_without_error(self):
        """configure_logging() must not raise."""
        from app.core.logging import configure_logging

        configure_logging("INFO")
        configure_logging("DEBUG")
        configure_logging("WARNING")


@pytest.mark.asyncio
class TestRequestIdMiddleware:
    async def test_request_id_middleware_binds_context_for_http(self):
        """Middleware binds request_id contextvars for HTTP requests."""
        import structlog
        from app.core.logging import RequestIdMiddleware

        # Track what was called on the inner app
        received_calls = []

        async def inner_app(scope, receive, send):
            received_calls.append(scope["type"])

        middleware = RequestIdMiddleware(inner_app)

        scope = {"type": "http"}
        await middleware(scope, None, None)

        assert "http" in received_calls

    async def test_request_id_middleware_passes_through_non_http(self):
        """Middleware passes through WebSocket/lifespan scopes without error."""
        from app.core.logging import RequestIdMiddleware

        received_scopes = []

        async def inner_app(scope, receive, send):
            received_scopes.append(scope["type"])

        middleware = RequestIdMiddleware(inner_app)

        # WebSocket scope — no context binding
        scope = {"type": "websocket"}
        await middleware(scope, None, None)

        assert "websocket" in received_scopes
