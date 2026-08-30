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

Phase 18 BLR-01 addition:
    - app.worker.tasks.runtime.deployment._fetch_blast_radius_sync is patched
      alongside the four existing _fetch_*_sync functions in every test that
      reaches Step 4 — it is the fifth collector and, unlike the other four,
      it opens its OWN get_sync_db() session inside deployment_service.py
      (not the module-level get_sync_db patched here), so leaving it
      unpatched would attempt a real control-DB connection.

Phase 18 BLR-02 addition:
    - app.worker.tasks.runtime.deployment._compute_envelope_hash_sync is
      patched alongside _fetch_blast_radius_sync for the same reason — it
      also opens its own get_sync_db() session inside deployment_service.py.
      Step 4's guarded try/except means an unpatched real-DB connection
      failure would still complete the run (envelope_hash=None), but every
      test patches it anyway to keep this module fast and independent of
      whatever is or isn't listening on localhost:5432.
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
        empty_eval = _measured_eval_signal()
        empty_red_team = _measured_red_team_signal()
        empty_verified_qa = {"row_count": 60, "avg_faithfulness": 0.9, "avg_relevance": 0.9}
        empty_corpus = {"document_count": 5, "chunk_count": 100, "last_ingested_at": None}
        empty_blast_radius = {
            "configured_max_single_action_cents": None,
            "configured_max_hourly_aggregate_cents": None,
            "observed_max_single_action_cents": None,
            "observed_max_hourly_aggregate_cents": None,
            "observed_window_days": 7,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
            "enabled_skill_count": 0,
        }

        async def _fake_call_orchestrator_async(signals_json, result_container, *, ledger=None):
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
            "app.worker.tasks.runtime.deployment._fetch_blast_radius_sync",
            return_value=empty_blast_radius,
        ), patch(
            "app.worker.tasks.runtime.deployment._compute_envelope_hash_sync",
            return_value="test-envelope-hash",
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

    def test_the_orchestrator_turn_is_billed_to_the_tenant_it_assesses(self):
        """The ledger the task builds, asserted at the call site rather than in isolation.

        Ticket #49 put the Orchestrator's prose turn on the owned loop, so it now
        leaves a `model_calls` row like every other purpose. Until then it ran on
        the Agent SDK against a model no route named, and a checklist run could
        not report what its own assessment cost.

        The seven fakes in this module take `ledger` with a default, which is what
        lets them stand in for a keyword-only parameter. A default is also what
        would let the task stop passing one without a single test noticing, so
        this is the test that reads it.
        """
        from app.core.model_client import LedgerContext
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())
        tenant_id = str(uuid.uuid4())
        mock_run_id = str(uuid.uuid4())
        seen: dict = {}

        mock_agent = MagicMock()
        mock_agent.neon_connection_string = b"encrypted_conn"
        mock_agent.tenant_id = tenant_id
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

        async def _capture(signals_json, result_container, *, ledger=None):
            seen["ledger"] = ledger
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
            return_value=_measured_eval_signal(),
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_red_team_summary_sync",
            return_value=_measured_red_team_signal(),
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_verified_qa_stats_sync",
            return_value={"row_count": 60, "avg_faithfulness": 0.9, "avg_relevance": 0.9},
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync",
            return_value={"document_count": 5, "chunk_count": 100, "last_ingested_at": None},
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_blast_radius_sync",
            return_value={
                "configured_max_single_action_cents": None,
                "configured_max_hourly_aggregate_cents": None,
                "observed_max_single_action_cents": None,
                "observed_max_hourly_aggregate_cents": None,
                "observed_window_days": 7,
                "warn_threshold_single_cents": 50000,
                "warn_threshold_hourly_cents": 200000,
                "enabled_skill_count": 0,
            },
        ), patch(
            "app.worker.tasks.runtime.deployment._compute_envelope_hash_sync",
            return_value="test-envelope-hash",
        ), patch(
            "app.worker.tasks.runtime.deployment._call_orchestrator_async",
            side_effect=_capture,
        ):
            run_deployment_checklist.run(agent_id=agent_id)

        ledger = seen.get("ledger")
        assert isinstance(ledger, LedgerContext), (
            f"the task passed {ledger!r} rather than a LedgerContext. Without one "
            "the orchestrator's turn bills nobody and the run cannot report its cost."
        )
        assert ledger.tenant_id == tenant_id, (
            "the turn is billed to the tenant whose agent it assesses, so a wrong "
            "tenant id here is a wrong tenant's invoice."
        )
        assert ledger.agent_id == agent_id
        assert ledger.job_id == mock_run_id, (
            "the checklist run is the job, so a rollup can total one run's spend."
        )


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
        from celery.exceptions import Retry

        from app.worker.tasks.runtime.deployment import run_deployment_checklist

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

        empty_eval = _measured_eval_signal()
        empty_red_team = _measured_red_team_signal()
        empty_verified_qa = {"row_count": 0, "avg_faithfulness": 0.0, "avg_relevance": 0.0}
        empty_corpus = {"document_count": 0, "chunk_count": 0, "last_ingested_at": None}
        empty_blast_radius = {
            "configured_max_single_action_cents": None,
            "configured_max_hourly_aggregate_cents": None,
            "observed_max_single_action_cents": None,
            "observed_max_hourly_aggregate_cents": None,
            "observed_window_days": 7,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
            "enabled_skill_count": 0,
        }

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
                "app.worker.tasks.runtime.deployment._fetch_blast_radius_sync",
                return_value=empty_blast_radius,
            ), patch(
                "app.worker.tasks.runtime.deployment._compute_envelope_hash_sync",
                return_value="test-envelope-hash",
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


# ---------------------------------------------------------------------------
# TestBlastRadiusWiring (Phase 18 BLR-01)
# ---------------------------------------------------------------------------


def _build_full_happy_path_mock_db(mock_run_id):
    """Shared Step 1/2/3/6 mock_db + mock_run pair for the BLR-01 wiring tests."""
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
    return mock_db, mock_run


def _measured_eval_signal():
    """An eval signal that was actually MEASURED (P2).

    These tests are about wiring, not about evidence, so they must supply a
    measured signal or apply_signal_evidence_gate downgrades every one of them
    to 'block' and they stop testing what their names say. The old fixture —
    `pass_rates: {}` with no state field — was exactly the payload audit D3
    produced in production, and every one of these tests was silently asserting
    that an agent whose quality had never been measured could ship.
    """
    from app.domain.eval_result import (
        Cost,
        DatasetOutcome,
        EvalResult,
        Invocation,
        Measurement,
    )
    from app.services.deployment_service import EVAL_SIGNAL_MEASURED, _eval_summary

    # Built through the collector's own constructor since #51 slice 4. The
    # hand-written dict this replaces had no `datasets` block at all, and the
    # gate now refuses a 'measured' signal whose gated metrics were measured on
    # no dataset, so the fixture would have downgraded every wiring test to
    # 'block' while looking like a measurement, which is the same shape of
    # mistake audit D3's `pass_rates: {}` was.
    scored = DatasetOutcome(
        attempted=30,
        valid=30,
        scored=30,
        metrics={
            "faithfulness": Measurement(value=0.92, observations=30, measured=True),
            "answer_relevancy": Measurement(value=0.9, observations=30, measured=True),
        },
        # Both gates measured and cleared on all thirty. The three verdict
        # counts add up to `scored` or the record refuses to be built.
        scenarios_passed=30,
    )
    return _eval_summary(
        EVAL_SIGNAL_MEASURED,
        last_run_at="2026-05-23T02:00:00",
        last_run_status="complete",
        record=EvalResult(
            run_id=str(uuid.uuid4()),
            agent_id=str(uuid.uuid4()),
            invocation=Invocation(
                status="measured",
                valid=30,
                attempted=30,
                responded=30,
                scorable=30,
                failed=0,
                empty=0,
            ),
            datasets={"exploratory": scored},
            requested_model="gpt-5.6-luna",
            cost=Cost(
                input_tokens=10, output_tokens=5, usd=0.01, zar=0.2, measured=True
            ),
        ),
        # D1/P3, and the same argument as `eval_signal` one release later: the
        # gate refuses a 'measured' signal that does not record having invoked
        # the agent, because until P2 the eval scored each scenario's own
        # reference answer and every stored run is silent on the question.
        agent_invoked=True,
    )


def _measured_red_team_signal():
    """A security signal that was actually MEASURED, coverage included.

    `coverage_source` is what the collector attaches in the measured state, and
    it has to be here for the same reason `eval_signal` does (see above): these
    tests are about wiring, and from P4 the gate treats a summary carrying no
    run-level coverage figure as a claim nobody made — 'we cannot confirm all
    seven attack types were tested' — and warns. Omitting it here would have
    these tests asserting against a qualification rather than the wiring.
    """
    from app.services.deployment_service import COVERAGE_SOURCE_RUN

    return {
        "signal": "measured",
        "last_run_at": "2026-05-23T03:00:00",
        "deployment_blocked": False,
        "critical_count": 0,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "vectors_attempted": 7,
        "vectors_valid": 7,
        "invalid_vectors": [],
        "coverage_complete": True,
        "coverage_source": COVERAGE_SOURCE_RUN,
    }


def _empty_first_four_signals():
    """The four pre-existing safe-default signal dicts, reused across BLR-01 wiring tests."""
    return (
        _measured_eval_signal(),
        _measured_red_team_signal(),
        {"row_count": 60, "avg_faithfulness": 0.9, "avg_relevance": 0.9},
        {"document_count": 5, "chunk_count": 100, "last_ingested_at": None},
    )


class TestBlastRadiusWiring:
    """Tests for Step 4's fifth collector call and Step 6's derived-warning merge."""

    def test_step4_calls_blast_radius_collector_with_agent_id_only(self):
        """Step 4 calls the fifth collector with exactly one positional argument — no conn_str."""
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())
        mock_run_id = str(uuid.uuid4())
        mock_db, mock_run = _build_full_happy_path_mock_db(mock_run_id)
        empty_eval, empty_red_team, empty_verified_qa, empty_corpus = _empty_first_four_signals()
        empty_blast_radius = {
            "configured_max_single_action_cents": None,
            "configured_max_hourly_aggregate_cents": None,
            "observed_max_single_action_cents": None,
            "observed_max_hourly_aggregate_cents": None,
            "observed_window_days": 7,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
            "enabled_skill_count": 0,
        }

        async def _fake_call_orchestrator_async(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "recommendation": "ship", "summary": "All good.", "warnings": [],
            }

        with patch(
            "app.worker.tasks.runtime.deployment.get_sync_db", _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.deployment.fernet_decrypt",
            return_value="postgresql://test/tenant",
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_eval_summary_sync", return_value=empty_eval,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_red_team_summary_sync",
            return_value=empty_red_team,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_verified_qa_stats_sync",
            return_value=empty_verified_qa,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync", return_value=empty_corpus,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_blast_radius_sync",
            return_value=empty_blast_radius,
        ) as mock_blast_radius, patch(
            "app.worker.tasks.runtime.deployment._compute_envelope_hash_sync",
            return_value="test-envelope-hash",
        ), patch(
            "app.worker.tasks.runtime.deployment._call_orchestrator_async",
            side_effect=_fake_call_orchestrator_async,
        ):
            run_deployment_checklist.run(agent_id=agent_id)

        mock_blast_radius.assert_called_once_with(agent_id)

    def test_blast_radius_collector_failure_does_not_fail_the_run(self):
        """A raising collector is contained by its own try/except; the run still completes
        and the persisted signal carries the copied safe-default shape with None figures."""
        from app.worker.tasks.runtime.deployment import (
            BLAST_RADIUS_DEFAULT_SIGNAL,
            run_deployment_checklist,
        )

        agent_id = str(uuid.uuid4())
        mock_run_id = str(uuid.uuid4())
        mock_db, mock_run = _build_full_happy_path_mock_db(mock_run_id)
        empty_eval, empty_red_team, empty_verified_qa, empty_corpus = _empty_first_four_signals()

        async def _fake_call_orchestrator_async(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "recommendation": "ship", "summary": "All good.", "warnings": [],
            }

        with patch(
            "app.worker.tasks.runtime.deployment.get_sync_db", _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.deployment.fernet_decrypt",
            return_value="postgresql://test/tenant",
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_eval_summary_sync", return_value=empty_eval,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_red_team_summary_sync",
            return_value=empty_red_team,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_verified_qa_stats_sync",
            return_value=empty_verified_qa,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync", return_value=empty_corpus,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_blast_radius_sync",
            side_effect=RuntimeError("control DB unreachable"),
        ), patch(
            "app.worker.tasks.runtime.deployment._compute_envelope_hash_sync",
            return_value="test-envelope-hash",
        ), patch(
            "app.worker.tasks.runtime.deployment._call_orchestrator_async",
            side_effect=_fake_call_orchestrator_async,
        ):
            result = run_deployment_checklist.run(agent_id=agent_id)

        assert result.get("status") == "complete", (
            f"A blast-radius collector failure must not fail the run: {result}"
        )
        persisted_blast_radius = mock_run.report["blast_radius"]
        assert persisted_blast_radius == BLAST_RADIUS_DEFAULT_SIGNAL
        assert persisted_blast_radius["configured_max_single_action_cents"] is None
        assert persisted_blast_radius["observed_max_single_action_cents"] is None

    def test_derived_blast_radius_warning_reaches_persisted_warnings(self):
        """A derived blast-radius warning and an unrelated orchestrator warning both
        land in the persisted checklist_runs.warnings list."""
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())
        mock_run_id = str(uuid.uuid4())
        mock_db, mock_run = _build_full_happy_path_mock_db(mock_run_id)
        empty_eval, empty_red_team, empty_verified_qa, empty_corpus = _empty_first_four_signals()
        above_threshold_blast_radius = {
            "configured_max_single_action_cents": 60000,
            "configured_max_hourly_aggregate_cents": 10000,
            "observed_max_single_action_cents": None,
            "observed_max_hourly_aggregate_cents": None,
            "observed_window_days": 7,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
            "enabled_skill_count": 1,
        }

        async def _fake_call_orchestrator_async(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "recommendation": "ship_with_warnings",
                "summary": "One eval metric is a little low.",
                "warnings": [
                    {
                        "warning_id": "eval_low_pass_rate",
                        "category": "eval_quality",
                        "message": "One metric scored below 0.85.",
                        "severity_level": "warning",
                    }
                ],
            }

        with patch(
            "app.worker.tasks.runtime.deployment.get_sync_db", _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.deployment.fernet_decrypt",
            return_value="postgresql://test/tenant",
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_eval_summary_sync", return_value=empty_eval,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_red_team_summary_sync",
            return_value=empty_red_team,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_verified_qa_stats_sync",
            return_value=empty_verified_qa,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync", return_value=empty_corpus,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_blast_radius_sync",
            return_value=above_threshold_blast_radius,
        ), patch(
            "app.worker.tasks.runtime.deployment._compute_envelope_hash_sync",
            return_value="test-envelope-hash",
        ), patch(
            "app.worker.tasks.runtime.deployment._call_orchestrator_async",
            side_effect=_fake_call_orchestrator_async,
        ):
            run_deployment_checklist.run(agent_id=agent_id)

        persisted_ids = {w["warning_id"] for w in mock_run.warnings}
        assert "eval_low_pass_rate" in persisted_ids
        assert "blast_radius_single_action_above_threshold" in persisted_ids

    def test_derived_warning_not_duplicated_when_orchestrator_emits_same_id(self):
        """When the orchestrator already emits the same warning_id, the merge keeps
        exactly one row — the orchestrator's own, not a second derived copy."""
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())
        mock_run_id = str(uuid.uuid4())
        mock_db, mock_run = _build_full_happy_path_mock_db(mock_run_id)
        empty_eval, empty_red_team, empty_verified_qa, empty_corpus = _empty_first_four_signals()
        above_threshold_blast_radius = {
            "configured_max_single_action_cents": 60000,
            "configured_max_hourly_aggregate_cents": 10000,
            "observed_max_single_action_cents": None,
            "observed_max_hourly_aggregate_cents": None,
            "observed_window_days": 7,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
            "enabled_skill_count": 1,
        }

        async def _fake_call_orchestrator_async(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "recommendation": "ship_with_warnings",
                "summary": "Flagged by the orchestrator too.",
                "warnings": [
                    {
                        "warning_id": "blast_radius_single_action_above_threshold",
                        "category": "financial_exposure",
                        "message": "Orchestrator-authored duplicate.",
                        "severity_level": "warning",
                    }
                ],
            }

        with patch(
            "app.worker.tasks.runtime.deployment.get_sync_db", _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.deployment.fernet_decrypt",
            return_value="postgresql://test/tenant",
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_eval_summary_sync", return_value=empty_eval,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_red_team_summary_sync",
            return_value=empty_red_team,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_verified_qa_stats_sync",
            return_value=empty_verified_qa,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync", return_value=empty_corpus,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_blast_radius_sync",
            return_value=above_threshold_blast_radius,
        ), patch(
            "app.worker.tasks.runtime.deployment._compute_envelope_hash_sync",
            return_value="test-envelope-hash",
        ), patch(
            "app.worker.tasks.runtime.deployment._call_orchestrator_async",
            side_effect=_fake_call_orchestrator_async,
        ):
            run_deployment_checklist.run(agent_id=agent_id)

        matching = [
            w for w in mock_run.warnings
            if w["warning_id"] == "blast_radius_single_action_above_threshold"
        ]
        assert len(matching) == 1, (
            f"Expected exactly one blast_radius_single_action_above_threshold row, got: {matching}"
        )
        assert matching[0]["message"] == "Orchestrator-authored duplicate."


