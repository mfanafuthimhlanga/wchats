"""Unit tests for app.api.v1.deployment — M8 FastAPI deployment routes.

Tests:
    TestGetChecklistRun
        test_get_detail_returns_report              — DEP-04: GET returns report + all signal sections

    TestAcknowledge
        test_acknowledge_updates_warning_acknowledgments — DEP-05: POST acknowledge updates JSONB
        test_approve_blocked_when_warnings_unacked       — 422 when warnings not all acknowledged

    TestApproveDeployment
        test_approve_sets_is_deployed_true          — DEP-06: POST approve sets is_deployed=True
        test_approve_rejects_blocked                — 422 for recommendation='block'

    TestEmbedSnippet                                — BACKLOG 7.1: one generator, both hosts
        test_snippet_carries_both_data_attributes   — data-agent AND a non-empty data-api
        test_both_hosts_come_from_settings          — neither host is a literal
        test_route_returns_the_generated_snippet    — GET /embed-snippet serves that exact string
        test_another_tenants_agent_is_404           — IDOR

Mock strategy:
    - FastAPI dependency_overrides for get_current_tenant and get_async_db
    - ASGITransport(app=app) for request dispatch (no live HTTP server)
    - AsyncMock for db.get() returning mock Agent/ChecklistRun objects
    - Mock tenant with tenant.id = uuid4() matching agent.tenant_id (IDOR passes)
    - dependency_overrides.clear() in finally blocks to avoid test pollution
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

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_async_db, get_current_tenant
from app.core.config import settings
from app.main import app
from app.models.agent import Agent
from app.models.checklist_run import ChecklistRun
from app.models.tenant import Tenant
from app.services.capability_service import canonical_envelope_hash
from app.services.deployment_service import SnippetNotConfigured, _make_iframe_snippet

# ---------------------------------------------------------------------------
# Fixture envelope row sets for the BLR-02 drift tests — Task 3 builds hashes
# from these via canonical_envelope_hash rather than hard-coding hex
# literals, so the tests cannot drift from the implementation.
# ---------------------------------------------------------------------------

ENVELOPE_ROW_SET_A = [
    {
        "skill": "issue_refund",
        "enabled": True,
        "rate_limit": "5/hour",
        "constraints": {"max_amount_cents": 5000},
        "requires_confirmation": False,
        "requires_identity_verification": False,
        "actor_mode": "always-on",
    },
]

# Differs from A in exactly one semantic field (constraints.max_amount_cents).
ENVELOPE_ROW_SET_B = [
    {
        "skill": "issue_refund",
        "enabled": True,
        "rate_limit": "5/hour",
        "constraints": {"max_amount_cents": 9999},
        "requires_confirmation": False,
        "requires_identity_verification": False,
        "actor_mode": "always-on",
    },
]


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_fake_tenant() -> MagicMock:
    """Return a mock Tenant with a stable id."""
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_ready_agent(tenant: MagicMock, agent_id: UUID | None = None) -> MagicMock:
    """Return a mock Agent in 'ready' state belonging to the given tenant."""
    agent = MagicMock(spec=Agent)
    agent.id = agent_id or uuid4()
    agent.tenant_id = tenant.id
    agent.status = "ready"
    agent.is_deployed = False
    agent.deleted_at = None
    # BACKLOG 5.1: approve now decrypts this to re-read live red-team findings
    # (guard 2b), so it must be a REAL fernet token. Leaving the old
    # b"fake-encrypted-bytes" here would make every approve test fail closed on
    # an InvalidToken and prove nothing about the guard it was written for.
    from app.core.security import fernet_encrypt

    agent.neon_connection_string = fernet_encrypt("postgresql://stub/stub")
    return agent


@pytest.fixture(autouse=True)
def _clean_live_red_team_findings():
    """Stub the tenant-DB read guard 2b performs (BACKLOG 5.1).

    Only the DATABASE READ is stubbed — the decrypt in
    `_refuse_if_a_critical_finding_is_open` still runs for real against the
    token `_make_ready_agent` now supplies, so the guard's plumbing is
    exercised here and only its query is faked. These are unit tests with no
    tenant Postgres; the guard's actual refusal behaviour is owned by
    `tests/integration/test_deploy_gate_redteam.py`, which drives it against a
    real findings table in both directions.

    Autouse rather than per-test: every approve test reaches this guard, and a
    test that forgets the stub fails with a confusing InvalidToken 422 rather
    than the thing it was asserting.
    """
    with patch(
        "app.services.deployment_service._fetch_red_team_summary_sync",
        return_value={"deployment_blocked": False, "critical_count": 0},
    ):
        yield


def _make_complete_checklist_run(
    agent_id: UUID,
    run_id: UUID | None = None,
    recommendation: str = "ship",
    warnings: list | None = None,
    all_warnings_acknowledged: bool = False,
    envelope_hash: str | None = None,
    envelope_acknowledged_at=None,
    eval_summary: dict | None = None,
) -> MagicMock:
    """Return a mock ChecklistRun with status='complete'.

    `eval_summary` DEFAULTS TO A RUN THAT INVOKED THE AGENT (audit D1, P3
    review). The approve route now refuses a stored run whose own report does
    not record `agent_invoked is True`, because `recommendation` is frozen at
    checklist time and a run completed before the D1 gate landed still says
    'ship' over an eval that scored the dataset's own reference answers. The
    old default here was `{}` — which is exactly the historical shape — so
    every approve test in this module would otherwise be asserting the refusal
    rather than what its name says. Pass `{}` deliberately to test the refusal.
    """
    run = MagicMock(spec=ChecklistRun)
    run.id = run_id or uuid4()
    run.agent_id = agent_id
    run.status = "complete"
    run.recommendation = recommendation
    run.report = {
        "eval_summary": (
            {"agent_invoked": True} if eval_summary is None else eval_summary
        ),
        "summary": "Good.",
        "recommendation": recommendation,
    }
    run.warnings = warnings if warnings is not None else []
    run.warning_acknowledgments = {}
    run.all_warnings_acknowledged = all_warnings_acknowledged
    run.approved_at = None
    run.approved_by = None
    run.created_at = datetime.now(timezone.utc)
    run.envelope_hash = envelope_hash
    run.envelope_acknowledged_at = envelope_acknowledged_at
    return run


def _make_mock_db(
    agent: MagicMock,
    run: MagicMock | None = None,
    envelope_rows: list[dict] | None = None,
) -> AsyncMock:
    """Return an async DB mock that dispatches db.get() to agent or run by type.

    db.execute(...) is scripted to return a result whose .mappings().all()
    yields envelope_rows (empty list by default) — the route's
    _fetch_envelope_rows goes through db.execute, not db.get.
    """
    mock_db = AsyncMock()

    async def _fake_get(model, pk):
        if model is Agent:
            return agent
        if model is ChecklistRun:
            return run
        return None

    mock_db.get.side_effect = _fake_get

    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = (
        envelope_rows if envelope_rows is not None else []
    )
    mock_db.execute.return_value = mock_result

    return mock_db


# ---------------------------------------------------------------------------
# TestGetChecklistRun
# ---------------------------------------------------------------------------


class TestGetChecklistRun:
    """Tests for GET /api/v1/agents/{agent_id}/checklist-runs/{run_id} (DEP-04)."""

    async def test_get_detail_returns_report(self):
        """GET /checklist-runs/{run_id} returns 200 with 'run' dict containing report (DEP-04)."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        run_id = uuid4()

        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_run = _make_complete_checklist_run(
            agent_id=agent_id,
            run_id=run_id,
            recommendation="ship",
        )
        mock_db = _make_mock_db(mock_agent, mock_run)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{agent_id}/checklist-runs/{run_id}",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert "run" in body
        run_data = body["run"]
        assert run_data["status"] == "complete"
        assert "report" in run_data
        assert run_data["recommendation"] == "ship"
        assert "warnings" in run_data
        assert "warning_acknowledgments" in run_data
        assert "all_warnings_acknowledged" in run_data


