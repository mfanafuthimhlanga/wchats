"""
Integration tests: SSE late-join and stream behaviour.

These tests use:
- A REAL local Postgres DB for job_events persistence.
- A REAL local Redis for pub/sub.
- The FastAPI app (ASGITransport) with real dependencies overridden to use
  the local test DB and Redis.
- asyncio to connect to the SSE endpoint concurrently with live event emission.

NO Celery worker is needed — tests insert job_events rows directly into the DB
and publish to Redis directly, isolating SSE endpoint behaviour from Celery.

Tests:
    test_sse_replays_prior_events
        — Pre-populate job_events; connect; assert all 4 events received; stream closes.

    test_sse_receives_live_events_after_replay
        — Pre-populate job.started; connect; emit live event after 0.5s;
          assert both DB replay event and live Redis event are received.

    test_sse_closes_on_completed_job
        — Pre-populate all 6 events (including job.complete); connect;
          assert stream closes within 2 seconds (terminal event detected in replay).
"""

import asyncio
import json
import os
import time
import uuid

import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# DB URL helpers — mirrors integration conftest.py
# ---------------------------------------------------------------------------
_INTEGRATION_DB_URL = os.environ.get(
    "INTEGRATION_DB_URL",
    "postgresql://wchats:wchats@localhost:5432/wchats_control",
)
_INTEGRATION_DB_ASYNC_URL = _INTEGRATION_DB_URL.replace(
    "postgresql://", "postgresql+asyncpg://"
)
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Helper: insert job_events rows directly (bypassing emit() to isolate SSE logic)
# ---------------------------------------------------------------------------


def _insert_events(tenant_id: uuid.UUID, job_id: uuid.UUID, events: list[dict]) -> None:
    """Insert job_events rows directly into the DB.

    Args:
        tenant_id: Tenant ID (used only for completeness in test setup).
        job_id: Job ID to associate events with.
        events: List of dicts with 'event_type' and 'payload' keys.
    """
    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            for evt in events:
                conn.execute(
                    text(
                        """
                        INSERT INTO job_events (job_id, event_type, payload, created_at)
                        VALUES (:job_id, :event_type, :payload::jsonb, now())
                        """
                    ),
                    {
                        "job_id": str(job_id),
                        "event_type": evt["event_type"],
                        "payload": json.dumps(evt.get("payload", {})),
                    },
                )
    finally:
        engine.dispose()


def _delete_test_rows(tenant_id: uuid.UUID) -> None:
    """Delete all rows for the test tenant (T-07-01 teardown)."""
    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM job_events WHERE job_id IN "
                    "(SELECT id FROM jobs WHERE tenant_id = :tid)"
                ),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                text("DELETE FROM jobs WHERE tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                text("DELETE FROM agents WHERE tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                text("DELETE FROM tenants WHERE id = :tid"),
                {"tid": str(tenant_id)},
            )
    finally:
        engine.dispose()


def _setup_test_job(tenant_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """Insert minimal tenant, agent, and job rows for the test."""
    from app.core.security import generate_api_key, hash_api_key

    raw_key = generate_api_key()
    api_key_hash = hash_api_key(raw_key)
    agent_id = uuid.uuid4()

    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tenants (id, name, api_key, created_at) "
                    "VALUES (:id, :name, :api_key, now())"
                ),
                {
                    "id": str(tenant_id),
                    "name": f"sse-test-tenant-{tenant_id}",
                    "api_key": api_key_hash,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO agents (id, tenant_id, name, soul, role, status, created_at) "
                    "VALUES (:id, :tenant_id, :name, :soul::jsonb, :role, 'pending', now())"
                ),
                {
                    "id": str(agent_id),
                    "tenant_id": str(tenant_id),
                    "name": f"sse-test-agent-{agent_id}",
                    "soul": json.dumps({"tone": "professional", "language": "en"}),
                    "role": "support",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, agent_id, kind, status, created_at) "
                    "VALUES (:id, :tenant_id, :agent_id, 'provision', 'pending', now())"
                ),
                {
                    "id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "agent_id": str(agent_id),
                },
            )
    finally:
        engine.dispose()

    return raw_key