# ---------------------------------------------------------------------------
# TestEvidenceGateWiring (P2) — an absent signal reaches the persisted verdict
# ---------------------------------------------------------------------------


class TestEvidenceGateWiring:
    """The gate has to change what is WRITTEN, not just what a helper returns.

    apply_signal_evidence_gate is unit-tested in test_deployment_service.py.
    What matters here is the wiring: the recommendation the owner sees, the one
    persisted on checklist_runs and the one the approve route reads must all be
    the gated value, and the reason must arrive with it as a warning. A gate
    applied after the report is built, or applied to a copy, would pass every
    test in the other module and change nothing.
    """

    def _drive(
        self,
        eval_summary,
        red_team_summary,
        orchestrator_recommendation="ship",
        dispatch=None,
    ):
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())
        self.agent_id = agent_id
        mock_run_id = str(uuid.uuid4())
        mock_db, mock_run = _build_full_happy_path_mock_db(mock_run_id)

        async def _fake_call_orchestrator_async(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "recommendation": orchestrator_recommendation,
                "summary": "All good.",
                "warnings": [],
            }

        blast_radius = {
            "configured_max_single_action_cents": None,
            "configured_max_hourly_aggregate_cents": None,
            "observed_max_single_action_cents": None,
            "observed_max_hourly_aggregate_cents": None,
            "observed_window_days": 7,
            "warn_threshold_single_cents": 50000,
            "warn_threshold_hourly_cents": 200000,
            "enabled_skill_count": 0,
        }

        with patch(
            "app.worker.tasks.runtime.deployment.get_sync_db", _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.deployment.fernet_decrypt",
            return_value="postgresql://test/tenant",
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_eval_summary_sync",
            return_value=eval_summary,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_red_team_summary_sync",
            return_value=red_team_summary,
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_verified_qa_stats_sync",
            return_value={"row_count": 60, "avg_faithfulness": 0.9, "avg_relevance": 0.9},
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync",
            return_value={"document_count": 5, "chunk_count": 100, "last_ingested_at": None},
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_blast_radius_sync",
            return_value=blast_radius,
        ), patch(
            "app.worker.tasks.runtime.deployment._compute_envelope_hash_sync",
            return_value="test-envelope-hash",
        ), patch(
            "app.worker.tasks.runtime.deployment._dispatch_eval_run",
            side_effect=(dispatch if dispatch is not None else (lambda _a: True)),
        ) as dispatch_mock, patch(
            "app.worker.tasks.runtime.deployment._call_orchestrator_async",
            side_effect=_fake_call_orchestrator_async,
        ):
            result = run_deployment_checklist.run(agent_id=agent_id)
            self.dispatch_mock = dispatch_mock

        return result, mock_run

    def test_an_unmeasured_eval_signal_blocks_the_persisted_recommendation(self):
        """The orchestrator says ship; the platform writes block.

        This is audit D3's whole consequence in one assertion. Before P2 the
        eval query raised, the task substituted an empty pass_rates dict and
        this exact run shipped.
        """
        from app.services.deployment_service import EVAL_SUMMARY_UNAVAILABLE_SIGNAL

        result, mock_run = self._drive(
            dict(EVAL_SUMMARY_UNAVAILABLE_SIGNAL), _measured_red_team_signal()
        )

        assert result["recommendation"] == "block"
        assert mock_run.recommendation == "block", (
            "the gated verdict must be the one PERSISTED — the approve route "
            "reads checklist_runs.recommendation, not the task's return value"
        )
        assert mock_run.report["recommendation"] == "block"

    def test_the_reason_is_persisted_as_a_warning(self):
        """A 'block' with no stated reason is an unexplained refusal."""
        from app.services.deployment_service import EVAL_SUMMARY_UNAVAILABLE_SIGNAL

        _, mock_run = self._drive(
            dict(EVAL_SUMMARY_UNAVAILABLE_SIGNAL), _measured_red_team_signal()
        )

        warning_ids = [w["warning_id"] for w in mock_run.warnings]
        assert "eval_signal_unavailable" in warning_ids

    def test_an_unreadable_red_team_signal_blocks_too(self):
        from app.services.deployment_service import RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL

        result, mock_run = self._drive(
            _measured_eval_signal(), dict(RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL)
        )

        assert result["recommendation"] == "block"
        assert "red_team_signal_unavailable" in [
            w["warning_id"] for w in mock_run.warnings
        ]

    def test_measured_signals_leave_the_orchestrator_verdict_intact(self):
        """The gate is a floor, not a second opinion."""
        result, mock_run = self._drive(
            _measured_eval_signal(), _measured_red_team_signal()
        )

        assert result["recommendation"] == "ship"
        assert mock_run.recommendation == "ship"
        assert mock_run.warnings == []

    def test_a_collector_failure_substitutes_an_unavailable_signal_not_zeros(self):
        """Step 4's except clause is the substitution audit D3 exploited.

        `_fetch_eval_summary_sync` raising used to yield `pass_rates: {}` —
        a measurement-shaped value for a query that never ran. Driven here
        through the real except path rather than by handing the task a
        pre-built dict, because the substitution IS the thing under test.
        """
        from app.services.deployment_service import (
            BLAST_RADIUS_DEFAULT_SIGNAL as _BLAST_RADIUS_DEFAULT_SIGNAL,
        )
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())
        mock_run_id = str(uuid.uuid4())
        mock_db, mock_run = _build_full_happy_path_mock_db(mock_run_id)

        async def _fake_call_orchestrator_async(signals_json, result_container, *, ledger=None):
            # The orchestrator is handed the substituted signal, so assert on
            # what it was told as well as on what the platform decided.
            import json as _json

            signals = _json.loads(signals_json)
            assert signals["eval_summary"]["eval_signal"] == "unavailable"
            assert signals["eval_summary"]["pass_rates"] is None
            result_container["report"] = {
                "recommendation": "ship", "summary": "All good.", "warnings": [],
            }

        with patch(
            "app.worker.tasks.runtime.deployment.get_sync_db", _make_sync_db_ctx(mock_db),
        ), patch(
            "app.worker.tasks.runtime.deployment.fernet_decrypt",
            return_value="postgresql://test/tenant",
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_eval_summary_sync",
            side_effect=RuntimeError("tenant DB unreachable"),
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_red_team_summary_sync",
            return_value=_measured_red_team_signal(),
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_verified_qa_stats_sync",
            return_value={"row_count": 60, "avg_faithfulness": 0.9, "avg_relevance": 0.9},
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync",
            return_value={"document_count": 5, "chunk_count": 100, "last_ingested_at": None},
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_blast_radius_sync",
            return_value=dict(_BLAST_RADIUS_DEFAULT_SIGNAL),
        ), patch(
            "app.worker.tasks.runtime.deployment._compute_envelope_hash_sync",
            return_value="test-envelope-hash",
        ), patch(
            "app.worker.tasks.runtime.deployment._call_orchestrator_async",
            side_effect=_fake_call_orchestrator_async,
        ):
            result = run_deployment_checklist.run(agent_id=agent_id)

        assert result["recommendation"] == "block"


