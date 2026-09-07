"""POST /agents/{agent_id}/golden-scenarios/drafts creates a job and dispatches ids only (#203)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.api.deps import get_async_db, get_current_tenant
from app.main import app
from app.models.agent import Agent
from app.models.tenant import Tenant


def _tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.deleted_at = None
    return tenant


def _agent(tenant: Tenant, conn_string: bytes | None = b"encrypted") -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.tenant_id = tenant.id
    agent.deleted_at = None
    agent.neon_connection_string = conn_string
    return agent


async def _post(agent_id, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            f"/api/v1/agents/{agent_id}/golden-scenarios/drafts",
            headers={"X-API-Key": "vrd_live_test"},
            json=body,
        )


def _override(tenant, agent):
    app.dependency_overrides[get_current_tenant] = lambda: tenant
    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=agent)
    mock_db.add = MagicMock()
    app.dependency_overrides[get_async_db] = lambda: mock_db
    return mock_db


class TestGoldenDraftRoute:
    async def test_missing_or_foreign_agent_is_404(self):
        _override(_tenant(), None)
        try:
            response = await _post(uuid4(), {"n": 10})
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 404

    async def test_agent_without_tenant_db_is_404(self):
        tenant = _tenant()
        agent = _agent(tenant, conn_string=None)
        _override(tenant, agent)
        try:
            response = await _post(agent.id, {"n": 10})
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 404

    async def test_n_outside_ten_to_thirty_is_422(self):
        tenant = _tenant()
        agent = _agent(tenant)
        _override(tenant, agent)
        try:
            too_few = await _post(agent.id, {"n": 3})
            too_many = await _post(agent.id, {"n": 31})
        finally:
            app.dependency_overrides.clear()
        assert (too_few.status_code, too_many.status_code) == (422, 422)

    async def test_202_creates_a_golden_draft_job_and_dispatches_ids_only(self):
        tenant = _tenant()
        agent = _agent(tenant)
        mock_db = _override(tenant, agent)
        job_id = uuid4()

        async def _refresh(job):
            job.id = job_id

        mock_db.refresh = AsyncMock(side_effect=_refresh)
        try:
            with patch("app.api.v1.evals.draft_golden_scenarios") as task:
                response = await _post(agent.id, {"n": 12})
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 202
        assert response.json() == {
            "status": "queued", "job_id": str(job_id), "agent_id": str(agent.id), "n": 12,
        }
        job = mock_db.add.call_args.args[0]
        assert (job.kind, job.status, job.agent_id) == ("golden_draft", "pending", agent.id)
        kwargs = task.apply_async.call_args.kwargs
        assert kwargs["queue"] == "runtime"
        assert kwargs["kwargs"] == {"job_id": str(job_id), "agent_id": str(agent.id), "n": 12}
        assert "conn" not in str(kwargs["kwargs"])