# ---------------------------------------------------------------------------
# TestAcknowledge
# ---------------------------------------------------------------------------


class TestAcknowledge:
    """Tests for POST /agents/{agent_id}/checklist-runs/{run_id}/acknowledge (DEP-05)."""

    async def test_acknowledge_updates_warning_acknowledgments(self):
        """POST /acknowledge with all warning_ids sets all_warnings_acknowledged=True (DEP-05)."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        run_id = uuid4()

        # Run has one warning that needs acknowledgment
        warnings = [
            {
                "warning_id": "test_warning",
                "category": "eval_quality",
                "message": "Low coverage",
                "severity_level": "warning",
            }
        ]
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_run = _make_complete_checklist_run(
            agent_id=agent_id,
            run_id=run_id,
            recommendation="ship_with_warnings",
            warnings=warnings,
            all_warnings_acknowledged=False,
        )
        mock_db = _make_mock_db(mock_agent, mock_run)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{agent_id}/checklist-runs/{run_id}/acknowledge",
                    json={"warning_ids": ["test_warning"]},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        # After acknowledging the only warning, all_warnings_acknowledged should be True
        assert body["all_warnings_acknowledged"] is True

    async def test_approve_blocked_when_warnings_unacked(self):
        """POST /approve-deployment returns 422 when ship_with_warnings and not all acked (DEP-06)."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        run_id = uuid4()

        warnings = [
            {
                "warning_id": "unacked_warning",
                "category": "security",
                "message": "Unacknowledged security concern",
                "severity_level": "warning",
            }
        ]
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        # ship_with_warnings and all_warnings_acknowledged=False — approval should be blocked
        mock_run = _make_complete_checklist_run(
            agent_id=agent_id,
            run_id=run_id,
            recommendation="ship_with_warnings",
            warnings=warnings,
            all_warnings_acknowledged=False,
        )
        mock_db = _make_mock_db(mock_agent, mock_run)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{agent_id}/approve-deployment",
                    json={"checklist_run_id": str(run_id)},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# test_approve_deployment_envelope_drift_422 (module scope — 18-VALIDATION.md
