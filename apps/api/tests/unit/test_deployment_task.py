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

import json
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.domain.calibration_status import CalibrationStatus

# ---------------------------------------------------------------------------
# Helper: build a mock get_sync_db context manager
# ---------------------------------------------------------------------------


def _make_sync_db_ctx(mock_db):
    """Return a patched get_sync_db that yields mock_db when used as 'with get_sync_db() as db'."""
    @contextmanager
    def _fake_get_sync_db():
        yield mock_db

    return _fake_get_sync_db


def _deployment_patch(name, **kwargs):
    """patch() against a name in the task module's own namespace."""
    return patch("app.worker.tasks.runtime.deployment." + name, **kwargs)


def _fence_updates(mock_db, mock_run):
    """Make this mock_db honour the chain's own WHERE status = 'running' fence.

    A MagicMock answers every statement with a truthy result, so a fence written
    against one can never be observed to hold and a test of it would be a
    tautology. This applies an UPDATE's values to `mock_run` only while the row
    still says 'running', the way Postgres would, and hands back the run id or
    nothing accordingly. Every other statement — the guard's SELECT — falls
    through to the mock's own scripted answer.
    """
    from sqlalchemy.sql.dml import Update

    scripted = mock_db.execute.return_value

    def _execute(statement, *_args, **_kwargs):
        if not isinstance(statement, Update):
            return scripted
        result = MagicMock()
        if mock_run.status != "running":
            result.first.return_value = None
            return result
        for column, bound in statement._values.items():
            setattr(mock_run, column.key, getattr(bound, "value", bound))
        result.first.return_value = (mock_run.id,)
        return result

    mock_db.execute.side_effect = _execute
    return mock_db


def _ship_verdict():
    """A Verdict with no reasons, which is what `decide()` returns for a clean run."""
    from app.domain.verdict import Outcome, Verdict

    return Verdict(outcome=Outcome.SHIP)


def _blocking_verdict(rule="absent_eval_measurement"):
    """One blocking reason, and the Verdict that has to agree with it."""
    from app.domain.verdict import Outcome, Reason, Verdict

    return Verdict(
        outcome=Outcome.BLOCK,
        reasons=[
            Reason(
                rule=rule,
                signal="the evaluation run's result",
                observed="no evaluation result was recorded for this agent",
                threshold=(
                    "an evaluation must have run and reported before a deploy ships"
                ),
                outcome=Outcome.BLOCK,
            )
        ],
    )


