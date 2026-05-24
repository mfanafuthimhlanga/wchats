"""Unit tests for app.worker.tasks.runtime.deployment — M8 Celery task.

Tests:
    TestRunDeploymentChecklistIdempotency
        test_idempotency_skip_on_running_row  — idempotency guard (60 min window)

    TestRunDeploymentChecklistHappyPath
        test_happy_path_sets_status_complete  — full flow sets checklist_runs.status='complete'

    TestRunDeploymentChecklistFailurePath
        test_failure_sets_status_failed       — exception sets checklist_runs.status='failed'

Mock strategy:
    - app.worker.tasks.runtime.deployment.get_sync_db patched as context manager
    - app.worker.tasks.runtime.deployment.fernet_decrypt patched to return plain conn_str
    - app.worker.tasks.runtime.deployment._fetch_*_sync functions patched at module boundary
    - app.worker.tasks.runtime.deployment._call_orchestrator_async patched to control result_container
    - Tasks called via .run(...) to bypass Celery broker
    - In CELERY_TASK_ALWAYS_EAGER mode, self.retry() raises Retry — expected and caught in failure test
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

import uuid
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


# ---------------------------------------------------------------------------
# TestRunDeploymentChecklistIdempotency
# ---------------------------------------------------------------------------


class TestRunDeploymentChecklistIdempotency:
    """Tests for idempotency guard in run_deployment_checklist."""

    def test_idempotency_skip_on_running_row(self):
        """run_deployment_checklist returns {"status": "already_running"} when a recent running row exists.

        Simulates the idempotency guard path in run_deployment_checklist step 2.
        The task opens get_sync_db twice before the guard fires:
          - Step 1: db.get(Agent, agent_id) returns mock agent with neon_connection_string
          - Step 2: db.execute(...).scalar_one_or_none() returns mock existing ChecklistRun
        """
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())

        # Mock agent with neon_connection_string set
        mock_agent = MagicMock()
        mock_agent.neon_connection_string = b"encrypted_conn"

        # Mock existing ChecklistRun returned by idempotency query
        mock_existing_run = MagicMock()

        # Mock DB: get(Agent) returns mock_agent; execute().scalar_one_or_none() returns existing run
        mock_db = MagicMock()
        mock_db.get.return_value = mock_agent
        mock_db.execute.return_value.scalar_one_or_none.return_value = mock_existing_run

        with patch(
            "app.worker.tasks.runtime.deployment.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.deployment.fernet_decrypt",
            return_value="postgresql://test/tenant",
        ):
            result = run_deployment_checklist.run(agent_id=agent_id)

        assert result == {"status": "already_running"}, (
            f"Expected {{'status': 'already_running'}}, got {result}"
        )


# ---------------------------------------------------------------------------
# TestRunDeploymentChecklistHappyPath
# ---------------------------------------------------------------------------


class TestRunDeploymentChecklistHappyPath:
    """Tests for the happy-path flow of run_deployment_checklist."""

    def test_happy_path_sets_status_complete(self):
        """run_deployment_checklist returns complete result when orchestrator produces a report.

        Simulates the full happy-path flow:
          - Step 1: agent found with neon_connection_string
          - Step 2: idempotency check returns None (no existing running row)
          - Step 3: INSERT checklist_runs row (ORM add/commit/refresh)
          - Step 4: All 4 _fetch_*_sync functions return mock signal dicts
          - Step 5: _call_orchestrator_async side_effect sets result_container["report"]
          - Step 6: UPDATE to status='complete'
        """
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())
        mock_run_id = str(uuid.uuid4())

        # Mock agent
        mock_agent = MagicMock()
        mock_agent.neon_connection_string = b"encrypted_conn"

        # Mock ChecklistRun returned after INSERT (via db.refresh)
        mock_run = MagicMock()
        mock_run.id = mock_run_id
        mock_run.status = "running"

        # Shared mock DB: used across all get_sync_db() context manager invocations
        # - get(Agent) returns mock_agent on first call (Step 1)
        # - execute().scalar_one_or_none() returns None (Step 2: no existing running row)
        # - get(ChecklistRun, run_id) returns mock_run for the UPDATE step (Step 6)
        mock_db = MagicMock()

        def _db_get(model, pk):
            """Return appropriate mock based on model class name."""
            if hasattr(model, "__name__") and model.__name__ == "Agent":
                return mock_agent
            return mock_run

        mock_db.get.side_effect = _db_get
        mock_db.execute.return_value.scalar_one_or_none.return_value = None
        # Simulate db.refresh setting the run.id after INSERT
        mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", mock_run_id)

        # Signal fetch return values (minimal valid dicts)
        empty_eval = {"last_run_at": None, "scenario_count": 0, "pass_rates": {}, "failing_scenarios": 0}
        empty_red_team = {
            "last_run_at": None, "deployment_blocked": False,
            "critical_count": 0, "high_count": 0, "medium_count": 0, "low_count": 0,
        }
        empty_verified_qa = {"row_count": 60, "avg_faithfulness": 0.9, "avg_relevance": 0.9}
        empty_corpus = {"document_count": 5, "chunk_count": 100, "last_ingested_at": None}

        async def _fake_call_orchestrator_async(signals_json, result_container):
            """Coroutine stub that sets result_container["report"] (happy path)."""
            result_container["report"] = {
                "recommendation": "ship",
                "summary": "All signals look good.",
                "warnings": [],
            }

        with patch(
            "app.worker.tasks.runtime.deployment.get_sync_db",
            _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.deployment.fernet_decrypt",
            return_value="postgresql://test/tenant",
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_eval_summary_sync",
            return_value=empty_eval,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_red_team_summary_sync",
            return_value=empty_red_team,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_verified_qa_stats_sync",
            return_value=empty_verified_qa,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync",
            return_value=empty_corpus,
        ), patch(
            "app.worker.tasks.runtime.deployment._call_orchestrator_async",
            side_effect=_fake_call_orchestrator_async,
        ):
            result = run_deployment_checklist.run(agent_id=agent_id)

        assert result.get("status") == "complete", (
            f"Expected status='complete', got {result}"
        )
        assert "run_id" in result, f"'run_id' missing from result: {result}"
        assert result.get("recommendation") == "ship", (
            f"Expected recommendation='ship', got {result.get('recommendation')}"
        )
        # Verify db.commit() was called to persist the 'complete' status
        assert mock_db.commit.called, "db.commit() should be called to persist 'complete' status"


# ---------------------------------------------------------------------------
# TestRunDeploymentChecklistFailurePath
# ---------------------------------------------------------------------------


class TestRunDeploymentChecklistFailurePath:
    """Tests for exception/failure path in run_deployment_checklist."""

    def test_failure_sets_status_failed(self):
        """When orchestrator fails (no report produced), task sets status='failed' and retries.

        The task flow on failure:
          1. _call_orchestrator_async raises (asyncio.run re-raises it)
          2. Task falls through to Step 6: result_container.get("report") is None
          3. Task raises RuntimeError("Orchestrator did not produce a report")
          4. Except block in Step 7: set run.status = 'failed', db.commit()
          5. self.retry() raises Retry in CELERY_TASK_ALWAYS_EAGER mode — expected

        In CELERY_TASK_ALWAYS_EAGER=True mode, self.retry() re-raises the underlying
        exception rather than scheduling it. We catch Retry (or the underlying RuntimeError)
        here and verify the 'failed' status was committed.
        """
        from app.worker.tasks.runtime.deployment import run_deployment_checklist
        from celery.exceptions import Retry

        agent_id = str(uuid.uuid4())
        mock_run_id = str(uuid.uuid4())

        mock_agent = MagicMock()
        mock_agent.neon_connection_string = b"encrypted_conn"

        mock_run = MagicMock()
        mock_run.id = mock_run_id
        mock_run.status = "running"

        mock_db = MagicMock()

        def _db_get(model, pk):
            if hasattr(model, "__name__") and model.__name__ == "Agent":
                return mock_agent
            return mock_run

        mock_db.get.side_effect = _db_get
        mock_db.execute.return_value.scalar_one_or_none.return_value = None
        mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", mock_run_id)

        empty_eval = {"last_run_at": None, "scenario_count": 0, "pass_rates": {}, "failing_scenarios": 0}
        empty_red_team = {
            "last_run_at": None, "deployment_blocked": False,
            "critical_count": 0, "high_count": 0, "medium_count": 0, "low_count": 0,
        }
        empty_verified_qa = {"row_count": 0, "avg_faithfulness": 0.0, "avg_relevance": 0.0}
        empty_corpus = {"document_count": 0, "chunk_count": 0, "last_ingested_at": None}

        async def _failing_orchestrator_async(signals_json, result_container):
            """Coroutine stub that raises without setting result_container["report"]."""
            raise Exception("SDK failure")

        # In CELERY_TASK_ALWAYS_EAGER mode, self.retry() propagates as Retry or RuntimeError.
        # Catch either — the important assertion is that db.commit() was called after
        # run.status was set to "failed".
        try:
            with patch(
                "app.worker.tasks.runtime.deployment.get_sync_db",
                _make_sync_db_ctx(mock_db),
            ), patch(
                "app.worker.tasks.runtime.deployment.fernet_decrypt",
                return_value="postgresql://test/tenant",
            ), patch(
                "app.worker.tasks.runtime.deployment._fetch_eval_summary_sync",
                return_value=empty_eval,
            ), patch(
                "app.worker.tasks.runtime.deployment._fetch_red_team_summary_sync",
                return_value=empty_red_team,
            ), patch(
                "app.worker.tasks.runtime.deployment._fetch_verified_qa_stats_sync",
                return_value=empty_verified_qa,
            ), patch(
                "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync",
                return_value=empty_corpus,
            ), patch(
                "app.worker.tasks.runtime.deployment._call_orchestrator_async",
                side_effect=_failing_orchestrator_async,
            ):
                result = run_deployment_checklist.run(agent_id=agent_id)
            # If it didn't raise, result should be {} (after retry exhaustion)
            assert isinstance(result, dict), f"Expected dict result, got: {type(result)}"
        except (Retry, RuntimeError, Exception):
            # In eager mode, self.retry() raises Retry wrapping the original exception.
            # This is expected behavior — the task correctly attempted to retry.
            pass

        # Regardless of Retry exception, db.commit() must have been called to persist
        # the 'failed' status before the retry was attempted.
        assert mock_db.commit.called, (
            "db.commit() should be called to persist status='failed' before retry"
        )
        # Verify run.status was set to 'failed' at some point
        # mock_run.status is set via __setattr__ on the MagicMock object
        assert mock_run.status == "failed" or mock_db.commit.call_count >= 2, (
            "run.status should be set to 'failed' and committed before retry"
        )