# pins this exact node id; it must NOT be class-scoped)
# ---------------------------------------------------------------------------


async def test_approve_deployment_envelope_drift_422():
    """T-18-BLR-03: POST /approve-deployment returns 422 when the live
    envelope hash differs from the hash the run recorded — asserted at the
    route via a real ASGI request through ASGITransport, not below it."""
    fake_tenant = _make_fake_tenant()
    agent_id = uuid4()
    run_id = uuid4()

    mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
    mock_agent.is_deployed = False

    recorded_hash = canonical_envelope_hash(ENVELOPE_ROW_SET_A)
    mock_run = _make_complete_checklist_run(
        agent_id=agent_id,
        run_id=run_id,
        recommendation="ship",
        warnings=[],
        all_warnings_acknowledged=True,
        envelope_hash=recorded_hash,
    )
    # Live envelope is ROW_SET_B — differs from the recorded hash's
    # ROW_SET_A in one semantic field.
    mock_db = _make_mock_db(mock_agent, mock_run, envelope_rows=ENVELOPE_ROW_SET_B)

    app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
    app.dependency_overrides[get_async_db] = lambda: mock_db

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/agents/{agent_id}/approve-deployment",
                json={"checklist_run_id": str(run_id)},
                headers={"X-API-Key": "vrd_live_test"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "Capability envelope changed" in response.json()["detail"]
    # The 422 must precede the mutation — asserting the status code alone
    # would not catch a route that flipped the flag and then raised.
    assert mock_agent.is_deployed is False


# ---------------------------------------------------------------------------
# TestApproveDeployment
# ---------------------------------------------------------------------------


class TestApproveDeployment:
    """Tests for POST /agents/{agent_id}/approve-deployment (DEP-06)."""

    async def test_approve_sets_is_deployed_true(self):
        """POST /approve-deployment returns 200 with deployed=True and iframe_snippet (DEP-06)."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        run_id = uuid4()

        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_agent.is_deployed = False

        # recommendation='ship' and all_warnings_acknowledged=True — approve should succeed.
        # Phase 18 BLR-02: envelope_hash must match the live envelope (the
        # mock db's default envelope_rows is []) or the new fourth
        # validation would 422 on drift — canonical_envelope_hash([]) keeps
        # this pre-existing DEP-06 test green under the new fail-closed gate.
        mock_run = _make_complete_checklist_run(
            agent_id=agent_id,
            run_id=run_id,
            recommendation="ship",
            warnings=[],
            all_warnings_acknowledged=True,
            envelope_hash=canonical_envelope_hash([]),
        )
        mock_db = _make_mock_db(mock_agent, mock_run)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{agent_id}/approve-deployment",
                    json={"checklist_run_id": str(run_id)},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["deployed"] is True
        assert "iframe_snippet" in body
        assert "widget.wchats.app" in body["iframe_snippet"]

    async def test_approve_rejects_blocked(self):
        """POST /approve-deployment returns 422 with 'blocked' in detail for blocked runs (DEP-06)."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        run_id = uuid4()

        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_agent.is_deployed = False

        # recommendation='block' — approval must be rejected
        mock_run = _make_complete_checklist_run(
            agent_id=agent_id,
            run_id=run_id,
            recommendation="block",
            warnings=[],
            all_warnings_acknowledged=False,
        )
        mock_db = _make_mock_db(mock_agent, mock_run)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{agent_id}/approve-deployment",
                    json={"checklist_run_id": str(run_id)},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422
        detail = response.json().get("detail", "")
        assert "blocked" in detail.lower(), (
            f"Expected 'blocked' in error detail, got: {detail!r}"
        )

    async def test_approve_succeeds_when_envelope_hash_matches(self):
        """A recorded hash matching the live envelope approves and stamps
        envelope_acknowledged_at (T-18-BLR-02 happy path)."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        run_id = uuid4()

        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_agent.is_deployed = False

        matching_hash = canonical_envelope_hash(ENVELOPE_ROW_SET_A)
        mock_run = _make_complete_checklist_run(
            agent_id=agent_id,
            run_id=run_id,
            recommendation="ship",
            warnings=[],
            all_warnings_acknowledged=True,
            envelope_hash=matching_hash,
        )
        mock_db = _make_mock_db(mock_agent, mock_run, envelope_rows=ENVELOPE_ROW_SET_A)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{agent_id}/approve-deployment",
                    json={"checklist_run_id": str(run_id)},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["deployed"] is True
        assert mock_run.envelope_acknowledged_at is not None

    async def test_approve_rejects_run_with_null_envelope_hash(self):
        """T-18-BLR-02: a NULL recorded hash (pre-0019 historical run, or a
        run whose hash collector failed) is unapprovable — an absent
        acknowledgement is drift, never a match."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        run_id = uuid4()

        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_agent.is_deployed = False

        mock_run = _make_complete_checklist_run(
            agent_id=agent_id,
            run_id=run_id,
            recommendation="ship",
            warnings=[],
            all_warnings_acknowledged=True,
            envelope_hash=None,
        )
        mock_db = _make_mock_db(mock_agent, mock_run, envelope_rows=ENVELOPE_ROW_SET_A)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{agent_id}/approve-deployment",
                    json={"checklist_run_id": str(run_id)},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422
        assert mock_agent.is_deployed is False

    async def test_envelope_drift_check_runs_after_the_three_existing_validations(self):
        """A 'block' run with a drifting envelope hash still reports the
        blocked-deployment detail — proving the new check is appended after
        the three shipped validations, not inserted ahead of them, so it
        cannot mask a more severe pre-existing gate."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        run_id = uuid4()

        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_agent.is_deployed = False

        recorded_hash = canonical_envelope_hash(ENVELOPE_ROW_SET_A)
        mock_run = _make_complete_checklist_run(
            agent_id=agent_id,
            run_id=run_id,
            recommendation="block",
            warnings=[],
            all_warnings_acknowledged=False,
            envelope_hash=recorded_hash,
        )
        mock_db = _make_mock_db(mock_agent, mock_run, envelope_rows=ENVELOPE_ROW_SET_B)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{agent_id}/approve-deployment",
                    json={"checklist_run_id": str(run_id)},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422
        detail = response.json().get("detail", "")
        assert "blocked" in detail.lower(), (
            f"Expected the blocked-deployment detail, not the envelope "
            f"drift detail, got: {detail!r}"
        )


# ---------------------------------------------------------------------------
# TestApproveRefusesAnUninvokedRun (audit D1, P3 review)
# ---------------------------------------------------------------------------


class TestApproveRefusesAnUninvokedRun:
    """The gate does not reach a checklist run that already completed.

    `apply_signal_evidence_gate` has exactly one caller — the checklist Celery
    task — and `agent.is_deployed` has exactly one writer: this route, which
    validates against `run.recommendation`, a value FROZEN by whatever gate was
    running the day the row was written. So P3 refusing an uninvoked eval at
    checklist time closes nothing for the runs that already exist: every
    readiness check completed before this release carries a 'ship' computed
    over the tautology at eval.py:374-375, its status is 'complete', its
    recommendation is not 'block', its warnings do not apply and its envelope
    hash has not moved. `{"deployed": true}`, and the agent this phase exists
    to refuse goes live.

    checklist_runs has no TTL and no gate-version column
    (app/models/checklist_run.py), so the run's own evidence has to be re-read
    at approve time rather than aged out.
    """

    async def _post(self, run):
        fake_tenant = _make_fake_tenant()
        agent_id = run.agent_id
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_agent.is_deployed = False
        mock_db = _make_mock_db(mock_agent, run)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{agent_id}/approve-deployment",
                    json={"checklist_run_id": str(run.id)},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()
        return response, mock_agent

    def _historical_run(self, **kw):
        """A checklist run exactly as one written before this branch looks:
        complete, 'ship', warnings acknowledged, envelope hash matching the
        live (empty) envelope, and an eval_summary with excellent scores and
        no invocation claim anywhere in it."""
        return _make_complete_checklist_run(
            agent_id=kw.pop("agent_id", uuid4()),
            recommendation="ship",
            warnings=[],
            all_warnings_acknowledged=True,
            envelope_hash=canonical_envelope_hash([]),
            eval_summary=kw.pop(
                "eval_summary",
                {
                    "eval_signal": "measured",
                    "pass_rates": {"faithfulness": 0.99},
                    "scenario_count": 30,
                },
            ),
            **kw,
        )

    async def test_a_pre_d1_checklist_run_is_no_longer_approvable(self):
        """THE ONE THAT MATTERS: every run stored before this release."""
        run = self._historical_run()

        response, mock_agent = await self._post(run)

        assert response.status_code == 422, (
            "a readiness check decided over the tautology was approved because "
            "its recommendation was frozen at 'ship' before the gate existed"
        )
        assert mock_agent.is_deployed is False, (
            "the 422 must precede the mutation — asserting the status code "
            "alone would not catch a route that flipped the flag and raised"
        )

    async def test_a_run_that_recorded_false_is_refused_too(self):
        run = self._historical_run(eval_summary={"agent_invoked": False})

        response, mock_agent = await self._post(run)

        assert response.status_code == 422
        assert mock_agent.is_deployed is False

    async def test_a_run_with_no_report_at_all_is_refused(self):
        """A run that never reached step 6 of the checklist task has report
        NULL. A gate that cannot read its evidence has not been satisfied."""
        run = self._historical_run()
        run.report = None

        response, mock_agent = await self._post(run)

        assert response.status_code == 422
        assert mock_agent.is_deployed is False

    async def test_the_detail_names_the_step_the_owner_has_to_take_first(self):
        """"Re-run the checklist" is not enough: the checklist would reach the
        same verdict over the same stale eval. A fresh eval comes first."""
        run = self._historical_run()

        response, _ = await self._post(run)

        detail = response.json().get("detail", "")
        assert "Evaluation page" in detail, f"got: {detail!r}"
        assert "readiness check" in detail, f"got: {detail!r}"

    async def test_a_run_that_records_the_invocation_still_approves(self):
        """The refusal has to be able to NOT fire."""
        run = self._historical_run(eval_summary={"agent_invoked": True})

        response, mock_agent = await self._post(run)

        assert response.status_code == 200
        assert response.json()["deployed"] is True
        assert mock_agent.is_deployed is True

    async def test_a_blocked_run_still_reports_the_blocked_detail(self):
        """Ordering, upward: the new check sits behind the three shipped
        validations so it cannot mask a more severe pre-existing gate."""
        run = self._historical_run()
        run.recommendation = "block"
        run.all_warnings_acknowledged = False

        response, _ = await self._post(run)

        assert response.status_code == 422
        assert "blocked" in response.json().get("detail", "").lower()

    async def test_it_is_reported_ahead_of_envelope_drift(self):
        """Ordering, downward. Both are wrong; only one of them tells the owner
        they must run a fresh eval before the checklist can reach any other
        verdict, and "Capability envelope changed — re-run the checklist" does
        not mention it."""
        run = self._historical_run()
        run.envelope_hash = canonical_envelope_hash(ENVELOPE_ROW_SET_A)

        fake_tenant = _make_fake_tenant()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=run.agent_id)
        mock_agent.is_deployed = False
        mock_db = _make_mock_db(mock_agent, run, envelope_rows=ENVELOPE_ROW_SET_B)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{run.agent_id}/approve-deployment",
                    json={"checklist_run_id": str(run.id)},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422
        detail = response.json().get("detail", "")
        assert "Capability envelope" not in detail, f"got: {detail!r}"
        assert "Evaluation page" in detail, f"got: {detail!r}"


# ---------------------------------------------------------------------------
# TestChecklistReadEnvelopeDrift
# ---------------------------------------------------------------------------


class TestChecklistReadEnvelopeDrift:
    """Tests for envelope_drift surfaced on the checklist reads (CAP-04)."""

    async def test_get_detail_reports_envelope_drift_true_when_hashes_differ(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        run_id = uuid4()

        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        recorded_hash = canonical_envelope_hash(ENVELOPE_ROW_SET_A)
        mock_run = _make_complete_checklist_run(
            agent_id=agent_id,
            run_id=run_id,
            recommendation="ship",
            envelope_hash=recorded_hash,
        )
        mock_db = _make_mock_db(mock_agent, mock_run, envelope_rows=ENVELOPE_ROW_SET_B)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{agent_id}/checklist-runs/{run_id}",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        run_data = response.json()["run"]
        assert run_data["envelope_drift"] is True
        assert "envelope_hash" in run_data
        assert "envelope_acknowledged_at" in run_data

    async def test_get_detail_reports_envelope_drift_false_when_hashes_match(self):
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        run_id = uuid4()

        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        matching_hash = canonical_envelope_hash(ENVELOPE_ROW_SET_A)
        mock_run = _make_complete_checklist_run(
            agent_id=agent_id,
            run_id=run_id,
            recommendation="ship",
            envelope_hash=matching_hash,
        )
        mock_db = _make_mock_db(mock_agent, mock_run, envelope_rows=ENVELOPE_ROW_SET_A)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{agent_id}/checklist-runs/{run_id}",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        run_data = response.json()["run"]
        assert run_data["envelope_drift"] is False

    async def test_list_route_computes_live_hash_once(self):
        """The list route computes the live envelope hash once per request,
        not once per run — the mocked db.execute is awaited exactly twice
        total for a two-run page (one envelope-projection query, one runs
        query), proving the hash is not recomputed per run."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()

        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        run_1 = _make_complete_checklist_run(agent_id=agent_id, recommendation="ship")
        run_2 = _make_complete_checklist_run(agent_id=agent_id, recommendation="ship")

        mock_db = _make_mock_db(mock_agent, envelope_rows=ENVELOPE_ROW_SET_A)

        # The route computes the live envelope hash first, then queries
        # checklist_runs — script db.execute's two sequential calls in that
        # order.
        envelope_result = MagicMock()
        envelope_result.mappings.return_value.all.return_value = ENVELOPE_ROW_SET_A

        runs_result = MagicMock()
        runs_result.scalars.return_value.all.return_value = [run_1, run_2]

        mock_db.execute = AsyncMock(side_effect=[envelope_result, runs_result])

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{agent_id}/checklist-runs",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert len(body["runs"]) == 2
        assert mock_db.execute.await_count == 2


class TestApproveFailsClosedWhenLiveFindingsCannotBeRead:
    """Guard 2b must refuse when it CANNOT check, not only when it finds something.

    BACKLOG `5.1`. The guard re-reads live red-team findings from the tenant DB
    at approve time. If that read fails — an undecryptable connection string, an
    unreachable database, a credential rotated out from under the row — the
    honest answer is "unknown", and unknown is never permission to deploy.
    **"I could not check" is not "there is nothing to find."**

    This class exists because the first mutation proof written for the
    fail-closed branch was INVALID: it mutated the route to fail open *and*
    reverted the fixture's token in the same step, so the two changes
    compensated and the suite stayed green in both states. A mutation that does
    not modify what it claims to modify is not weak evidence, it is none
    (retro.md, second standing rule). Nothing tested this branch at all — so
    here it is, tested.
    """

    async def _post_with_unreadable_connection(self):
        # envelope_hash must MATCH the (empty) live projection, or guard 4
        # refuses for drift and this test would pass without guard 2b existing
        # at all. Observed: with the guard mutated to fail open, the status-code
        # assertion below still saw 422 -- from envelope drift. A 422 proves
        # nothing on its own in a route with five guards; the detail does.
        run = _make_complete_checklist_run(
            agent_id=uuid4(),
            recommendation="ship",
            envelope_hash=canonical_envelope_hash([]),
        )
        fake_tenant = _make_fake_tenant()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=run.agent_id)
        # Not a valid fernet token — fernet_decrypt raises InvalidToken, which
        # is the shape of every real "cannot read the tenant DB" failure.
        mock_agent.neon_connection_string = b"not-a-fernet-token"
        mock_db = _make_mock_db(mock_agent, run)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/agents/{run.agent_id}/approve-deployment",
                    json={"checklist_run_id": str(run.id)},
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()
        return response, mock_agent

    @pytest.mark.anyio
    async def test_it_refuses_rather_than_approving_unchecked(self):
        response, mock_agent = await self._post_with_unreadable_connection()

        assert response.status_code == 422, (
            "approve-deployment succeeded while it was UNABLE to read the agent's "
            "live red-team findings. An unverifiable agent must not deploy — "
            f"got {response.status_code}: {response.text[:300]}"
        )
        assert "could not verify" in response.json()["detail"].lower(), (
            "a 422 arrived, but not from guard 2b -- this route has five guards "
            "and any of them yields 422, so the status code alone cannot show "
            f"which refused. detail={response.json()['detail']!r}"
        )
        assert mock_agent.is_deployed is False, (
            "the agent was marked deployed despite the refusal"
        )

    @pytest.mark.anyio
    async def test_the_detail_says_it_could_not_verify_not_that_it_found_something(self):
        """The two refusals mean different things and must read differently.

        "3 critical findings are open" tells the owner to contain them.
        "could not verify" tells them to fix their database. Collapsing the two
        sends them to the wrong place — the same failure `1.30` records, where
        a timeout was reported in the vocabulary of a model-quality problem.
        """
        response, _ = await self._post_with_unreadable_connection()
        detail = response.json()["detail"].lower()
        assert "could not verify" in detail, detail
        assert "critical red-team finding(s) are open" not in detail, detail