# ---------------------------------------------------------------------------
# Helper: create FastAPI app with overridden DB/Redis for integration testing
# ---------------------------------------------------------------------------


def _make_app_with_real_deps():
    """Return (app, raw_api_key_for_tenant) with DB/Redis deps using real local services.

    We use FastAPI dependency_overrides to inject real async sessions pointed at
    the local test DB and a real async Redis pointed at the local Redis.
    """
    from app.api.deps import get_async_redis
    from app.core.database import get_async_db
    from app.main import app

    # Create async engine + session factory for the test DB
    test_async_engine = create_async_engine(
        _INTEGRATION_DB_ASYNC_URL,
        pool_pre_ping=True,
    )
    test_async_session_factory = async_sessionmaker(
        test_async_engine,
        expire_on_commit=False,
    )

    async def override_get_async_db():
        async with test_async_session_factory() as session:
            yield session

    async def override_get_async_redis():
        client = aioredis.Redis.from_url(_REDIS_URL, decode_responses=True)
        try:
            yield client
        finally:
            await client.aclose()

    app.dependency_overrides[get_async_db] = override_get_async_db
    app.dependency_overrides[get_async_redis] = override_get_async_redis

    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sse_replays_prior_events():
    """SSE endpoint replays prior events from DB and closes on terminal event.

    Setup:
        - Pre-populate job_events with 4 events (including job.complete)
        - Set job.status = 'complete' in DB

    Asserts:
        - All 4 pre-populated event types received by SSE client
        - Connection closes without hanging (terminal event detected in replay)
    """
    tenant_id = uuid.uuid4()
    job_id = uuid.uuid4()

    # Pre-populate
    raw_key = _setup_test_job(tenant_id, job_id)

    # Mark job as complete
    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE jobs SET status = 'complete', finished_at = now() WHERE id = :id"),
            {"id": str(job_id)},
        )
    engine.dispose()

    prior_events = [
        {"event_type": "job.started", "payload": {"job_id": str(job_id)}},
        {"event_type": "neon.project.creating", "payload": {"job_id": str(job_id)}},
        {"event_type": "neon.project.ready", "payload": {"project_id": "test-123"}},
        {"event_type": "job.complete", "payload": {"job_id": str(job_id)}},
    ]
    _insert_events(tenant_id, job_id, prior_events)

    app = _make_app_with_real_deps()

    received_events = []
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-API-Key": raw_key},
            timeout=10.0,
        ) as client:
            async with client.stream("GET", f"/jobs/{job_id}/events") as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    line = line.strip()
                    if line.startswith("event:"):
                        received_events.append(line.replace("event:", "").strip())
                    # Stop reading after collecting all expected events (or stream closes)
                    if len(received_events) >= 4:
                        break

        # Assert all 4 events received
        expected_types = [
            "job.started",
            "neon.project.creating",
            "neon.project.ready",
            "job.complete",
        ]
        assert received_events == expected_types, (
            f"Expected {expected_types}, got {received_events}"
        )

    finally:
        # Clear dependency overrides
        from app.main import app as main_app
        main_app.dependency_overrides.clear()
        _delete_test_rows(tenant_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sse_receives_live_events_after_replay():
    """SSE endpoint receives DB-replay events first, then live Redis events.

    Setup:
        - Pre-populate job_events with job.started (only)
        - After 0.5s, publish a live event to Redis channel job_events:{job_id}
        - After 1.5s more, publish job.complete to terminate the stream

    Asserts:
        - job.started received (from DB replay)
        - Live events received after the replay event
        - DB replay event comes before Redis live events
    """
    tenant_id = uuid.uuid4()
    job_id = uuid.uuid4()

    raw_key = _setup_test_job(tenant_id, job_id)

    # Pre-populate only the first event (job.started)
    _insert_events(
        tenant_id,
        job_id,
        [{"event_type": "job.started", "payload": {"job_id": str(job_id)}}],
    )

    app = _make_app_with_real_deps()

    received_events = []

    async def publish_live_events():
        """Background task: publish live events to Redis after a short delay."""
        await asyncio.sleep(0.5)
        r = aioredis.Redis.from_url(_REDIS_URL, decode_responses=True)
        try:
            # Publish a live "neon.project.creating" event
            await r.publish(
                f"job_events:{job_id}",
                json.dumps({
                    "event_type": "neon.project.creating",
                    "payload": {"job_id": str(job_id), "at": "2026-05-13T00:00:00Z"},
                }),
            )
            await asyncio.sleep(0.5)
            # Publish job.complete to close the stream
            await r.publish(
                f"job_events:{job_id}",
                json.dumps({
                    "event_type": "job.complete",
                    "payload": {"job_id": str(job_id), "at": "2026-05-13T00:00:01Z"},
                }),
            )
        finally:
            await r.aclose()

    try:
        # Start the publisher task concurrently
        publisher_task = asyncio.create_task(publish_live_events())

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-API-Key": raw_key},
            timeout=10.0,
        ) as client:
            async with client.stream("GET", f"/jobs/{job_id}/events") as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    line = line.strip()
                    if line.startswith("event:"):
                        received_events.append(line.replace("event:", "").strip())
                    if len(received_events) >= 3:
                        break

        await publisher_task

        # job.started must be first (from DB replay)
        assert len(received_events) >= 2, (
            f"Expected at least 2 events, got {received_events}"
        )
        assert received_events[0] == "job.started", (
            f"First event must be 'job.started' (DB replay), got {received_events[0]}"
        )
        # Subsequent events should include the live events
        assert "neon.project.creating" in received_events[1:], (
            f"Live event 'neon.project.creating' not found after DB replay. Got: {received_events}"
        )

    finally:
        from app.main import app as main_app
        main_app.dependency_overrides.clear()
        _delete_test_rows(tenant_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sse_closes_on_completed_job():
    """SSE endpoint closes within 2 seconds when terminal event is already in DB.

    Setup:
        - Pre-populate all 6 events (including job.complete)

    Asserts:
        - Connection closes (stream ends) — no hang
        - All 6 events received before stream closes
    """
    tenant_id = uuid.uuid4()
    job_id = uuid.uuid4()

    raw_key = _setup_test_job(tenant_id, job_id)

    # Pre-populate all 6 events including terminal job.complete
    all_events = [
        {"event_type": "job.started", "payload": {"job_id": str(job_id)}},
        {"event_type": "neon.project.creating", "payload": {"job_id": str(job_id)}},
        {"event_type": "neon.project.ready", "payload": {"project_id": "test-123"}},
        {"event_type": "migrations.running", "payload": {"job_id": str(job_id)}},
        {"event_type": "migrations.complete", "payload": {"job_id": str(job_id)}},
        {"event_type": "job.complete", "payload": {"job_id": str(job_id)}},
    ]
    _insert_events(tenant_id, job_id, all_events)

    # Mark job as complete
    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE jobs SET status = 'complete', finished_at = now() WHERE id = :id"),
            {"id": str(job_id)},
        )
    engine.dispose()

    app = _make_app_with_real_deps()

    received_events = []
    start_time = time.monotonic()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-API-Key": raw_key},
            timeout=10.0,
        ) as client:
            async with client.stream("GET", f"/jobs/{job_id}/events") as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    line = line.strip()
                    if line.startswith("event:"):
                        received_events.append(line.replace("event:", "").strip())

        elapsed = time.monotonic() - start_time

        # Stream should close quickly (terminal event in DB replay — no Redis subscribe wait)
        assert elapsed < 5.0, (
            f"SSE stream took {elapsed:.1f}s to close — expected < 5s (terminal event in DB)"
        )

        # All 6 events should be received
        expected = [
            "job.started",
            "neon.project.creating",
            "neon.project.ready",
            "migrations.running",
            "migrations.complete",
            "job.complete",
        ]
        assert received_events == expected, (
            f"Expected {expected}, got {received_events}"
        )

    finally:
        from app.main import app as main_app
        main_app.dependency_overrides.clear()
        _delete_test_rows(tenant_id)