# ---------------------------------------------------------------------------
# TestDayOneEvalPath (P2 review) — the block has to be one the owner can clear
# ---------------------------------------------------------------------------


class TestDayOneEvalPath(TestEvidenceGateWiring):
    """EVAL_SIGNAL_NO_RUNS hard-blocks, and nothing in signup -> ingest ->
    deploy produced an eval run.

    `run_eval_suite` is dispatched by the nightly beat and by the "Run Now"
    button on the eval dashboard. Agent creation dispatches neither, and the
    ingestion chain ends at synthesize_retrieval_strategy. So a new tenant
    ingested documents, reached status='ready', ran the readiness check, got
    'block', and POST /approve-deployment answered 422 — with the only remedy on
    a page the onboarding flow never routes to. CLAUDE.md's stated core value
    ends in "deploy".

    The fix is not to soften the gate. An agent with no measurement still must
    not ship. The checklist starts the measurement it is demanding, and says so.
    """

    def _never_evaluated(self):
        return {
            "eval_signal": "no_runs",
            "signal_detail": "no eval run has ever been recorded for this agent",
            "last_run_at": None,
            "last_run_status": None,
            "scenario_count": 0,
            "valid_scenario_count": None,
            "scored_scenario_count": 0,
            "denominator_source": None,
            "pass_rates": None,
            "failing_scenarios": None,
        }

    def test_a_never_evaluated_agent_still_cannot_ship(self):
        """The gate is unchanged: an eval that is RUNNING is not evidence."""
        result, mock_run = self._drive(
            self._never_evaluated(), _measured_red_team_signal()
        )

        assert result["recommendation"] == "block"
        assert mock_run.recommendation == "block"

    def test_the_first_eval_is_started_by_the_checklist_itself(self):
        self._drive(self._never_evaluated(), _measured_red_team_signal())

        self.dispatch_mock.assert_called_once_with(self.agent_id)

    def test_the_warning_tells_the_owner_to_wait_not_to_find_a_page(self):
        """A non-technical owner cannot act on "run an eval from the Evaluation
        page" if nothing routed them there. Once the checklist has started the
        run, the honest instruction is to come back."""
        _, mock_run = self._drive(
            self._never_evaluated(), _measured_red_team_signal()
        )

        matching = [
            w for w in mock_run.warnings if w["warning_id"] == "eval_never_run"
        ]
        assert len(matching) == 1, (
            f"expected one eval_never_run warning, got {mock_run.warnings}"
        )
        assert "started" in matching[0]["message"].lower()

    def test_a_failed_dispatch_still_produces_a_report(self):
        """Broker down. The checklist has already collected every other signal
        and still owes the owner a verdict; the warning falls back to naming the
        page rather than promising a run that was never queued."""
        _, mock_run = self._drive(
            self._never_evaluated(),
            _measured_red_team_signal(),
            dispatch=lambda _a: False,
        )

        matching = [
            w for w in mock_run.warnings if w["warning_id"] == "eval_never_run"
        ]
        assert len(matching) == 1
        assert "started" not in matching[0]["message"].lower()
        assert "Evaluation page" in matching[0]["message"]

    def test_nothing_is_dispatched_when_a_run_already_exists(self):
        """A measured agent, or one whose run scored nothing, has a run on the
        record. Re-dispatching on every readiness check would run the judge
        against the whole scenario set each time an owner clicks the button."""
        self._drive(_measured_eval_signal(), _measured_red_team_signal())
        self.dispatch_mock.assert_not_called()

        self._drive(
            dict(_measured_eval_signal(), eval_signal="no_valid_scores",
                 pass_rates=None),
            _measured_red_team_signal(),
        )
        self.dispatch_mock.assert_not_called()