class TestApproveRefusesAnOpenCriticalFinding:
    """Guard 2b's HEADLINE direction, at unit level (BACKLOG `5.1`, `1.33`).

    An adversarial review found that disabling the refusal itself --
    `if summary.get("deployment_blocked")` -> `if False` -- left this module at
    **21/21 green** while approve-deployment shipped an agent with open critical
    findings. The only coverage of that direction lived in
    `tests/integration/test_deploy_gate_redteam.py`, which is skipped unless
    `INTEGRATION_TESTS_ENABLED=1` and a local Postgres exists. **A skipped test
    is unobserved, never a pass** -- so the behaviour the whole `5.1` fix exists
    to deliver had no runnable proof at all.

    These tests override the module's autouse stub, which reports a clean
    summary for every other test here.
    """

    def _blocked_summary(self):
        return {"deployment_blocked": True, "critical_count": 3}

    async def _post(self):
        run = _make_complete_checklist_run(
            agent_id=uuid4(),
            recommendation="ship",
            envelope_hash=canonical_envelope_hash([]),
        )
        fake_tenant = _make_fake_tenant()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=run.agent_id)
        mock_db = _make_mock_db(mock_agent, run)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        try:
            with patch(
                "app.services.deployment_service._fetch_red_team_summary_sync",
                return_value=self._blocked_summary(),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/v1/agents/{run.agent_id}/approve-deployment",
                        json={"checklist_run_id": str(run.id)},
                        headers={"X-API-Key": "vrd_live_test"},
                    )
        finally:
            app.dependency_overrides.clear()
        return response, mock_agent

    @pytest.mark.anyio
    async def test_an_open_critical_finding_refuses_the_deployment(self):
        response, mock_agent = await self._post()

        assert response.status_code == 422, (
            "approve-deployment SHIPPED an agent with 3 open critical red-team "
            f"findings. got {response.status_code}: {response.text[:300]}"
        )
        assert mock_agent.is_deployed is False, (
            "the agent was marked deployed despite the refusal"
        )

    @pytest.mark.anyio
    async def test_the_detail_names_the_findings_not_a_verification_failure(self):
        """Five guards return 422 here, so the status code identifies nothing.

        This assertion is what distinguishes "an open finding refused you" from
        "I could not check" and from envelope drift. The run's envelope hash is
        matched to the live projection above precisely so guard 4 cannot be the
        one answering.
        """
        response, _ = await self._post()
        detail = response.json()["detail"].lower()
        assert "critical red-team finding(s) are open" in detail, detail
        assert "could not verify" not in detail, detail
        assert "3" in detail, f"the count should reach the operator: {detail}"


