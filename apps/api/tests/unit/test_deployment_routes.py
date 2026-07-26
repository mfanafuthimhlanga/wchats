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

Mock strategy:
    - FastAPI dependency_overrides for get_current_tenant and get_async_db
    - ASGITransport(app=app) for request dispatch (no live HTTP server)
    - AsyncMock for db.get() returning mock Agent/ChecklistRun objects
    - Mock tenant with tenant.id = uuid4() matching agent.tenant_id (IDOR passes)
    - dependency_overrides.clear() in finally blocks to avoid test pollution
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

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_async_db, get_current_tenant
from app.main import app
from app.models.agent import Agent
from app.models.checklist_run import ChecklistRun
from app.models.tenant import Tenant
from app.services.capability_service import canonical_envelope_hash

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
    agent.neon_connection_string = b"fake-encrypted-bytes"
    return agent


def _make_complete_checklist_run(
    agent_id: UUID,
    run_id: UUID | None = None,
    recommendation: str = "ship",
    warnings: list | None = None,
    all_warnings_acknowledged: bool = False,
    envelope_hash: str | None = None,
    envelope_acknowledged_at=None,
) -> MagicMock:
    """Return a mock ChecklistRun with status='complete'."""
    run = MagicMock(spec=ChecklistRun)
    run.id = run_id or uuid4()
    run.agent_id = agent_id
    run.status = "complete"
    run.recommendation = recommendation
    run.report = {"eval_summary": {}, "summary": "Good.", "recommendation": recommendation}
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