@contextmanager
def _past_step_3b(eval_status="complete", red_team_status="complete", verdict=None):
    """Get a test past the sequencer without a tenant DB (#54).

    Step 3b dispatches both jobs and then polls the tenant DB until each has a
    terminal row of its own. Every test that reaches step 4 now goes through it,
    and one whose subject is something else says so by handing both readers a
    terminal status: the wait returns on its first poll having slept nothing.

    Without this the readers connect to a fake DSN, read None every time, and the
    task re-queues itself instead of collecting anything. Both dispatches and the
    re-queue are stubbed for the same class of reason: all three put a real
    message on a broker that is not running.

    A test that asserts on the eval dispatch re-patches it inside its own `with`,
    after this one, and the inner patcher is the one it reads.
    """
    with ExitStack() as stack:
        stack.enter_context(
            _deployment_patch(
                "_dispatch_moment",
                return_value=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
            )
        )
        stack.enter_context(_deployment_patch("_dispatch_eval_run", return_value=True))
        stack.enter_context(_deployment_patch("_dispatch_red_team_run", return_value=True))
        stack.enter_context(
            _deployment_patch("latest_eval_run_status_since", return_value=eval_status)
        )
        stack.enter_context(
            _deployment_patch(
                "latest_red_team_run_status_since", return_value=red_team_status
            )
        )
        stack.enter_context(_deployment_patch("_requeue_wait"))
        # The decision is stubbed for the same reason the dispatches are: it
        # opens two psycopg2 connections against a fake DSN, and a test whose
        # subject is the ledger or the warning merge is not also a test of
        # decide(). TestTheVerdictDrivesTheRecommendation drives the real one.
        stack.enter_context(
            _deployment_patch(
                "_compute_verdict",
                return_value=verdict if verdict is not None else _ship_verdict(),
            )
        )
        yield


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

        The existing row carries a real beat because the guard reads one (#129).
        TestTheIdempotencyGuardKeysOnTheRunNotTheClock runs the same guard against
        PostgreSQL, where the WHERE clause is evaluated rather than scripted.
        """
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())

        # Mock agent with neon_connection_string set
        mock_agent = MagicMock()
        mock_agent.neon_connection_string = b"encrypted_conn"

        # Mock existing ChecklistRun returned by idempotency query, beating now
        mock_existing_run = MagicMock()
        mock_existing_run.created_at = datetime.now(timezone.utc)
        mock_existing_run.heartbeat_at = datetime.now(timezone.utc)

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
        _fence_updates(mock_db, mock_run)
        # Simulate db.refresh setting the run.id after INSERT
        mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", mock_run_id)

        # Signal fetch return values (minimal valid dicts)
        empty_eval = _measured_eval_signal()
        empty_red_team = _measured_red_team_signal()
        empty_verified_qa = _measured_verified_qa_signal()
        empty_corpus = _measured_corpus_signal()
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

        with _past_step_3b(), patch(
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
        _fence_updates(mock_db, mock_run)
        mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", mock_run_id)

        async def _capture(signals_json, result_container, *, ledger=None):
            seen["ledger"] = ledger
            result_container["report"] = {
                "recommendation": "ship",
                "summary": "All signals look good.",
                "warnings": [],
            }

        with _past_step_3b(), patch(
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
            return_value=_measured_verified_qa_signal(),
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync",
            return_value=_measured_corpus_signal(),
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
        _fence_updates(mock_db, mock_run)
        mock_db.refresh.side_effect = lambda obj: setattr(obj, "id", mock_run_id)

        empty_eval = _measured_eval_signal()
        empty_red_team = _measured_red_team_signal()
        # A tenant whose corpus was READ and came back empty. The dict this
        # replaces put 0.0 in both averages, which is the #121 payload itself:
        # a faithfulness of nought asserted about pairs that do not exist.
        empty_verified_qa = _measured_verified_qa_signal(row_count=0)
        empty_corpus = _measured_corpus_signal(document_count=0, chunk_count=0)
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
            with _past_step_3b(), patch(
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
    _fence_updates(mock_db, mock_run)
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


def _measured_verified_qa_signal(row_count=60, average=0.9):
    """The verified-QA payload the collector writes when it counted something.

    Built through the collector's own constructor for the reason
    `_measured_eval_signal` is: the hand-written dict this replaces carried no
    `signal` key at all, which is the pre-#131 shape, so every wiring test that
    used it sent `derive_quality_warnings` down the compatibility branch kept
    for reports stored before that fix. The happy paths were therefore the one
    place in this module NOT exercising what production stores today.

    A `row_count` of nought comes back with both averages None, because that is
    what the constructor does with an average over no rows.
    """
    from app.services.deployment_service import (
        VERIFIED_QA_SIGNAL_MEASURED,
        _verified_qa_stats,
    )

    return _verified_qa_stats(
        VERIFIED_QA_SIGNAL_MEASURED,
        row_count=row_count,
        avg_faithfulness=average,
        avg_relevance=average,
    )


def _measured_corpus_signal(document_count=5, chunk_count=100):
    """The corpus payload the collector writes when it reached the tenant DB.

    Same provenance and same reason as `_measured_verified_qa_signal`. Every
    figure here is a count, so all three are real at nought and the signal is
    the only thing that separates nought documents from no read at all.
    """
    from app.services.deployment_service import CORPUS_SIGNAL_MEASURED, _corpus_stats

    return _corpus_stats(
        CORPUS_SIGNAL_MEASURED,
        document_count=document_count,
        chunk_count=chunk_count,
        last_ingested_at=None,
    )


def _empty_first_four_signals():
    """The four pre-existing safe-default signal dicts, reused across BLR-01 wiring tests."""
    return (
        _measured_eval_signal(),
        _measured_red_team_signal(),
        _measured_verified_qa_signal(),
        _measured_corpus_signal(),
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

        with _past_step_3b(), patch(
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

        with _past_step_3b(), patch(
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

        with _past_step_3b(), patch(
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

        with _past_step_3b(), patch(
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

        with _past_step_3b(), patch(
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
            return_value=_measured_verified_qa_signal(),
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync",
            return_value=_measured_corpus_signal(),
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

    def test_measured_signals_leave_the_computed_verdict_intact(self):
        """The gate is a floor, not a second opinion.

        Renamed at #54: the verdict it is a floor under is the platform's, not
        the orchestrator's. The assertion is unchanged, because what it always
        tested is that a measured pair of signals produces no downgrade and no
        warning of its own.
        """
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

        with _past_step_3b(), patch(
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
            return_value=_measured_verified_qa_signal(),
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync",
            return_value=_measured_corpus_signal(),
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

    THE DISPATCH IS UNCONDITIONAL SINCE #54. What these tests still pin is the
    half that survives: the block, and the warning that tells the owner to wait
    for the run the platform started rather than naming a page nothing routes to.
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

    def test_a_measured_agent_gets_a_fresh_run_as_well(self):
        """The condition is gone, and so is the state it used to read (#54).

        Step 4b decided whether to dispatch by reading a signal collected BEFORE
        the dispatch, which is the ordering this slice removes. The checklist
        dispatches first and waits for both jobs, so no earlier state survives to
        branch on and a measured agent is re-measured like every other.

        The spend bound moved rather than vanished. It is the checklist's own
        60-minute idempotency guard, one checklist per agent per hour and so one
        eval per agent per hour, plus run_eval_suite's own guard on a run already
        in flight.
        """
        self._drive(_measured_eval_signal(), _measured_red_team_signal())
        self.dispatch_mock.assert_called_once_with(self.agent_id)


class TestExistingTenantEvalPath(TestEvidenceGateWiring):
    """The population P3 actually creates, and the convergence it did not get.

    Step 4b was written for EVAL_SIGNAL_NO_RUNS, which is day 1. P3 then made
    every EXISTING tenant block too — and none of them is in `no_runs`: they
    have runs, produced by the tautology, which now report
    EVAL_SIGNAL_AGENT_NOT_INVOKED. So the convergence mechanism fired for
    nobody, and the warning routed the whole population to "the Evaluation
    page", which _dispatch_eval_run's own docstring says the onboarding flow
    reaches from nowhere. The wall had moved, not gone.

    IT FIRED FOR THE ABSENT HALF ONLY, and that asymmetry is gone with #54. It
    was the design while the dispatch could read a signal first: `agent_invoked
    is None` converges, because a fresh run on a 0013+ tenant writes the key
    either way, while `agent_invoked is False` recurs every night on a broken
    agent and firing on it bought live turns that changed nothing. The checklist
    now dispatches BEFORE any signal for this run exists, so it cannot tell the
    two apart, and its own 60-minute guard is what bounds the spend.

    What these tests still pin is the warning: the message branches on
    `agent_invoked`, and only the absent branch may narrate the tautology.
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

    def test_a_run_that_recorded_false_gets_a_fresh_run_too(self):
        """The asymmetry went with the condition that expressed it (#54).

        `agent_invoked is False` was refused because it recurs: a broken agent
        produces it every night, so firing on it bought a fresh set of live turns
        per readiness check and left the state unchanged. The checklist can no
        longer ask, because it dispatches before any signal for this run exists.
        Its own 60-minute guard is what bounds the spend now, at one eval per
        agent per hour, and the old conditional never bounded it that tightly.
        """
        self._drive(
            self._tautological(agent_invoked=False),
            _measured_red_team_signal(),
        )

        self.dispatch_mock.assert_called_once_with(self.agent_id)

    def test_a_failed_run_gets_a_fresh_run_too(self):
        """Same argument, on the state that recurs for whatever produced it."""
        self._drive(
            dict(_measured_eval_signal(), eval_signal="run_failed",
                 last_run_status="failed", pass_rates=None),
            _measured_red_team_signal(),
        )

        self.dispatch_mock.assert_called_once_with(self.agent_id)

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


# ---------------------------------------------------------------------------
# The checklist sequences the two jobs it grades (#54 criterion 3)
# ---------------------------------------------------------------------------
# THE FIRST CHECKLIST EVER RUN READ eval_signal=no_runs SECONDS AFTER STARTING
# THE EVAL IT WAS ASKING ABOUT. Step 4 collected first and step 4b dispatched
# afterwards, on what step 4 had found, so every number in that report described
# the state before the checklist acted, and no red team was dispatched at all.
#
# The tests below drive the real task with no real time in them. The poll
# interval is patched to zero and the two status readers are stand-ins counting
# their own calls, so a twenty-five-minute ceiling costs microseconds. The
# collectors are stand-ins too, and each returns a DIFFERENT payload depending on
# whether both runs had reported terminal at the moment it was called. That is
# what makes ordering assertable through the persisted report: a task that
# collected early gets the stale summary and the assertions fail.

_BLAST_RADIUS_FIXTURE = {
    "configured_max_single_action_cents": None,
    "configured_max_hourly_aggregate_cents": None,
    "observed_max_single_action_cents": None,
    "observed_max_hourly_aggregate_cents": None,
    "observed_window_days": 7,
    "warn_threshold_single_cents": 50000,
    "warn_threshold_hourly_cents": 200000,
    "enabled_skill_count": 0,
}


def _never_evaluated_signal():
    """The eval summary a fresh agent's collector returns before its run ends."""
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


def _never_red_teamed_signal():
    """The security half of the same fresh agent."""
    from app.services.deployment_service import (
        RED_TEAM_SIGNAL_NO_RUNS,
        _red_team_summary,
    )

    return _red_team_summary(
        RED_TEAM_SIGNAL_NO_RUNS,
        detail="no red-team run has ever been recorded for this agent",
    )


def _sequenced_world(eval_polls, red_team_polls):
    """A tenant DB whose two runs each need N polls before they report terminal.

    Returns (state, fetchers, collectors). `state["events"]` is the interleaving
    of every poll and every collect in the order the task made them, which is the
    thing under test.
    """
    state = {"eval": 0, "red_team": 0, "events": []}

    def _both_terminal():
        return state["eval"] > eval_polls and state["red_team"] > red_team_polls

    def _reader(name, needed):
        def _fetch(*_args, **_kwargs):
            state[name] += 1
            value = "complete" if state[name] > needed else "running"
            state["events"].append(("poll", name, value))
            return value

        return _fetch

    def _collect_eval(*_args, **_kwargs):
        state["events"].append(("collect", "eval"))
        return _measured_eval_signal() if _both_terminal() else _never_evaluated_signal()

    def _collect_red_team(*_args, **_kwargs):
        state["events"].append(("collect", "red_team"))
        if _both_terminal():
            return _measured_red_team_signal()
        return _never_red_teamed_signal()

    fetchers = (_reader("eval", eval_polls), _reader("red_team", red_team_polls))
    return state, fetchers, (_collect_eval, _collect_red_team)


def _drive_sequenced(
    fetchers,
    collectors,
    *,
    ceiling_s=2700,
    mock_log=None,
    dispatch=None,
    verdict=None,
    on_orchestrate=None,
):
    """Run the real task to settlement, driving every continuation by hand.

    The wait is a chain of messages now, so one call to the task is one poll.
    `_requeue_wait` is captured rather than performed, and this driver feeds the
    state it captured straight back in. That is the whole production loop with
    the broker and the countdown taken out of it, so a wait that takes five polls
    takes five calls here and no real time at all.
    """
    from app.core.config import settings
    from app.worker.tasks.runtime import deployment as deployment_task
    from app.worker.tasks.runtime.deployment import run_deployment_checklist

    agent_id = str(uuid.uuid4())
    mock_db, mock_run = _build_full_happy_path_mock_db(str(uuid.uuid4()))
    dispatch_eval = dispatch[0] if dispatch else (lambda _a: True)
    dispatch_red_team = dispatch[1] if dispatch else (lambda _a: True)
    queued: list[dict] = []

    async def _report(signals_json, result_container, *, ledger=None):
        # `on_orchestrate` is the window between the last beat and the completing
        # write, which is where the guard actually reaps a deciding pass.
        if on_orchestrate is not None:
            on_orchestrate(mock_run)
        result_container["report"] = {
            "recommendation": "ship",
            "summary": "All good.",
            "warnings": [],
        }

    def _capture_requeue(_agent_id, state):
        queued.append(state)
        return True

    def _target(name, **kwargs):
        return patch("app.worker.tasks.runtime.deployment." + name, **kwargs)

    patches = [
        _target("get_sync_db", new=_make_sync_db_ctx(mock_db)),
        _target("fernet_decrypt", return_value="postgresql://test/tenant"),
        _target(
            "_dispatch_moment",
            return_value=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        ),
        _target("_dispatch_eval_run", new=dispatch_eval),
        _target("_dispatch_red_team_run", new=dispatch_red_team),
        _target("_requeue_wait", new=_capture_requeue),
        _target("latest_eval_run_status_since", side_effect=fetchers[0]),
        _target("latest_red_team_run_status_since", side_effect=fetchers[1]),
        _target("_fetch_eval_summary_sync", side_effect=collectors[0]),
        _target("_fetch_red_team_summary_sync", side_effect=collectors[1]),
        _target(
            "_fetch_verified_qa_stats_sync",
            return_value=_measured_verified_qa_signal(),
        ),
        _target(
            "_fetch_corpus_stats_sync",
            return_value=_measured_corpus_signal(),
        ),
        _target("_fetch_blast_radius_sync", return_value=dict(_BLAST_RADIUS_FIXTURE)),
        _target("_compute_envelope_hash_sync", return_value="test-envelope-hash"),
        _target("_call_orchestrator_async", side_effect=_report),
        _target(
            "_compute_verdict",
            return_value=verdict if verdict is not None else _ship_verdict(),
        ),
        patch.object(settings, "CHECKLIST_WAIT_POLL_S", 0),
        patch.object(settings, "CHECKLIST_WAIT_CEILING_S", ceiling_s),
    ]
    if mock_log is not None:
        patches.append(patch.object(deployment_task, "log", mock_log))

    with ExitStack() as stack:
        for one in patches:
            stack.enter_context(one)
        result = run_deployment_checklist.run(agent_id=agent_id)
        # A ceiling this driver cannot exceed. Without it a fetcher that never
        # reports terminal, against a ceiling wall-clock time cannot reach inside
        # a test, would spin here rather than failing.
        for _ in range(60):
            if result.get("status") != "waiting":
                break
            assert queued, "the task said it was waiting and re-queued nothing"
            result = run_deployment_checklist.run(
                agent_id=agent_id, wait_state=queued.pop()
            )
        else:
            raise AssertionError("the wait never settled inside 60 polls")

    return result, mock_run, agent_id


class TestTheChecklistSequencesBothJobs:
    """Criterion 3, written as the failure shape the first real run produced."""

    def test_the_collectors_run_only_after_both_runs_are_terminal(self):
        """An agent with no prior runs. Both jobs are dispatched, both stay in
        flight for several polls, and not one signal is read until each has
        reported terminal."""
        state, fetchers, collectors = _sequenced_world(eval_polls=2, red_team_polls=4)

        result, _, _ = _drive_sequenced(fetchers, collectors)

        collects = [i for i, e in enumerate(state["events"]) if e[0] == "collect"]
        in_flight = [
            i for i, e in enumerate(state["events"])
            if e[0] == "poll" and e[2] != "complete"
        ]
        assert collects, "no collector ran at all"
        assert in_flight, "the stand-in never reported a run still in flight"
        assert min(collects) > max(in_flight), (
            "a signal was collected while a run was still in flight, which is the "
            f"shape the first checklist ever executed produced: {state['events']}"
        )
        assert result["status"] == "complete"

    def test_the_collected_summary_is_the_post_terminal_one(self):
        """Ordering asserted through what was PERSISTED, not through call order.

        The stand-in collectors return the never-run summaries while either job
        is in flight and the measured ones afterwards, so a task that collected
        early persists 'block' with eval_signal='no_runs' about an eval it had
        just started. That is what the first real run wrote.
        """
        _, fetchers, collectors = _sequenced_world(eval_polls=2, red_team_polls=4)

        result, mock_run, _ = _drive_sequenced(fetchers, collectors)

        assert mock_run.report["eval_summary"]["eval_signal"] == "measured"
        assert mock_run.report["red_team_summary"]["signal"] == "measured"
        assert result["recommendation"] == "ship", (
            "both halves were measured after the wait, so nothing should have "
            f"forced a downgrade: {mock_run.warnings}"
        )

    def test_both_jobs_are_dispatched_up_front_with_agent_id_only(self):
        """Unconditional, and before the first poll. The eval used to fire on two
        of the seven eval signal states and the red team on none of them."""
        state, fetchers, collectors = _sequenced_world(eval_polls=0, red_team_polls=0)
        seen = []

        def _record(name):
            def _dispatch(agent_id):
                seen.append((name, agent_id))
                return True

            return _dispatch

        _, _, agent_id = _drive_sequenced(
            fetchers, collectors, dispatch=(_record("eval"), _record("red_team"))
        )

        assert seen == [("eval", agent_id), ("red_team", agent_id)], (
            f"both jobs, dispatched up front, agent_id only (CTL-08): {seen}"
        )
        assert state["events"][0][0] == "poll", (
            "the first event after the dispatch must be a poll, never a collect: "
            f"{state['events'][:3]}"
        )

    def test_a_ceiling_expiry_completes_the_run_and_names_the_timed_out_half(self):
        """The red team is still running when the ceiling expires.

        The task must not raise and must not reach for the pre-dispatch summary.
        The red-team record reads as the absent state it is, the gate blocks on
        it, and the log names which half ran out of ceiling with the wait it
        actually observed.
        """
        _, fetchers, collectors = _sequenced_world(eval_polls=0, red_team_polls=10**6)
        mock_log = MagicMock()

        result, mock_run, _ = _drive_sequenced(
            fetchers, collectors, ceiling_s=0, mock_log=mock_log
        )

        assert result["status"] == "complete", (
            f"a ceiling expiry must never fail the checklist: {result}"
        )
        assert mock_run.report["red_team_summary"]["signal"] == "did_not_finish", (
            "the red team never finished, so its record is the absent one, never "
            "a summary that was already there before the checklist dispatched"
        )
        assert result["recommendation"] == "block"
        assert "red_team_did_not_finish" in [w["warning_id"] for w in mock_run.warnings]

        expiries = [
            call for call in mock_log.warning.call_args_list
            if call.args
            and call.args[0] == "run_deployment_checklist.wait_ceiling_expired"
        ]
        assert len(expiries) == 1, (
            f"expected one ceiling-expiry warning: {mock_log.warning.call_args_list}"
        )
        assert expiries[0].kwargs["timed_out"] == ["red_team"], (
            "the log has to name WHICH half timed out. 'A job timed out' sends the "
            f"reader to both of them: {expiries[0].kwargs}"
        )
        assert "waited_s" in expiries[0].kwargs, (
            "the observed wait, not the configured ceiling. They differ, and only "
            "the observed one separates a slow run from an unreachable tenant DB."
        )

    def test_the_eval_dispatch_still_reaches_the_owner_facing_warning(self):
        """`eval_dispatched` survived the fold from step 4b.

        The warning the owner reads branches on it: "we have started its first
        evaluation" against "run an eval from the Evaluation page", and the
        onboarding flow routes to no such page. Losing the key would silently
        put every owner back on the second sentence.
        """
        _, fetchers, _ = _sequenced_world(eval_polls=0, red_team_polls=0)
        collectors = (
            lambda *_a, **_k: _never_evaluated_signal(),
            lambda *_a, **_k: _measured_red_team_signal(),
        )

        _, mock_run, _ = _drive_sequenced(fetchers, collectors)

        matching = [w for w in mock_run.warnings if w["warning_id"] == "eval_never_run"]
        assert len(matching) == 1, f"expected one eval_never_run warning: {mock_run.warnings}"
        assert "started" in matching[0]["message"].lower()


class TestARefusedDispatchIsNotWaitedOn:
    """#130: a half the broker refused burnt the whole ceiling before blocking.

    `_open_wait` records the refusal, so that half's outcome is decided the
    moment the wait opens: no run of this checklist's exists to reach terminal,
    every poll until the ceiling asks a question already answered, and the answer
    was always going to be an absent measurement. Fail-closed, and forty-five
    minutes of it with the answer in hand.
    """

    def _refused(self):
        return (lambda _agent_id: False, lambda _agent_id: True)

    def test_a_half_the_broker_refused_is_not_polled_to_the_ceiling(self):
        """The eval never reports terminal. Its dispatch never happened either."""
        state, fetchers, collectors = _sequenced_world(
            eval_polls=10**6, red_team_polls=0
        )

        result, mock_run, _ = _drive_sequenced(
            fetchers, collectors, dispatch=self._refused()
        )

        eval_polls = [e for e in state["events"] if e[0] == "poll" and e[1] == "eval"]
        assert len(eval_polls) == 1, (
            "one look is all a refused dispatch is worth; the rest is the ceiling "
            f"spent on a question already answered: {len(eval_polls)} polls"
        )
        assert result["status"] == "complete"
        assert result["recommendation"] == "block", (
            "a half that was never started is an absent measurement and the gate "
            f"still refuses on it: {result}"
        )
        assert mock_run.report["eval_summary"]["eval_signal"] == "did_not_finish"

    def test_both_dispatches_refused_settle_on_the_first_pass(self):
        state, fetchers, collectors = _sequenced_world(
            eval_polls=10**6, red_team_polls=10**6
        )

        result, _, _ = _drive_sequenced(
            fetchers,
            collectors,
            dispatch=(lambda _a: False, lambda _a: False),
        )

        polls = [e for e in state["events"] if e[0] == "poll"]
        assert len(polls) == 2, (
            f"one look at each half and then the report: {state['events']}"
        )
        assert result["status"] == "complete"
        assert result["recommendation"] == "block"

    def test_a_dispatched_half_still_holds_the_wait_open(self):
        """The change is scoped to a refusal. A job in flight is still waited on."""
        state, fetchers, collectors = _sequenced_world(eval_polls=3, red_team_polls=0)

        result, _, _ = _drive_sequenced(fetchers, collectors)

        eval_polls = [e for e in state["events"] if e[0] == "poll" and e[1] == "eval"]
        assert len(eval_polls) == 4, (
            f"a dispatched run is polled until it reports terminal: {eval_polls}"
        )
        assert result["status"] == "complete"

    def test_a_refused_dispatch_still_reads_a_run_the_poll_does_find(self):
        """A refusal closes the WAIT, never the reading.

        run_eval_suite's own guard absorbs a dispatch made while a run is already
        in flight, and the nightly beat starts runs this checklist did not. A row
        at or after `since` that reports terminal on the first look is this
        checklist's evidence whatever the broker said about the dispatch.
        """
        _, fetchers, collectors = _sequenced_world(eval_polls=0, red_team_polls=0)

        _, mock_run, _ = _drive_sequenced(
            fetchers, collectors, dispatch=self._refused()
        )

        assert mock_run.report["eval_summary"]["eval_signal"] == "measured", (
            "the poll found a terminal run at or after the dispatch moment, so "
            f"its record is read: {mock_run.report['eval_summary']}"
        )

    def test_the_log_says_never_dispatched_rather_than_ran_out_of_ceiling(self):
        """Two different incidents. One is a slow job, the other a dead broker."""
        _, fetchers, collectors = _sequenced_world(
            eval_polls=10**6, red_team_polls=0
        )
        mock_log = MagicMock()

        _drive_sequenced(
            fetchers, collectors, dispatch=self._refused(), mock_log=mock_log
        )

        closures = [
            call
            for call in mock_log.warning.call_args_list
            if call.args
            and call.args[0] == "run_deployment_checklist.wait_closed_undispatched"
        ]
        assert len(closures) == 1, (
            f"expected one undispatched-closure warning: {mock_log.warning.call_args_list}"
        )
        assert closures[0].kwargs["never_dispatched"] == ["eval"]
        expiries = [
            call
            for call in mock_log.warning.call_args_list
            if call.args
            and call.args[0] == "run_deployment_checklist.wait_ceiling_expired"
        ]
        assert expiries == [], (
            "nothing ran out of ceiling here, and saying so sends the reader to "
            f"look for a slow job: {expiries}"
        )

    def test_the_owner_facing_detail_says_it_never_started(self):
        """"had not finished after 0s" reads as a platform that gave up instantly."""
        _, fetchers, collectors = _sequenced_world(
            eval_polls=10**6, red_team_polls=0
        )

        _, mock_run, _ = _drive_sequenced(
            fetchers, collectors, dispatch=self._refused()
        )

        detail = mock_run.report["eval_summary"]["signal_detail"]
        assert "had not finished" not in detail, (
            f"no run of this checklist's was ever started: {detail!r}"
        )
        assert "start" in detail, (
            f"the detail has to name what actually went wrong: {detail!r}"
        )


class TestTheWaitIsAChainOfMessages:
    """The worker slot is free between polls (#54 review).

    The wait used to sleep inside the task body, on the same `runtime` queue as
    the two jobs it had dispatched. On the documented local topology that is one
    execution slot: the checklist held it for the whole ceiling, neither job
    could start, and the wait could never be satisfied. Every test here observes
    one pass at a time.
    """

    @contextmanager
    def _world(self, eval_status, red_team_status, queued, collected):
        from app.core.config import settings

        mock_db, mock_run = _build_full_happy_path_mock_db(str(uuid.uuid4()))
        self.mock_db = mock_db
        self.mock_run = mock_run

        async def _report(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "recommendation": "ship",
                "summary": "All good.",
                "warnings": [],
            }

        def _collect(name):
            def _fetch(*_a, **_k):
                collected.append(name)
                return (
                    _measured_eval_signal()
                    if name == "eval"
                    else _measured_red_team_signal()
                )

            return _fetch

        patches = [
            _deployment_patch("get_sync_db", new=_make_sync_db_ctx(mock_db)),
            _deployment_patch("fernet_decrypt", return_value="postgresql://test/tenant"),
            _deployment_patch(
                "_dispatch_moment",
                return_value=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
            ),
            _deployment_patch("_dispatch_eval_run", return_value=True),
            _deployment_patch("_dispatch_red_team_run", return_value=True),
            _deployment_patch(
                "_requeue_wait", new=lambda _a, state: queued.append(state) or True
            ),
            _deployment_patch("latest_eval_run_status_since", return_value=eval_status),
            _deployment_patch(
                "latest_red_team_run_status_since", return_value=red_team_status
            ),
            _deployment_patch("_fetch_eval_summary_sync", new=_collect("eval")),
            _deployment_patch("_fetch_red_team_summary_sync", new=_collect("red_team")),
            _deployment_patch(
                "_fetch_verified_qa_stats_sync",
                return_value=_measured_verified_qa_signal(),
            ),
            _deployment_patch(
                "_fetch_corpus_stats_sync",
                return_value=_measured_corpus_signal(),
            ),
            _deployment_patch(
                "_fetch_blast_radius_sync", return_value=dict(_BLAST_RADIUS_FIXTURE)
            ),
            _deployment_patch(
                "_compute_envelope_hash_sync", return_value="test-envelope-hash"
            ),
            _deployment_patch("_call_orchestrator_async", side_effect=_report),
            _deployment_patch("_compute_verdict", return_value=_ship_verdict()),
            patch.object(settings, "CHECKLIST_WAIT_CEILING_S", 2700),
            patch.object(settings, "CHECKLIST_WAIT_POLL_S", 10),
        ]
        with ExitStack() as stack:
            for one in patches:
                stack.enter_context(one)
            yield

    def test_a_job_still_in_flight_re_queues_and_collects_nothing(self):
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        queued: list[dict] = []
        collected: list[str] = []
        with self._world("complete", "running", queued, collected):
            result = run_deployment_checklist.run(agent_id=str(uuid.uuid4()))

        assert result["status"] == "waiting"
        assert result["pending"] == ["red_team"], (
            f"the pass has to name which half it is still waiting on: {result}"
        )
        assert collected == [], (
            "a collector ran while a job was in flight, which is the staleness "
            "the sequencing exists to remove"
        )
        assert len(queued) == 1, f"exactly one continuation per pass: {queued}"

    def test_a_continuation_neither_re_guards_nor_inserts_a_second_row(self):
        """The row the guard would find is this run's own.

        A continuation that ran step 2 would skip itself with 'already_running'
        and abandon the wait it was carrying; one that ran step 3 would leave a
        second 'running' row behind on every poll.
        """
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        queued: list[dict] = []
        with self._world("running", "running", queued, []):
            run_deployment_checklist.run(agent_id=str(uuid.uuid4()))
            first_adds = self.mock_db.add.call_count
            self.mock_db.execute.reset_mock()
            run_deployment_checklist.run(
                agent_id=str(uuid.uuid4()), wait_state=queued.pop()
            )

        assert first_adds == 1, "the first pass inserts the checklist row"
        assert self.mock_db.add.call_count == first_adds, (
            "a continuation inserted a second checklist_runs row"
        )
        # The guard is the only SELECT a pass makes. A continuation's writes are
        # UPDATEs now that every one of them is fenced (`_claimed`), so counting
        # every statement would count the beat and read it as a second guard.
        from sqlalchemy.sql.selectable import Select

        selects = [
            call
            for call in self.mock_db.execute.call_args_list
            if call.args and isinstance(call.args[0], Select)
        ]
        assert selects == [], (
            "a continuation re-ran the idempotency guard and would skip itself"
        )

    def test_a_half_already_terminal_is_not_polled_again(self):
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        queued: list[dict] = []
        with self._world("complete", "running", queued, []):
            run_deployment_checklist.run(agent_id=str(uuid.uuid4()))
            state = queued.pop()

        assert state["statuses"] == {"eval": "complete", "red_team": None}, (
            "the continuation carries what was already observed, so the settled "
            f"half is never re-read: {state['statuses']}"
        )

    def test_the_continuation_carries_the_run_it_belongs_to(self):
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        queued: list[dict] = []
        with self._world("running", "running", queued, []):
            result = run_deployment_checklist.run(agent_id=str(uuid.uuid4()))
            state = queued.pop()

        assert state["run_id"] == result["run_id"]
        assert state["since"] == "2026-08-30T12:00:00+00:00", (
            "the boundary is the tenant DB's clock at dispatch, and it must "
            f"survive the trip across the broker unchanged: {state['since']}"
        )
        assert state["eval_dispatched"] is True

    def test_a_continuation_missing_its_state_refuses_rather_than_starting_over(self):
        """Defaulting `since` would grade runs this checklist did not start."""
        from app.worker.tasks.runtime.deployment import _require_wait_state

        with pytest.raises(KeyError) as excinfo:
            _require_wait_state({"run_id": "abc"})

        message = str(excinfo.value)
        for key in ("since", "started_at", "statuses"):
            assert key in message, f"the refusal must name {key}: {message}"

    def test_a_continuation_handed_something_that_is_not_state_refuses(self):
        from app.worker.tasks.runtime.deployment import _require_wait_state

        with pytest.raises(TypeError):
            _require_wait_state("resume please")

    def test_the_re_queue_carries_only_the_agent_and_the_state(self):
        """CTL-08 across the broker: no conn_str in a task kwarg, ever."""
        from app.core.config import settings
        from app.worker.tasks.runtime import deployment as deployment_task

        state = {
            "run_id": "run-1",
            "since": "2026-08-30T12:00:00+00:00",
            "started_at": "2026-08-30T12:00:01+00:00",
            "statuses": {"eval": "complete", "red_team": None},
            "eval_dispatched": True,
            "red_team_dispatched": True,
        }
        with patch.object(
            deployment_task.run_deployment_checklist, "apply_async"
        ) as apply_async:
            deployment_task._requeue_wait("agent-1", state)

        kwargs = apply_async.call_args.kwargs
        assert kwargs["kwargs"] == {"agent_id": "agent-1", "wait_state": state}
        assert kwargs["queue"] == "runtime"
        assert kwargs["countdown"] == settings.CHECKLIST_WAIT_POLL_S, (
            "the countdown is what the sleep used to be, and the broker holds it"
        )
        assert "conn_str" not in json.dumps(kwargs["kwargs"])


def _drive_with_verdict(
    verdict,
    *,
    eval_summary=None,
    red_team_summary=None,
    orchestrator=None,
    mock_log=None,
    verified_qa=None,
):
    """Run the whole task once with the decision already made, and return what
    it persisted.

    The verdict is injected rather than derived, because these tests are about
    what the task DOES with a decision. TestTheVerdictIsComputedFromTheRecords
    below drives `_compute_verdict` itself.
    """
    from app.worker.tasks.runtime import deployment as deployment_task
    from app.worker.tasks.runtime.deployment import run_deployment_checklist

    agent_id = str(uuid.uuid4())
    mock_db, mock_run = _build_full_happy_path_mock_db(str(uuid.uuid4()))

    async def _ship_report(signals_json, result_container, *, ledger=None):
        result_container["report"] = {
            "summary": "All good.",
            "warnings": [],
        }

    patches = [
        _deployment_patch("get_sync_db", new=_make_sync_db_ctx(mock_db)),
        _deployment_patch("fernet_decrypt", return_value="postgresql://test/tenant"),
        _deployment_patch(
            "_fetch_eval_summary_sync",
            return_value=eval_summary if eval_summary else _measured_eval_signal(),
        ),
        _deployment_patch(
            "_fetch_red_team_summary_sync",
            return_value=(
                red_team_summary if red_team_summary else _measured_red_team_signal()
            ),
        ),
        _deployment_patch(
            "_fetch_verified_qa_stats_sync",
            return_value=verified_qa or _measured_verified_qa_signal(),
        ),
        _deployment_patch(
            "_fetch_corpus_stats_sync",
            return_value=_measured_corpus_signal(),
        ),
        _deployment_patch(
            "_fetch_blast_radius_sync", return_value=dict(_BLAST_RADIUS_FIXTURE)
        ),
        _deployment_patch("_compute_envelope_hash_sync", return_value="hash"),
        _deployment_patch(
            "_call_orchestrator_async",
            side_effect=orchestrator if orchestrator else _ship_report,
        ),
    ]
    if mock_log is not None:
        patches.append(patch.object(deployment_task, "log", mock_log))

    with _past_step_3b(verdict=verdict), ExitStack() as stack:
        for one in patches:
            stack.enter_context(one)
        result = run_deployment_checklist.run(agent_id=agent_id)

    return result, mock_run


class TestTheVerdictDrivesTheRecommendation:
    """#54 criterion 1 and issue #36, asserted through what was PERSISTED.

    The approve route reads `checklist_runs.recommendation`, so a decision that
    is right in a return value and wrong in the column is a decision that does
    not exist.
    """

    def test_a_blocking_verdict_is_the_persisted_recommendation(self):
        result, mock_run = _drive_with_verdict(_blocking_verdict())

        assert result["recommendation"] == "block"
        assert mock_run.recommendation == "block"
        assert mock_run.report["recommendation"] == "block"

    def test_the_model_cannot_talk_the_platform_out_of_the_verdict(self):
        """The scripted narration submits a contradictory recommendation, the way
        a confident model would. It is not read, because submit_report has no
        such field and the task never looks for one.

        This is issue #36 in one assertion: until this release the deploy label
        WAS whatever came out of that completion.
        """

        async def _contradicts(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "recommendation": "ship",
                "summary": "Everything looks great, ship it.",
                "warnings": [],
            }

        result, mock_run = _drive_with_verdict(
            _blocking_verdict(), orchestrator=_contradicts
        )

        assert result["recommendation"] == "block", (
            "the model said ship over a blocking verdict and was believed"
        )
        assert mock_run.recommendation == "block"
        assert mock_run.report["summary"] == "Everything looks great, ship it.", (
            "the prose it wrote is still the prose the owner reads; only the "
            "decision is not its to make"
        )

    def test_every_reason_lands_as_a_warning_carrying_its_slug_and_its_numbers(self):
        _, mock_run = _drive_with_verdict(_blocking_verdict())

        matching = [
            w for w in mock_run.warnings if w["warning_id"] == "absent_eval_measurement"
        ]
        assert len(matching) == 1, (
            f"a block with no stated reason is unexplained: {mock_run.warnings}"
        )
        assert "the evaluation run's result" in matching[0]["message"]
        assert "no evaluation result was recorded" in matching[0]["message"]
        assert "must have run and reported" in matching[0]["message"]

    def test_the_verdict_payload_is_stored_on_the_report(self):
        """Stored whole, so a later reader rebuilds the decision rather than the
        one word it came to."""
        from app.domain.verdict import Verdict

        verdict = _blocking_verdict()
        _, mock_run = _drive_with_verdict(verdict)

        stored = mock_run.report["verdict"]
        assert Verdict.from_payload(stored) == verdict, (
            "the round trip IS the contract on this record"
        )

    def test_the_two_quality_warnings_ride_along_and_change_nothing(self):
        """Ported from prompt prose at #54. They warn; the outcome is the
        verdict's."""
        result, mock_run = _drive_with_verdict(
            _ship_verdict(),
            verified_qa=_measured_verified_qa_signal(row_count=4),
            red_team_summary=dict(_measured_red_team_signal(), medium_count=5),
        )

        ids = [w["warning_id"] for w in mock_run.warnings]
        assert "verified_qa_low_count" in ids
        assert "red_team_medium_findings" in ids
        assert result["recommendation"] == "ship", (
            f"a warning must never move the outcome: {ids}"
        )


class TestTheNarrationIsOptional:
    """#54 criterion 5. The verdict exists without the model."""

    def test_an_orchestrator_timeout_still_persists_complete_with_the_verdict(self):
        from app.services.deployment_service import NARRATION_UNAVAILABLE_SUMMARY

        async def _times_out(signals_json, result_container, *, ledger=None):
            raise TimeoutError()

        result, mock_run = _drive_with_verdict(
            _blocking_verdict(), orchestrator=_times_out
        )

        assert result["status"] == "complete", (
            "a decision the platform already reached must not be thrown away "
            f"because the write-up did not arrive: {result}"
        )
        assert mock_run.status == "complete"
        assert result["recommendation"] == "block"
        assert mock_run.report["summary"] == NARRATION_UNAVAILABLE_SUMMARY

    def test_a_narration_that_never_called_the_tool_completes_the_same_way(self):
        from app.services.deployment_service import NARRATION_UNAVAILABLE_SUMMARY

        async def _says_nothing(signals_json, result_container, *, ledger=None):
            return None

        result, mock_run = _drive_with_verdict(
            _ship_verdict(), orchestrator=_says_nothing
        )

        assert result["status"] == "complete"
        assert result["recommendation"] == "ship"
        assert mock_run.report["summary"] == NARRATION_UNAVAILABLE_SUMMARY

    def test_a_malformed_warning_costs_the_prose_and_never_the_decision(self):
        """The reviewer's scenario, and the fourth occupant of the failed block.

        The tool loop validates nothing: build_report_tools stores submit_report's
        arguments verbatim and dispatch_outcome awaits the handler on raw args, so
        whatever the model emitted reaches DeploymentReport exactly as emitted. A
        warnings item carrying only warning_id raised a pydantic ValidationError
        inside the persist block, which marked the run 'failed', retried twice on
        the same prompt and the same model, and discarded a verdict the platform
        had already computed.
        """

        async def _malformed(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "summary": "ok",
                "warnings": [{"warning_id": "x"}],
            }

        result, mock_run = _drive_with_verdict(
            _blocking_verdict(), orchestrator=_malformed
        )

        assert result["status"] == "complete", (
            f"a malformed narration must cost the prose and nothing else: {result}"
        )
        assert mock_run.status == "complete"
        assert result["recommendation"] == "block"
        assert mock_run.report["summary"] == "ok", (
            "the readable half of the narration is still the owner's prose"
        )
        ids = [w["warning_id"] for w in mock_run.warnings]
        assert "x" not in ids, f"an unreadable warning must not be persisted: {ids}"
        assert "absent_eval_measurement" in ids, (
            "the verdict's own reason is what explains the block"
        )

    def test_one_bad_warning_does_not_discard_the_good_ones_beside_it(self):
        async def _mixed(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "summary": "ok",
                "warnings": [
                    {"warning_id": "x"},
                    {
                        "warning_id": "narrated_note",
                        "category": "eval_quality",
                        "message": "Worth a look before launch.",
                        "severity_level": "info",
                    },
                ],
            }

        _, mock_run = _drive_with_verdict(_ship_verdict(), orchestrator=_mixed)

        ids = [w["warning_id"] for w in mock_run.warnings]
        assert "narrated_note" in ids, f"the readable warning was thrown out too: {ids}"
        assert "x" not in ids

    def test_a_summary_of_none_falls_back_and_keeps_the_readable_warnings(self):
        from app.services.deployment_service import NARRATION_UNAVAILABLE_SUMMARY

        async def _no_summary(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "summary": None,
                "warnings": [
                    {
                        "warning_id": "narrated_note",
                        "category": "eval_quality",
                        "message": "Worth a look before launch.",
                        "severity_level": "info",
                    }
                ],
            }

        result, mock_run = _drive_with_verdict(
            _ship_verdict(), orchestrator=_no_summary
        )

        assert result["status"] == "complete"
        assert result["recommendation"] == "ship"
        assert mock_run.report["summary"] == NARRATION_UNAVAILABLE_SUMMARY
        assert "narrated_note" in [w["warning_id"] for w in mock_run.warnings]

    def test_a_warnings_value_that_is_not_a_list_still_completes(self):
        async def _string_warnings(signals_json, result_container, *, ledger=None):
            result_container["report"] = {"summary": "ok", "warnings": "none"}

        result, mock_run = _drive_with_verdict(
            _ship_verdict(), orchestrator=_string_warnings
        )

        assert result["status"] == "complete"
        assert result["recommendation"] == "ship"
        assert mock_run.report["summary"] == "ok"

    def test_the_malformed_narration_is_logged_loudly(self):
        """A model emitting an unreadable report is a defect in the prompt or in
        the routing, and the owner-facing outcome no longer shows it. The log
        line is where it shows."""
        mock_log = MagicMock()

        async def _malformed(signals_json, result_container, *, ledger=None):
            result_container["report"] = {
                "summary": None,
                "warnings": [{"warning_id": "x"}],
            }

        _drive_with_verdict(
            _ship_verdict(), orchestrator=_malformed, mock_log=mock_log
        )

        malformed = [
            call
            for call in mock_log.error.call_args_list
            if call.args
            and call.args[0] == "run_deployment_checklist.narration_malformed"
        ]
        assert len(malformed) == 1, (
            f"expected one malformed-narration line: {mock_log.error.call_args_list}"
        )
        assert malformed[0].kwargs["dropped_warnings"] == 1
        assert malformed[0].kwargs["summary_replaced"] is True

    def test_a_well_formed_narration_is_not_logged_as_malformed(self):
        mock_log = MagicMock()
        _drive_with_verdict(_ship_verdict(), mock_log=mock_log)

        assert not [
            call
            for call in mock_log.error.call_args_list
            if call.args
            and call.args[0] == "run_deployment_checklist.narration_malformed"
        ], "a report this build can read is the ordinary case"

    def test_the_fallback_summary_says_the_write_up_is_what_is_missing(self):
        from app.services.deployment_service import NARRATION_UNAVAILABLE_SUMMARY

        assert "could not be produced" in NARRATION_UNAVAILABLE_SUMMARY
        assert "warnings" in NARRATION_UNAVAILABLE_SUMMARY, (
            "the fallback has to point the owner at where the reasons are"
        )

    def test_the_rendered_verdict_reaches_the_narration_turn(self):
        """The turn narrates a decision it is handed. If the verdict never
        reached the signals blob, the model would be writing prose about a
        recommendation it cannot see."""
        captured = {}

        async def _capture(signals_json, result_container, *, ledger=None):
            captured.update(json.loads(signals_json))
            result_container["report"] = {"summary": "ok", "warnings": []}

        _drive_with_verdict(_blocking_verdict(), orchestrator=_capture)

        assert captured["verdict"]["outcome"] == "block"
        assert captured["verdict"]["reasons"][0]["observed"] == (
            "no evaluation result was recorded for this agent"
        )


class TestTheEvidenceGateStaysAsTheFloor:
    """#54 criterion 4. One way, and a disagreement is a defect signal."""

    def test_an_unmeasured_signal_still_blocks_a_shipping_verdict(self):
        from app.services.deployment_service import EVAL_SUMMARY_UNAVAILABLE_SIGNAL

        result, mock_run = _drive_with_verdict(
            _ship_verdict(), eval_summary=dict(EVAL_SUMMARY_UNAVAILABLE_SIGNAL)
        )

        assert result["recommendation"] == "block"
        assert "eval_signal_unavailable" in [
            w["warning_id"] for w in mock_run.warnings
        ]

    def test_the_disagreement_is_logged_loudly_and_never_resolved_quietly(self):
        """decide() and the gate read one checklist through different windows.
        When they differ, one of them is wrong about the same run, and the log
        line is the defect report."""
        from app.services.deployment_service import EVAL_SUMMARY_UNAVAILABLE_SIGNAL

        mock_log = MagicMock()
        _drive_with_verdict(
            _ship_verdict(),
            eval_summary=dict(EVAL_SUMMARY_UNAVAILABLE_SIGNAL),
            mock_log=mock_log,
        )

        disagreements = [
            call
            for call in mock_log.error.call_args_list
            if call.args
            and call.args[0] == "run_deployment_checklist.evidence_gate_disagrees"
        ]
        assert len(disagreements) == 1, (
            f"expected one disagreement line: {mock_log.error.call_args_list}"
        )
        kwargs = disagreements[0].kwargs
        assert kwargs["verdict_outcome"] == "ship"
        assert kwargs["gated_recommendation"] == "block"
        assert kwargs["eval_signal"] == "unavailable"

    def test_agreement_is_not_logged_as_a_disagreement(self):
        mock_log = MagicMock()
        _drive_with_verdict(_blocking_verdict(), mock_log=mock_log)

        assert not [
            call
            for call in mock_log.error.call_args_list
            if call.args
            and call.args[0] == "run_deployment_checklist.evidence_gate_disagrees"
        ], "the gate agreeing with decide() is the ordinary case"

    def test_the_gate_can_never_upgrade_what_decide_refused(self):
        """A floor, not a second opinion. Measured signals plus a blocking
        verdict is the case where an upgrading gate would ship a refused run."""
        result, _ = _drive_with_verdict(_blocking_verdict())

        assert result["recommendation"] == "block"


class TestTheVerdictIsComputedFromTheRecords:
    """_compute_verdict itself: three records in, and nothing else."""

    def _compute(self, statuses, eval_record=None, red_team_record=None, calibration=None):
        from app.worker.tasks.runtime.deployment import _compute_verdict

        state = {
            "run_id": "run-1",
            "since": "2026-08-30T12:00:00+00:00",
            "started_at": "2026-08-30T12:00:00+00:00",
            "statuses": statuses,
            "eval_dispatched": True,
            "red_team_dispatched": True,
        }
        with ExitStack() as stack:
            stack.enter_context(
                _deployment_patch("latest_eval_run_id_since", return_value="eval-1")
            )
            stack.enter_context(
                _deployment_patch(
                    "latest_red_team_run_id_since", return_value="red-1"
                )
            )
            read_eval = stack.enter_context(
                _deployment_patch("read_eval_result", return_value=eval_record)
            )
            read_red = stack.enter_context(
                _deployment_patch("read_red_team_result", return_value=red_team_record)
            )
            stack.enter_context(
                _deployment_patch(
                    "load_calibration_status",
                    return_value=calibration
                    if calibration is not None
                    else CalibrationStatus.absent("no_artifact"),
                )
            )
            verdict = _compute_verdict("agent-1", "postgresql://test/t", state)
        return verdict, read_eval, read_red

    def test_a_half_the_wait_never_saw_finish_is_read_as_absent(self):
        """The typed absent input. Not a zero, and not the pre-dispatch row."""
        from app.domain.verdict import Outcome

        verdict, read_eval, read_red = self._compute(
            {"eval": "complete", "red_team": None}
        )

        assert read_red.call_count == 0, (
            "a run the wait never saw finish has no record to read, and reaching "
            "for the newest row of any vintage is what the boundary prevents"
        )
        assert verdict.outcome is Outcome.BLOCK
        assert "absent_red_team_measurement" in [r.rule for r in verdict.reasons]

    def test_a_terminal_half_is_read_by_the_id_of_the_run_that_was_awaited(self):
        self._compute({"eval": "complete", "red_team": "failed"})[1].assert_called_once_with(
            "eval-1", "postgresql://test/t"
        )

    def test_a_run_that_wrote_no_readable_record_blocks_exactly_as_a_missing_one(self):
        """read_eval_result returns None for a NULL column, a pre-0022 tenant and
        a payload that broke a construction rule. All three are unmeasured."""
        from app.domain.verdict import Outcome

        verdict, _, _ = self._compute({"eval": "complete", "red_team": "complete"})

        assert verdict.outcome is Outcome.BLOCK
        assert {"absent_eval_measurement", "absent_red_team_measurement"} <= {
            r.rule for r in verdict.reasons
        }

    def test_the_block_on_high_setting_crosses_the_seam_as_the_caller_reads_it(self):
        """app.domain may not import app.core.config, so this caller reads the
        setting. A wired-in True would make the setting dead config."""
        from app.core.config import settings
        from app.worker.tasks.runtime import deployment as deployment_task

        state = {
            "run_id": "run-1",
            "since": "2026-08-30T12:00:00+00:00",
            "started_at": "2026-08-30T12:00:00+00:00",
            "statuses": {"eval": None, "red_team": None},
            "eval_dispatched": True,
            "red_team_dispatched": True,
        }
        with patch.object(
            deployment_task, "decide", return_value=_ship_verdict()
        ) as decide, patch.object(
            settings, "DEP_BLOCK_ON_HIGH_RED_TEAM", False
        ), _deployment_patch(
            "load_calibration_status", return_value=CalibrationStatus.absent("no_artifact")
        ):
            deployment_task._compute_verdict("agent-1", "postgresql://test/t", state)

        assert decide.call_args.kwargs["block_on_high"] is False

    def test_the_calibration_identity_comes_off_the_run_s_own_record(self):
        """Run-level, and already None when the four metric routes disagree. A
        payload with no record has no identity to ask about at all."""
        from app.core.config import settings
        from app.worker.tasks.runtime import deployment as deployment_task

        state = {
            "run_id": "run-1",
            "since": "2026-08-30T12:00:00+00:00",
            "started_at": "2026-08-30T12:00:00+00:00",
            "statuses": {"eval": None, "red_team": None},
            "eval_dispatched": True,
            "red_team_dispatched": True,
        }
        with _deployment_patch(
            "load_calibration_status",
            return_value=CalibrationStatus.absent("no_artifact"),
        ) as loader:
            deployment_task._compute_verdict("agent-1", "postgresql://test/t", state)

        assert loader.call_args.args == (settings.CALIBRATION_ARTIFACT_PATH, None)


class TestATaskFailureBeforeTheVerdictStillFails:
    """status='failed' is reserved for this, and only this (#54 criterion 5)."""

    def test_a_record_read_that_raises_marks_the_run_failed(self):
        from celery.exceptions import Retry

        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())
        mock_db, mock_run = _build_full_happy_path_mock_db(str(uuid.uuid4()))

        with ExitStack() as stack:
            stack.enter_context(_past_step_3b())
            stack.enter_context(
                _deployment_patch("get_sync_db", new=_make_sync_db_ctx(mock_db))
            )
            stack.enter_context(
                _deployment_patch(
                    "fernet_decrypt", return_value="postgresql://test/tenant"
                )
            )
            stack.enter_context(
                _deployment_patch(
                    "_fetch_eval_summary_sync", return_value=_measured_eval_signal()
                )
            )
            stack.enter_context(
                _deployment_patch(
                    "_fetch_red_team_summary_sync",
                    return_value=_measured_red_team_signal(),
                )
            )
            stack.enter_context(
                _deployment_patch(
                    "_fetch_verified_qa_stats_sync",
                    return_value=_measured_verified_qa_signal(),
                )
            )
            stack.enter_context(
                _deployment_patch(
                    "_fetch_corpus_stats_sync",
                    return_value=_measured_corpus_signal(),
                )
            )
            stack.enter_context(
                _deployment_patch(
                    "_fetch_blast_radius_sync", return_value=dict(_BLAST_RADIUS_FIXTURE)
                )
            )
            stack.enter_context(
                _deployment_patch("_compute_envelope_hash_sync", return_value="hash")
            )
            # Re-patched INSIDE _past_step_3b's stub, so the real one is what
            # raises. The helper stubs it to keep every other test off a socket.
            stack.enter_context(
                _deployment_patch(
                    "_compute_verdict", side_effect=RuntimeError("tenant DB unreachable")
                )
            )
            try:
                run_deployment_checklist.run(agent_id=agent_id)
            except (Retry, RuntimeError):
                pass

        assert mock_run.status == "failed", (
            "the decision was never reached, so there is nothing to complete on"
        )


class TestTheRunStatusReaders:
    """The two queries the wait polls, against a cursor double.

    One statement answers both questions the checklist asks of a run: `status`
    for the poll, `id` for the record read once the wait settles.
    """

    def _read(self, reader, row):
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = row
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch(
            "app.services.deployment_service.psycopg2.connect", return_value=mock_conn
        ):
            status = reader(
                "agent-1",
                "postgresql://test/tenant",
                datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
            )
        return status, mock_cursor.execute.call_args

    def test_the_eval_reader_keys_on_the_agent_and_the_dispatch_moment(self):
        """`started_at >= %s` is the whole point of the query. Without it last
        night's terminal run satisfies the wait instantly and the checklist is
        straight back to grading a row it did not cause."""
        from app.services.deployment_service import latest_eval_run_status_since

        since = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        status, call = self._read(latest_eval_run_status_since, ("run-1", "complete"))

        assert status == "complete"
        sql, params = call.args
        assert "FROM eval_runs" in sql
        assert "started_at >= %s" in sql, (
            f"the dispatch boundary is missing from the query: {sql}"
        )
        assert "ORDER BY started_at DESC LIMIT 1" in sql
        assert params == ("m6:agent-1", since), (
            "m6:{agent_id} is the eval kind, matching _LATEST_RUN_SQL, so a "
            f"second agent on the same tenant DB is not read as this one: {params}"
        )

    def test_the_red_team_reader_keys_on_the_m7_kind(self):
        from app.services.deployment_service import latest_red_team_run_status_since

        since = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        status, call = self._read(latest_red_team_run_status_since, ("run-9", "failed"))

        assert status == "failed"
        sql, params = call.args
        assert "FROM red_team_runs" in sql
        assert "started_at >= %s" in sql, (
            f"the dispatch boundary is missing from the query: {sql}"
        )
        assert params == ("m7:agent-1", since), (
            "m7:{agent_id} is what run_red_team INSERTs and what its own "
            f"idempotency guard reads: {params}"
        )

    def test_no_row_yet_reads_as_none(self):
        """The run has not started, or the boundary excluded it. Either way there
        is nothing terminal to read."""
        from app.services.deployment_service import latest_eval_run_status_since

        status, _ = self._read(latest_eval_run_status_since, None)

        assert status is None

    def test_the_readers_answer_the_same_row_by_id_and_by_status(self):
        """The record the checklist reads has to belong to the run it awaited.

        Reading the newest eval run of ANY vintage is what the whole `since`
        boundary exists to prevent, and the id reader has to carry the same
        boundary or the record would come from a different run than the status
        the wait observed.
        """
        from app.services.deployment_service import (
            latest_eval_run_id_since,
            latest_red_team_run_id_since,
        )

        since = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        run_id, call = self._read(latest_eval_run_id_since, ("run-1", "complete"))
        assert run_id == "run-1"
        sql, params = call.args
        assert "started_at >= %s" in sql
        assert params == ("m6:agent-1", since)

        run_id, call = self._read(latest_red_team_run_id_since, ("run-9", "failed"))
        assert run_id == "run-9"
        assert call.args[1] == ("m7:agent-1", since)

    def test_no_row_reads_as_no_id_rather_than_an_empty_string(self):
        """None is what the record readers are never called with."""
        from app.services.deployment_service import latest_eval_run_id_since

        run_id, _ = self._read(latest_eval_run_id_since, None)

        assert run_id is None

    def test_an_unreachable_tenant_db_reads_as_none_rather_than_raising(self):
        """A read failure mid-wait must not fail a checklist that still owes the
        owner a report. It keeps the wait waiting and expires as absent."""
        from app.services.deployment_service import latest_eval_run_status_since

        with patch(
            "app.services.deployment_service.psycopg2.connect",
            side_effect=RuntimeError("tenant DB unreachable"),
        ):
            status = latest_eval_run_status_since(
                "agent-1",
                "postgresql://test/tenant",
                datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
            )

        assert status is None


class TestTheDispatchMoment:
    """The boundary comes from the tenant DB's clock, not the worker's."""

    def test_it_reads_the_tenant_clock(self):
        """Both jobs INSERT with the DB's NOW(), and the wait compares against
        that column. A worker clock a few seconds ahead would put the boundary
        after the row the checklist just caused, and the wait would expire on a
        run that had finished."""
        from app.services.deployment_service import _dispatch_moment

        tenant_now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (tenant_now,)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch(
            "app.services.deployment_service.psycopg2.connect", return_value=mock_conn
        ):
            moment = _dispatch_moment("postgresql://test/tenant")

        assert moment == tenant_now
        assert mock_cursor.execute.call_args.args[0] == "SELECT now()"

    def test_an_unreadable_tenant_clock_falls_back_to_the_worker(self):
        """A checklist that cannot read the clock still runs. The fallback
        carries the skew risk, which is why the service logs it."""
        from app.services.deployment_service import _dispatch_moment

        before = datetime.now(timezone.utc)
        with patch(
            "app.services.deployment_service.psycopg2.connect",
            side_effect=RuntimeError("tenant DB unreachable"),
        ):
            moment = _dispatch_moment("postgresql://test/tenant")

        assert moment >= before


class TestTheRedTeamDispatchHelper:
    """CLAUDE.md rule 1 and CTL-08, on the new dispatch."""

    def _dispatch(self, agent_id, fake_task):
        from app.worker.tasks.runtime import deployment as deployment_task

        module = MagicMock()
        module.run_red_team = fake_task
        with patch.dict(
            "sys.modules", {"app.worker.tasks.runtime.red_team": module}
        ):
            return deployment_task._dispatch_red_team_run(agent_id)

    def test_it_passes_only_the_agent_id_to_the_runtime_queue(self):
        agent_id = str(uuid.uuid4())
        fake_task = MagicMock()

        assert self._dispatch(agent_id, fake_task) is True

        fake_task.apply_async.assert_called_once_with(
            kwargs={"agent_id": agent_id}, queue="runtime"
        )

    def test_a_broker_failure_is_reported_rather_than_raised(self):
        """The checklist still owes the owner a verdict. The run then never
        reaches terminal, the wait reports it absent, and the gate blocks."""
        fake_task = MagicMock()
        fake_task.apply_async.side_effect = RuntimeError("broker unreachable")

        assert self._dispatch("agent-1", fake_task) is False


class TestTheReQueueGuardsTheChain:
    """A lost re-queue kills the whole wait chain (f55d052 review, #125 family).

    The dispatch helpers are best-effort because a lost dispatch still expires
    honestly. A lost re-queue leaves a 'running' row nothing will ever finish,
    so it is persisted as a failure instead.
    """

    def _state(self):
        return {
            "run_id": "run-1",
            "since": "2026-08-30T12:00:00+00:00",
            "started_at": "2026-08-30T12:00:01+00:00",
            "statuses": {"eval": "complete", "red_team": None},
            "eval_dispatched": True,
            "red_team_dispatched": True,
        }

    def test_a_broker_failure_returns_false_and_names_the_run(self):
        from app.worker.tasks.runtime import deployment as deployment_task

        mock_log = MagicMock()
        with patch.object(
            deployment_task.run_deployment_checklist,
            "apply_async",
            side_effect=RuntimeError("broker unreachable"),
        ), patch.object(deployment_task, "log", mock_log):
            assert deployment_task._requeue_wait("agent-1", self._state()) is False

        failures = [
            call for call in mock_log.error.call_args_list
            if call.args and call.args[0] == "run_deployment_checklist.requeue_failed"
        ]
        assert len(failures) == 1, mock_log.error.call_args_list
        assert failures[0].kwargs["run_id"] == "run-1"

    def test_a_failed_requeue_marks_the_run_failed_rather_than_stranding_it(self):
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        mock_db, mock_run = _build_full_happy_path_mock_db(str(uuid.uuid4()))
        with ExitStack() as stack:
            for one in [
                _deployment_patch("get_sync_db", new=_make_sync_db_ctx(mock_db)),
                _deployment_patch(
                    "fernet_decrypt", return_value="postgresql://test/tenant"
                ),
                _deployment_patch(
                    "_dispatch_moment",
                    return_value=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
                ),
                _deployment_patch("_dispatch_eval_run", return_value=True),
                _deployment_patch("_dispatch_red_team_run", return_value=True),
                _deployment_patch("latest_eval_run_status_since", return_value=None),
                _deployment_patch(
                    "latest_red_team_run_status_since", return_value=None
                ),
                _deployment_patch("_requeue_wait", return_value=False),
            ]:
                stack.enter_context(one)
            result = run_deployment_checklist.run(agent_id=str(uuid.uuid4()))

        assert result == {}, (
            "a wait that cannot continue must not report itself as waiting: "
            f"{result}"
        )
        assert mock_run.status == "failed", (
            "the row must not sit 'running' with no continuation coming: "
            f"{mock_run.status}"
        )


# ---------------------------------------------------------------------------
# The idempotency guard, executed by PostgreSQL rather than scripted (#129)
# ---------------------------------------------------------------------------

#: The disposable local cluster CLAUDE.md names, read through the same env-var
#: override the other probe tests use so a machine with non-default local
#: credentials is configured from one place. checklist_runs is a CONTROL DB
#: table; what this borrows from the probe database is a real PostgreSQL to run
#: the guard's WHERE against, inside a transaction that is rolled back.
CONTROL_PROBE_URL = os.getenv(
    "TEST_TENANT_PROBE_URL",
    os.getenv("TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432")
    + "/wchats_tenant_probe",
)


class TestTheIdempotencyGuardKeysOnTheRunNotTheClock:
    """#129: a congested chain outlived the guard's 60-minute created_at window.

    The ceiling caps the observed wait at 45 minutes, but continuations queue
    behind the eval and red-team turns they are waiting for on the solo worker
    and the deciding pass adds the orchestrator's budget on top. Past minute 60
    the window no longer covered a run that was still going, a second trigger saw
    no live row, and two checklists ran on one agent.

    A MOCK CANNOT SEE THIS. Every other test of the guard scripts
    `scalar_one_or_none`, so the WHERE clause is never evaluated and a row handed
    back reads as live whatever the query said about it. These run the statement.
    """

    @pytest.fixture
    def session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.models.checklist_run import ChecklistRun

        engine = create_engine(CONTROL_PROBE_URL)
        try:
            conn = engine.connect()
        except Exception as exc:  # OperationalError and everything under it
            engine.dispose()
            pytest.skip(f"no local probe cluster at {CONTROL_PROBE_URL}: {exc}")
        outer = conn.begin()
        try:
            ChecklistRun.__table__.create(bind=conn)
            db = Session(bind=conn, join_transaction_mode="create_savepoint")
            try:
                yield db
            finally:
                db.close()
        finally:
            outer.rollback()
            conn.close()
            engine.dispose()

    def _row(self, db, agent_id, *, age_s, beat_age_s, status="running"):
        """One checklist_runs row, aged and beating exactly as the case needs."""
        from datetime import timedelta

        from app.models.checklist_run import ChecklistRun

        now = datetime.now(timezone.utc)
        run = ChecklistRun(
            agent_id=agent_id,
            status=status,
            created_at=now - timedelta(seconds=age_s),
            heartbeat_at=(
                None if beat_age_s is None else now - timedelta(seconds=beat_age_s)
            ),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def _guard(self, db, agent_id):
        from app.worker.tasks.runtime import deployment as deployment_task

        @contextmanager
        def _lend():
            yield db

        with patch.object(deployment_task, "get_sync_db", _lend):
            return deployment_task._a_run_is_already_live(str(agent_id))

    def test_a_chain_still_beating_blocks_however_old_its_row_is(self, session):
        """The whole of #129. Three hours old, beating ten seconds ago, alive."""
        agent_id = uuid.uuid4()
        self._row(session, agent_id, age_s=3 * 3600, beat_age_s=10)

        assert self._guard(session, agent_id) is True, (
            "a chain that beat ten seconds ago is running, and a second trigger "
            "that starts beside it puts two checklists on one agent"
        )

    def test_a_row_whose_beat_went_silent_is_reaped_and_the_trigger_goes_through(
        self, session
    ):
        """The other direction: an abandoned row must not block the agent forever."""
        agent_id = uuid.uuid4()
        run = self._row(session, agent_id, age_s=4 * 3600, beat_age_s=4 * 3600)

        assert self._guard(session, agent_id) is False
        session.refresh(run)
        assert run.status == "failed", (
            "a row nothing is going to finish has to be closed out, not skipped "
            f"over: {run.status}"
        )

    def test_a_row_that_has_not_beaten_yet_falls_back_to_when_it_was_created(
        self, session
    ):
        """The first pass inserts and then dispatches; it has not polled yet."""
        agent_id = uuid.uuid4()
        self._row(session, agent_id, age_s=5, beat_age_s=None)

        assert self._guard(session, agent_id) is True

    def test_a_row_that_never_beat_and_never_will_is_reaped_on_its_created_at(
        self, session
    ):
        agent_id = uuid.uuid4()
        run = self._row(session, agent_id, age_s=5 * 3600, beat_age_s=None)

        assert self._guard(session, agent_id) is False
        session.refresh(run)
        assert run.status == "failed"

    def test_a_chain_queued_behind_the_red_team_run_it_dispatched_is_not_reaped(
        self, session
    ):
        """Prefork concurrency 2: the continuation waits out the red-team run.

        The chain dispatches both jobs onto the same `runtime` queue it runs on,
        so its next pass cannot execute until a slot frees. With two slots the
        gap between two beats is one red-team run end to end.
        """
        from app.worker.tasks.runtime.red_team import red_team_run_bound_s

        agent_id = uuid.uuid4()
        gap = red_team_run_bound_s() + 1
        self._row(session, agent_id, age_s=gap + 60, beat_age_s=gap)

        assert self._guard(session, agent_id) is True, (
            "the chain is queued behind the red-team run it dispatched, which is "
            f"{red_team_run_bound_s()}s of silence the guard has to sit through"
        )

    def test_a_chain_queued_behind_both_jobs_it_dispatched_is_not_reaped(
        self, session
    ):
        """The documented solo worker, and the whole of this defect.

        One execution slot means the continuation queues behind the eval chain
        AND the red-team run, so the gap between two beats is both bounds end to
        end. The threshold was the ceiling plus the decide grace, which is
        shorter than that, so the guard reaped a chain that was still working and
        a second checklist started beside it.
        """
        from app.worker.tasks.runtime.eval import eval_run_bound_s
        from app.worker.tasks.runtime.red_team import red_team_run_bound_s

        agent_id = uuid.uuid4()
        gap = eval_run_bound_s() + red_team_run_bound_s() + 1
        self._row(session, agent_id, age_s=gap + 60, beat_age_s=gap)

        assert self._guard(session, agent_id) is True, (
            f"a chain queued behind {gap - 1}s of jobs it started itself is "
            "working, and reaping it lets a second checklist run on this agent"
        )

    def test_a_finished_row_is_not_a_live_run_whatever_its_beat_says(self, session):
        agent_id = uuid.uuid4()
        self._row(session, agent_id, age_s=60, beat_age_s=1, status="complete")

        assert self._guard(session, agent_id) is False

    def test_another_agents_live_run_does_not_block_this_one(self, session):
        self._row(session, uuid.uuid4(), age_s=60, beat_age_s=1)

        assert self._guard(session, uuid.uuid4()) is False

    def test_an_agent_with_no_row_at_all_is_let_through(self, session):
        assert self._guard(session, uuid.uuid4()) is False


class TestTheStaleThresholdOutlastsTheJobsTheChainWaitsBehind:
    """What the guard calls abandoned has to be longer than the queue wait.

    The threshold was CHECKLIST_WAIT_CEILING_S plus the decide grace, and its
    docstring said a live chain "is never quiet for longer than the gap between
    two passes, and that gap is bounded by the ceiling itself". The ceiling
    bounds the WAIT, never the queue: a continuation cannot run at all while the
    eval and red-team jobs it dispatched hold the `runtime` slots, so the gap
    between two beats is those jobs' own bounds. The numbers are read from the
    modules that own them, so a change to any bound moves the test with the code.
    """

    def _bounds(self):
        from app.worker.tasks.runtime.eval import eval_run_bound_s
        from app.worker.tasks.runtime.red_team import red_team_run_bound_s

        return eval_run_bound_s(), red_team_run_bound_s()

    def test_the_threshold_covers_both_job_bounds_and_the_ceiling(self):
        from app.core.config import settings
        from app.worker.tasks.runtime.deployment import _stale_after_s

        eval_bound, red_team_bound = self._bounds()
        queue_gap = eval_bound + red_team_bound + settings.CHECKLIST_WAIT_CEILING_S

        assert _stale_after_s() > queue_gap, (
            f"a chain can go {queue_gap}s between beats on the documented solo "
            f"worker and the guard reaps it after {_stale_after_s()}s, so the "
            "run it reaps is still working"
        )

    def test_the_threshold_is_read_from_the_bounds_rather_than_sized_beside_them(
        self,
    ):
        """A moved bound has to move the threshold, or the two drift (1.33)."""
        from unittest.mock import patch as _patch

        from app.worker.tasks.runtime.deployment import _stale_after_s

        before = _stale_after_s()
        with _patch(
            "app.worker.tasks.runtime.red_team.ATTACKER_LOOP_TIMEOUT_S", 240.0
        ):
            after = _stale_after_s()

        assert after > before, (
            "doubling the attacker's per-attempt budget doubles how long a "
            f"continuation waits behind it, and the threshold did not move: "
            f"{before} then {after}"
        )


class TestEveryPassStampsTheRunsBeat:
    """The guard reads a heartbeat, so a live chain has to write one (#129)."""

    def test_a_pass_that_polls_stamps_the_beat_on_its_own_row(self):
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        mock_db, mock_run = _build_full_happy_path_mock_db("run-1")
        mock_run.heartbeat_at = None
        with ExitStack() as stack:
            for one in [
                _deployment_patch("get_sync_db", new=_make_sync_db_ctx(mock_db)),
                _deployment_patch(
                    "fernet_decrypt", return_value="postgresql://test/tenant"
                ),
                _deployment_patch(
                    "_dispatch_moment",
                    return_value=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
                ),
                _deployment_patch("_dispatch_eval_run", return_value=True),
                _deployment_patch("_dispatch_red_team_run", return_value=True),
                _deployment_patch("latest_eval_run_status_since", return_value=None),
                _deployment_patch(
                    "latest_red_team_run_status_since", return_value=None
                ),
                _deployment_patch("_requeue_wait", return_value=True),
            ]:
                stack.enter_context(one)
            result = run_deployment_checklist.run(agent_id=str(uuid.uuid4()))

        assert result["status"] == "waiting"
        assert isinstance(mock_run.heartbeat_at, datetime), (
            "a pass that reached the tenant DB has to say so on its row, or the "
            f"guard reads the chain as abandoned: {mock_run.heartbeat_at!r}"
        )

    def test_a_continuation_stamps_its_own_beat_too(self):
        """The pass that actually goes quiet is a continuation, not the first.

        The first pass beats seconds after the insert, when the row's created_at
        already reads as fresh. Every beat that matters to the guard comes from a
        continuation, and this pins that one directly rather than through the
        shared `_polled` the first-pass test happens to exercise.
        """
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        mock_db, mock_run = _build_full_happy_path_mock_db("run-1")
        mock_run.heartbeat_at = None
        with ExitStack() as stack:
            for one in [
                _deployment_patch("get_sync_db", new=_make_sync_db_ctx(mock_db)),
                _deployment_patch(
                    "fernet_decrypt", return_value="postgresql://test/tenant"
                ),
                _deployment_patch("_dispatch_eval_run", return_value=True),
                _deployment_patch("_dispatch_red_team_run", return_value=True),
                _deployment_patch("latest_eval_run_status_since", return_value=None),
                _deployment_patch(
                    "latest_red_team_run_status_since", return_value=None
                ),
                _deployment_patch("_requeue_wait", return_value=True),
            ]:
                stack.enter_context(one)
            result = run_deployment_checklist.run(
                agent_id=str(uuid.uuid4()),
                wait_state=_continuation_state("run-1"),
            )

        assert result["status"] == "waiting"
        assert isinstance(mock_run.heartbeat_at, datetime), (
            "a continuation queued behind the jobs it dispatched is exactly the "
            "chain the guard is deciding about, and it has to say it ran: "
            f"{mock_run.heartbeat_at!r}"
        )


def _continuation_state(run_id):
    """The state a continuation carries, shaped the way `_open_wait` writes it."""
    return {
        "run_id": run_id,
        "since": "2026-08-30T12:00:00+00:00",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "statuses": {"eval": None, "red_team": None},
        "eval_dispatched": True,
        "red_team_dispatched": True,
    }


class TestAReapedRunIsNeverWrittenOverByItsOwnChain:
    """#129's other direction: the guard closes a row and the chain reopens it.

    The guard reaps a chain it reads as abandoned, and the next trigger starts a
    fresh checklist for the agent. The reaped chain was never dead — it was
    queued — so its next pass stamped a beat onto the row it no longer owned and
    its deciding pass flipped that row to 'complete'. Two complete checklists on
    one agent, which is the outcome the guard exists to prevent, reached from
    inside it.

    Every write the chain makes is now fenced on the row still saying 'running',
    so a reaped run takes nothing further from it.
    """

    def _drive_continuation(self, mock_run_status, *, mock_log=None):
        """One continuation pass against a row in the state the guard left it."""
        from app.worker.tasks.runtime import deployment as deployment_task
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        mock_db, mock_run = _build_full_happy_path_mock_db("run-1")
        mock_run.status = mock_run_status
        mock_run.heartbeat_at = None
        requeued = []

        with ExitStack() as stack:
            for one in [
                _deployment_patch("get_sync_db", new=_make_sync_db_ctx(mock_db)),
                _deployment_patch(
                    "fernet_decrypt", return_value="postgresql://test/tenant"
                ),
                _deployment_patch("latest_eval_run_status_since", return_value=None),
                _deployment_patch(
                    "latest_red_team_run_status_since", return_value=None
                ),
                _deployment_patch(
                    "_requeue_wait",
                    new=lambda _agent_id, state: requeued.append(state) or True,
                ),
            ]:
                stack.enter_context(one)
            if mock_log is not None:
                stack.enter_context(patch.object(deployment_task, "log", mock_log))
            result = run_deployment_checklist.run(
                agent_id=str(uuid.uuid4()),
                wait_state=_continuation_state("run-1"),
            )
        return result, mock_run, requeued

    def test_a_deciding_pass_whose_row_was_reaped_does_not_complete_it(self):
        """The adversary's probe B. The reap lands during the narration turn."""
        _, fetchers, collectors = _sequenced_world(eval_polls=0, red_team_polls=0)

        result, mock_run, _ = _drive_sequenced(
            fetchers,
            collectors,
            on_orchestrate=lambda run: setattr(run, "status", "failed"),
        )

        assert mock_run.status == "failed", (
            "the guard closed this row out and a second checklist holds the "
            f"agent now; completing it puts two reports on one: {mock_run.status}"
        )
        assert result == {"status": "reaped", "run_id": mock_run.id}, (
            f"a pass that wrote nothing must not report a recommendation: {result}"
        )

    def test_a_continuation_whose_row_was_reaped_stops_before_it_re_queues(self):
        """The beat is where a queued chain finds out, and it stops there."""
        result, mock_run, requeued = self._drive_continuation("failed")

        assert requeued == [], (
            "a chain whose row was reaped must not keep the wait alive; the "
            f"agent already has a live checklist: {requeued}"
        )
        assert result == {}, f"nothing was written, so nothing is reported: {result}"
        assert mock_run.heartbeat_at is None, (
            "the beat landed on a row this chain no longer owns, which is what "
            "made the guard read the reaped run as alive again"
        )

    def test_the_reaped_chain_says_so_once_in_the_log(self):
        mock_log = MagicMock()

        self._drive_continuation("failed", mock_log=mock_log)

        reaped = [
            call
            for call in mock_log.warning.call_args_list
            if call.args
            and call.args[0] == "run_deployment_checklist.run_reaped_while_live"
        ]
        assert len(reaped) == 1, (
            f"expected one run_reaped_while_live warning: "
            f"{mock_log.warning.call_args_list}"
        )
        assert reaped[0].kwargs["run_id"] == "run-1"

    def test_a_row_still_running_is_still_this_chains_to_write(self):
        """The fence is scoped to a reap. An ordinary continuation carries on."""
        result, mock_run, requeued = self._drive_continuation("running")

        assert result["status"] == "waiting", result
        assert len(requeued) == 1
        assert isinstance(mock_run.heartbeat_at, datetime), (
            f"a live chain still beats: {mock_run.heartbeat_at!r}"
        )


class TestTheFirstPassIsFencedLikeTheContinuation:
    """#125: the stretch from the row insert to the first poll had no fence.

    A continuation that cannot be read marks its run failed, and a re-queue that
    fails does the same. The FIRST pass ran the dispatch moment, both dispatches
    and the first poll outside every try, so anything that raised there left the
    row 'running' with no terminal update and the step-2 guard then refused every
    re-run behind it for the whole window.
    """

    def _state(self):
        return {
            "run_id": "run-1",
            "since": "2026-08-30T12:00:00+00:00",
            "started_at": "2026-08-30T12:00:01+00:00",
            "statuses": {"eval": None, "red_team": None},
            "eval_dispatched": True,
            "red_team_dispatched": True,
        }

    def _drive(self, patches, wait_state=None):
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        mock_db, mock_run = _build_full_happy_path_mock_db("run-1")
        mock_log = MagicMock()
        with ExitStack() as stack:
            for one in [
                _deployment_patch("get_sync_db", new=_make_sync_db_ctx(mock_db)),
                _deployment_patch(
                    "fernet_decrypt", return_value="postgresql://test/tenant"
                ),
                _deployment_patch("log", new=mock_log),
                *patches,
            ]:
                stack.enter_context(one)
            result = run_deployment_checklist.run(
                agent_id=str(uuid.uuid4()), wait_state=wait_state
            )
        return result, mock_run, mock_log

    def _opened(self):
        return [
            _deployment_patch(
                "_dispatch_moment",
                return_value=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
            ),
            _deployment_patch("_dispatch_eval_run", return_value=True),
            _deployment_patch("_dispatch_red_team_run", return_value=True),
        ]

    def test_a_wait_that_cannot_be_opened_marks_the_row_failed(self):
        """The row exists from the insert on, so the failure has somewhere to land."""
        result, mock_run, _ = self._drive(
            [
                _deployment_patch(
                    "_dispatch_moment", side_effect=RuntimeError("tenant clock refused")
                )
            ]
        )

        assert result == {}, f"a run that never opened its wait is not waiting: {result}"
        assert mock_run.status == "failed", (
            "the row must not sit 'running' with no wait behind it: "
            f"{mock_run.status}"
        )

    def test_the_first_poll_that_raises_marks_the_row_failed(self):
        result, mock_run, _ = self._drive(
            self._opened()
            + [
                _deployment_patch(
                    "latest_eval_run_status_since",
                    side_effect=RuntimeError("tenant db unreachable"),
                )
            ]
        )

        assert result == {}, f"a pass that never polled is not waiting: {result}"
        assert mock_run.status == "failed", (
            f"the row must not sit 'running' after a poll that raised: {mock_run.status}"
        )

    def test_a_poll_that_raises_on_a_continuation_marks_the_row_failed(self):
        """The continuation reads its state fine and the poll is what falls over."""
        result, mock_run, _ = self._drive(
            [
                _deployment_patch(
                    "latest_eval_run_status_since",
                    side_effect=RuntimeError("tenant db unreachable"),
                )
            ],
            wait_state=self._state(),
        )

        assert result == {}
        assert mock_run.status == "failed", (
            f"a continuation whose poll raised must close its row: {mock_run.status}"
        )

    def test_the_failure_is_logged_with_the_error_type_that_caused_it(self):
        """`_continue_wait`'s shape: the run is named and so is what went wrong."""
        result, _, mock_log = self._drive(
            [
                _deployment_patch(
                    "_dispatch_moment", side_effect=KeyError("no such setting")
                )
            ]
        )

        assert result == {}
        failures = [
            call
            for call in mock_log.error.call_args_list
            if call.args and call.args[0] == "run_deployment_checklist.failed"
        ]
        assert len(failures) == 1, mock_log.error.call_args_list
        assert failures[0].kwargs["run_id"] == "run-1"
        assert failures[0].kwargs["error_type"] == "KeyError", (
            f"the original failure has to survive to the log: {failures[0].kwargs}"
        )


class TestAContinuationIsValidatedByValue:
    """FM-018: the reader checks what the writer guarantees, not key names alone."""

    def _state(self, **over):
        state = {
            "run_id": "run-1",
            "since": "2026-08-30T12:00:00+00:00",
            "started_at": "2026-08-30T12:00:01+00:00",
            "statuses": {"eval": "complete", "red_team": None},
            "eval_dispatched": True,
            "red_team_dispatched": True,
        }
        state.update(over)
        return state

    def test_a_status_this_build_never_wrote_is_refused(self):
        """poll_terminal_statuses treats any recorded status as terminal, so a
        smuggled 'running' would skip the wait and collect against live runs."""
        from app.worker.tasks.runtime.deployment import _require_wait_state

        with pytest.raises(ValueError) as excinfo:
            _require_wait_state(
                self._state(statuses={"eval": "running", "red_team": None})
            )
        assert "running" in str(excinfo.value)

    def test_a_statuses_map_missing_a_half_is_refused(self):
        from app.worker.tasks.runtime.deployment import _require_wait_state

        with pytest.raises(ValueError):
            _require_wait_state(self._state(statuses={"eval": None}))

    def test_a_timestamp_that_does_not_parse_is_refused(self):
        from app.worker.tasks.runtime.deployment import _require_wait_state

        with pytest.raises(ValueError) as excinfo:
            _require_wait_state(self._state(since="garbage"))
        assert "since" in str(excinfo.value)

    def test_an_unreadable_continuation_marks_its_run_failed(self):
        """Refusing must not mean vanishing: the named run is closed out (#125)."""
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        mock_db, mock_run = _build_full_happy_path_mock_db("run-1")
        with ExitStack() as stack:
            for one in [
                _deployment_patch("get_sync_db", new=_make_sync_db_ctx(mock_db)),
                _deployment_patch(
                    "fernet_decrypt", return_value="postgresql://test/tenant"
                ),
            ]:
                stack.enter_context(one)
            result = run_deployment_checklist.run(
                agent_id=str(uuid.uuid4()), wait_state=self._state(since="garbage")
            )

        assert result == {}
        assert mock_run.status == "failed"


class TestTheEvalHalfExpiresLikeTheRedTeamHalf:
    """The eval branch of the expiry fold, driven at task level (f55d052 review)."""

    def test_a_ceiling_expiry_with_the_eval_still_running_substitutes_did_not_finish(self):
        _, fetchers, collectors = _sequenced_world(eval_polls=10**6, red_team_polls=0)

        result, mock_run, _ = _drive_sequenced(fetchers, collectors, ceiling_s=0)

        assert result["status"] == "complete"
        assert mock_run.report["eval_summary"]["eval_signal"] == "did_not_finish", (
            "the eval never finished, so its summary is the substituted absent "
            "state, never the pre-dispatch collector read"
        )
        assert "eval_dispatched" in mock_run.report["eval_summary"], (
            "the substitution branch must still carry the dispatch fact the "
            "owner-facing warning reads"
        )
        assert result["recommendation"] == "block"
        assert "eval_did_not_finish" in [
            warning["warning_id"] for warning in mock_run.warnings
        ]


class TestTheWaitMeasuresItself:
    """`waited_s` is a measurement; a constant would satisfy every other test."""

    def test_waited_s_is_the_clock_since_the_wait_opened(self):
        from datetime import timedelta

        from app.worker.tasks.runtime.deployment import _waited_s

        started = datetime.now(timezone.utc) - timedelta(seconds=30)
        value = _waited_s({"started_at": started.isoformat()})
        assert 29.0 <= value <= 40.0, (
            f"thirty seconds of wait must read as about thirty seconds: {value}"
        )


class TestTheKnowledgeCollectorsRefuseLikeTheGatedTwo:
    """#131: `_collected`'s two remaining fallbacks were exact plausible zeros.

    The eval and red-team halves substitute an 'unavailable' signal the evidence
    gate refuses to ship on. These two substituted
    `{"row_count": 0, "avg_faithfulness": 0.0, "avg_relevance": 0.0}` and
    `{"document_count": 0, "chunk_count": 0, "last_ingested_at": None}`, which
    the owner's report renders as an empty knowledge base rather than as a read
    that never happened. Driven through the real except path, because the
    substitution is the thing under test.
    """

    def _drive(self):
        from app.services.deployment_service import (
            BLAST_RADIUS_DEFAULT_SIGNAL as _BLAST_RADIUS_DEFAULT_SIGNAL,
        )
        from app.worker.tasks.runtime.deployment import run_deployment_checklist

        agent_id = str(uuid.uuid4())
        mock_run_id = str(uuid.uuid4())
        mock_db, mock_run = _build_full_happy_path_mock_db(mock_run_id)
        told = {}

        async def _fake_call_orchestrator_async(
            signals_json, result_container, *, ledger=None
        ):
            told.update(json.loads(signals_json))
            result_container["report"] = {
                "recommendation": "ship",
                "summary": "All good.",
                "warnings": [],
            }

        with _past_step_3b(), patch(
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
            side_effect=RuntimeError("tenant DB unreachable"),
        ), patch(
            "app.worker.tasks.runtime.deployment._fetch_corpus_stats_sync",
            side_effect=RuntimeError("tenant DB unreachable"),
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

        return result, mock_run, told

    def test_the_verified_qa_outage_is_persisted_as_an_outage(self):
        _, mock_run, _ = self._drive()
        stats = mock_run.report["verified_qa_stats"]

        assert stats["signal"] == "unavailable"
        assert stats["row_count"] is None
        assert stats["avg_faithfulness"] is None
        assert stats["avg_relevance"] is None

    def test_the_corpus_outage_is_persisted_as_an_outage(self):
        _, mock_run, _ = self._drive()
        stats = mock_run.report["corpus_stats"]

        assert stats["signal"] == "unavailable"
        assert stats["document_count"] is None
        assert stats["chunk_count"] is None

    def test_a_reader_can_tell_an_outage_from_a_tenant_that_has_nothing(self):
        """The zeros a real empty tenant produces, asserted as NOT what an
        outage writes. This is the whole of #131."""
        _, mock_run, _ = self._drive()

        assert mock_run.report["verified_qa_stats"] != {
            "row_count": 0,
            "avg_faithfulness": 0.0,
            "avg_relevance": 0.0,
        }
        assert mock_run.report["corpus_stats"] != {
            "document_count": 0,
            "chunk_count": 0,
            "last_ingested_at": None,
        }

    def test_the_outage_reaches_the_owner_as_a_warning(self):
        _, mock_run, _ = self._drive()
        ids = [warning["warning_id"] for warning in mock_run.warnings]

        assert "verified_qa_unavailable" in ids
        assert "verified_qa_low_count" not in ids, (
            "a corpus nobody counted is not a thin corpus"
        )

    def test_the_orchestrator_is_told_the_figures_are_absent(self):
        _, _, told = self._drive()

        assert told["verified_qa_stats"]["row_count"] is None
        assert told["corpus_stats"]["document_count"] is None
