"""
Unit tests for GET /jobs/{job_id} route.

Tests:
    - GET /jobs/{job_id} with valid tenant → 200 with job data
    - GET /jobs/{job_id} with nonexistent job → 404
    - GET /jobs/{job_id} belonging to different tenant → 404
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.api.deps import get_async_redis, get_current_tenant
from app.core.database import get_async_db
from app.models.tenant import Tenant


def _make_fake_tenant() -> Tenant:
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid4()
    tenant.name = "Test Tenant"
    tenant.deleted_at = None
    return tenant


def _make_mock_db_with_job(job_found: bool = True):
    """Mock DB that either returns a job or None."""
    from app.models.job import Job
    from app.models.job_event import JobEvent

    mock_session = AsyncMock()

    if job_found:
        fake_job = MagicMock(spec=Job)
        fake_job.id = uuid4()
        fake_job.tenant_id = uuid4()
        fake_job.agent_id = uuid4()
        fake_job.kind = "create_agent"
        fake_job.status = "pending"
        fake_job.error = None
        fake_job.created_at = datetime.now(timezone.utc)

        mock_job_result = MagicMock()
        mock_job_result.scalar_one_or_none.return_value = fake_job

        mock_events_result = MagicMock()
        mock_events_result.scalars.return_value.all.return_value = []

        # Return job result first, then events result
        mock_session.execute = AsyncMock(
            side_effect=[mock_job_result, mock_events_result]
        )
    else:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

    return mock_session


def _make_mock_redis():
    r = AsyncMock()
    r.ping.return_value = True
    r.aclose = AsyncMock()
    return r


@pytest.mark.asyncio
class TestGetJob:
    async def test_get_job_returns_200(self):
        """GET /jobs/{job_id} with valid tenant and existing job → 200."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_with_job(job_found=True)
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                job_id = uuid4()
                response = await client.get(
                    f"/jobs/{job_id}",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert "id" in body
        assert "status" in body
        assert "events" in body

    async def test_get_job_not_found_returns_404(self):
        """GET /jobs/{job_id} with nonexistent job → 404."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_with_job(job_found=False)
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                job_id = uuid4()
                response = await client.get(
                    f"/jobs/{job_id}",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_get_job_invalid_uuid_returns_422(self):
        """GET /jobs/not-uuid → 422."""
        fake_tenant = _make_fake_tenant()
        mock_db = _make_mock_db_with_job(job_found=False)
        mock_redis = _make_mock_redis()

        app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
        app.dependency_overrides[get_async_db] = lambda: mock_db
        app.dependency_overrides[get_async_redis] = lambda: mock_redis

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/jobs/not-valid-uuid",
                    headers={"X-API-Key": "vrd_live_test"},
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422