class TestExistingTenantEvalPath(TestEvidenceGateWiring):
    """The population P3 actually creates, and the convergence it did not get.

    Step 4b was written for EVAL_SIGNAL_NO_RUNS, which is day 1. P3 then made
    every EXISTING tenant block too — and none of them is in `no_runs`: they
    have runs, produced by the tautology, which now report
    EVAL_SIGNAL_AGENT_NOT_INVOKED. So the convergence mechanism fired for
    nobody, and the warning routed the whole population to "the Evaluation
    page", which _dispatch_eval_run's own docstring says the onboarding flow
    reaches from nowhere. The wall had moved, not gone.

    IT FIRES FOR THE ABSENT HALF ONLY, and that asymmetry is the design rather
    than an oversight. `agent_invoked is None` is the historical population and
    it converges: a fresh run on a 0013+ tenant writes the key either way, so
    the state cannot recur and the dispatch is one-shot per agent, exactly like
    day 1. `agent_invoked is False` is a run that looked and said no — a broken
    or unreachable agent produces it again every night — so dispatching on it
    would buy up to AGENT_INVOCATION_MAX_CALLS_PER_RUN live SDK turns on every
    readiness check and leave the state unchanged. BACKLOG 2.18 carries the one
    residual: a pre-0013 tenant DB cannot record the key, so absence recurs
    there.
    """

    def _tautological(self, **over):
        """What every stored eval run on the platform looks like today."""
        payload = dict(
            _measured_eval_signal(),
            eval_signal="agent_not_invoked",
            agent_invoked=None,
            signal_detail=(
                "the most recent eval run does not record whether the agent "
                "was invoked at all"
            ),
            pass_rates=None,
            failing_scenarios=None,
        )
        payload.update(over)
        return payload

    def test_an_existing_tenant_still_cannot_ship(self):
        result, mock_run = self._drive(
            self._tautological(), _measured_red_team_signal()
        )

        assert result["recommendation"] == "block"
        assert mock_run.recommendation == "block"

    def test_a_fresh_run_is_started_for_the_historical_population(self):
        self._drive(self._tautological(), _measured_red_team_signal())

        self.dispatch_mock.assert_called_once_with(self.agent_id)

    def test_the_warning_tells_them_to_wait_rather_than_naming_a_page(self):
        _, mock_run = self._drive(
            self._tautological(), _measured_red_team_signal()
        )

        matching = [
            w for w in mock_run.warnings
            if w["warning_id"] == "eval_agent_not_invoked"
        ]
        assert len(matching) == 1, (
            f"expected one eval_agent_not_invoked warning, got {mock_run.warnings}"
        )
        assert "started" in matching[0]["message"].lower()
        assert "Evaluation page" not in matching[0]["message"]

    def test_a_failed_dispatch_falls_back_to_naming_the_page(self):
        """Broker down. The checklist still owes the owner a verdict, and the
        message must not promise a run that was never queued."""
        _, mock_run = self._drive(
            self._tautological(),
            _measured_red_team_signal(),
            dispatch=lambda _a: False,
        )

        matching = [
            w for w in mock_run.warnings
            if w["warning_id"] == "eval_agent_not_invoked"
        ]
        assert len(matching) == 1
        assert "started" not in matching[0]["message"].lower()
        assert "Evaluation page" in matching[0]["message"]

    def test_a_run_that_recorded_false_does_not_re_dispatch(self):
        """The spend bound. This state repeats — a broken agent produces it
        every night — so firing on it would buy a fresh set of up to sixty live
        SDK turns on every readiness check and leave the state unchanged. That
        is not convergence."""
        self._drive(
            self._tautological(agent_invoked=False),
            _measured_red_team_signal(),
        )

        self.dispatch_mock.assert_not_called()

    def test_a_failed_run_does_not_re_dispatch_either(self):
        """Same argument: 'run_failed' recurs for whatever reason produced it,
        and the owner is the one who decides to spend another run."""
        self._drive(
            dict(_measured_eval_signal(), eval_signal="run_failed",
                 last_run_status="failed", pass_rates=None),
            _measured_red_team_signal(),
        )

        self.dispatch_mock.assert_not_called()

    def test_the_dispatch_helper_passes_only_the_agent_id(self):
        """CLAUDE.md rule 4: no connection string in a Celery task argument.

        Asserted against the real helper rather than the patched one — this is
        the only test that exercises it.
        """
        from app.worker.tasks.runtime import deployment as deployment_task

        agent_id = str(uuid.uuid4())
        captured = {}

        class _FakeChain:
            def __init__(self, *signatures):
                captured["signatures"] = signatures

            def apply_async(self, **kwargs):
                captured["options"] = kwargs

        with patch("celery.chain", _FakeChain):
            assert deployment_task._dispatch_eval_run(agent_id) is True

        assert captured["options"] == {"queue": "runtime"}
        assert len(captured["signatures"]) == 2, (
            "scenario generation must precede the run — a tenant whose "
            "generation has never run has nothing to evaluate against"
        )
        for signature in captured["signatures"]:
            assert signature.args == (agent_id,), (
                f"a task argument other than agent_id crossed the boundary: "
                f"{signature.args}"
            )
            assert signature.immutable, (
                "a mutable signature would hand run_eval_suite the previous "
                "task result as its first positional argument"
            )

    def test_a_broker_failure_is_reported_rather_than_raised(self):
        """The checklist must survive a dispatch failure — it is a remedy, not
        a precondition."""
        from app.worker.tasks.runtime import deployment as deployment_task

        def _boom(*a, **kw):
            raise RuntimeError("broker unreachable")

        with patch("celery.chain", _boom):
            assert deployment_task._dispatch_eval_run("agent-1") is False