# ---------------------------------------------------------------------------
# TestEmbedSnippet — BACKLOG 7.1
# ---------------------------------------------------------------------------


class TestEmbedSnippet:
    """GET /api/v1/agents/{agent_id}/embed-snippet — the single generator.

    The defect these pin: `_make_iframe_snippet` hardcoded the CDN host and
    emitted NO `data-api` attribute at all. `apps/widget/embed/widget.js`
    resolves its API base from `data-api` and only warns when it is absent, so
    the tag the API issued produced a widget that rendered on the customer's
    site and could not talk to anything. Meanwhile the console rendered a
    second snippet it computed itself.

    `test_snippet_carries_both_data_attributes` fails on the old generator (no
    `data-api=`); `test_both_hosts_come_from_settings` fails on any generator
    that hardcodes either host, because it moves both settings to values that
    appear nowhere in the source.
    """

    def test_snippet_carries_both_data_attributes(self):
        """The tag names the agent AND the API it must call."""
        agent_id = str(uuid4())
        snippet = _make_iframe_snippet(agent_id)

        assert f'data-agent="{agent_id}"' in snippet, snippet
        assert "data-api=" in snippet, snippet
        # An empty data-api is the same failure as an absent one — the loader
        # falls through to warn-and-continue either way.
        assert 'data-api=""' not in snippet, snippet

    def test_both_hosts_come_from_settings(self, monkeypatch):
        """Neither host is a literal in the generator."""
        monkeypatch.setattr(settings, "WIDGET_CDN_BASE", "https://cdn.test.example/")
        monkeypatch.setattr(settings, "PUBLIC_API_BASE", "https://api.test.example/")

        snippet = _make_iframe_snippet(str(uuid4()))

        # rstrip("/") on both, so a base configured with a trailing slash
        # cannot produce a double slash in the URL a customer pastes.
        assert 'src="https://cdn.test.example/widget.js"' in snippet, snippet
        assert 'data-api="https://api.test.example"' in snippet, snippet
        assert "widget.wchats.app" not in snippet, snippet

    async def test_route_returns_the_generated_snippet(self):
        """The console's snippet is the API's snippet, byte for byte."""
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_mock_db(mock_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{agent_id}/embed-snippet",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["agent_id"] == str(agent_id)
        assert body["snippet"] == _make_iframe_snippet(str(agent_id))
        assert f'data-agent="{agent_id}"' in body["snippet"]
        assert "data-api=" in body["snippet"]

    async def test_another_tenants_agent_is_404(self):
        """An embed tag names a live endpoint — IDOR is a 404, not a snippet."""
        fake_tenant = _make_fake_tenant()
        other_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(other_tenant, agent_id=agent_id)
        mock_db = _make_mock_db(mock_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{agent_id}/embed-snippet",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# TestSnippetApiBaseIsRefusedInProduction — D2
# ---------------------------------------------------------------------------


class TestSnippetApiBaseIsRefusedInProduction:
    """PUBLIC_API_BASE's localhost default must not ship a snippet.

    The loader warns only when the API base is EMPTY
    (apps/widget/embed/widget.js), so the non-empty `http://localhost:8000`
    default is silent: the snippet renders on the customer's site, every
    visitor's browser calls its own machine, and http://localhost is
    potentially-trustworthy so an https page does not mixed-content block it
    either. Mirrors storage_service._get_s3()'s refusal of S3_ENDPOINT_URL.
    """

    @pytest.mark.parametrize(
        "api_base",
        [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "https://localhost",
            "http://0.0.0.0:8000",
            "",
            "   ",
        ],
    )
    def test_refused_in_production(self, monkeypatch, api_base):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "PUBLIC_API_BASE", api_base)

        with pytest.raises(SnippetNotConfigured) as exc_info:
            _make_iframe_snippet(str(uuid4()))

        assert "PUBLIC_API_BASE" in str(exc_info.value)

    @pytest.mark.parametrize(
        "api_base", ["http://localhost:8000", "http://127.0.0.1:8000", ""]
    )
    def test_allowed_in_development(self, monkeypatch, api_base):
        """The same values are the point of the default outside production."""
        monkeypatch.setattr(settings, "ENVIRONMENT", "development")
        monkeypatch.setattr(settings, "PUBLIC_API_BASE", api_base)

        snippet = _make_iframe_snippet(str(uuid4()))

        assert f'data-api="{api_base}"' in snippet, snippet

    def test_a_real_public_origin_is_allowed_in_production(self, monkeypatch):
        """Only loopback and empty are refused — a configured host still works."""
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "PUBLIC_API_BASE", "https://api.wchats.app/")

        snippet = _make_iframe_snippet(str(uuid4()))

        assert 'data-api="https://api.wchats.app"' in snippet, snippet

    async def test_route_answers_503_rather_than_issuing_a_dead_snippet(
        self, monkeypatch
    ):
        """A snippet a customer cannot use is an unavailable service, not a 200.

        Same translation documents.py applies to StorageNotConfigured.
        """
        fake_tenant = _make_fake_tenant()
        agent_id = uuid4()
        mock_agent = _make_ready_agent(fake_tenant, agent_id=agent_id)
        mock_db = _make_mock_db(mock_agent)

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "PUBLIC_API_BASE", "http://localhost:8000")

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/agents/{agent_id}/embed-snippet",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 503, response.text
        assert "PUBLIC_API_BASE" in response.json()["detail"]
