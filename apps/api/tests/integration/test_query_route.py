"""
Integration tests: POST /agents/{id}/query and GET /agents/{id}/queries.

These tests use:
- A REAL local Postgres DB (not mocked) — via the test_tenant fixture.
- ASGITransport + AsyncClient (FastAPI ASGI) for HTTP testing.
- dependency_overrides to inject real async DB + Redis sessions.
- unittest.mock.patch to prevent real Voyage/retrieval calls in retrieve_and_rank.
- CELERY_TASK_ALWAYS_EAGER is explicitly set to "False" by integration conftest.py,
  so apply_async dispatches to the broker — we mock retrieve_and_rank.apply_async
  to avoid needing a running runtime worker for HTTP-layer tests.

Tests:
    test_post_query_returns_202
        — POST with a ready agent returns 202, job_id, events_url, status=pending.

    test_post_query_agent_not_found
        — POST with a non-existent agent_id returns 404.

    test_get_queries_returns_list
        — POST a query first, then GET /queries returns a list with kind=query_agent.
"""

import json
import os
import uuid
from unittest.mock import patch

import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# DB URL — mirrors integration conftest.py
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
# Helpers: set up and tear down a ready-status agent
# ---------------------------------------------------------------------------


def _create_ready_agent(tenant_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    """Insert a tenant + agent with status='ready' and a dummy encrypted conn str.

    Returns (agent_id, raw_api_key).

    Uses a dummy Fernet-encrypted bytes value for neon_connection_string so the
    agent row passes the DB constraint without a real Neon project.  The
    retrieve_and_rank task is mocked so fernet_decrypt is never called.
    """
    from app.core.security import fernet_encrypt, generate_api_key, hash_api_key

    raw_key = generate_api_key()
    api_key_hash = hash_api_key(raw_key)
    agent_id = uuid.uuid4()

    # Encrypt a placeholder connection string to satisfy the bytea column
    dummy_conn = fernet_encrypt("postgresql://dummy:dummy@localhost/dummy")

    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tenants (id, name, api_key_hash, created_at) "
                    "VALUES (:id, :name, :api_key_hash, now())"
                ),
                {
                    "id": str(tenant_id),
                    "name": f"qtest-tenant-{tenant_id}",
                    "api_key_hash": api_key_hash,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO agents
                        (id, tenant_id, name, soul, role, status,
                         neon_connection_string, created_at)
                    VALUES
                        (:id, :tenant_id, :name, CAST(:soul AS jsonb), :role, 'ready',
                         :conn_str, now())
                    """
                ),
                {
                    "id": str(agent_id),
                    "tenant_id": str(tenant_id),
                    "name": f"qtest-agent-{agent_id}",
                    "soul": json.dumps({"tone": "professional", "language": "en"}),
                    "role": "support",
                    "conn_str": dummy_conn,
                },
            )
    finally:
        engine.dispose()

    return agent_id, raw_key


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


def _celery_task_args(mock_apply_async) -> list:
    """Return the positional task-arg list a mocked ``apply_async`` was given.

    Celery accepts the task arg list two ways::

        task.apply_async(args=[...], queue="runtime")   # keyword  -> call_args.kwargs
        task.apply_async([...], queue="runtime")        # positional -> call_args.args[0]

    ``app.api.v1.query`` uses the keyword form, so ``call_args.args`` is ``()``.
    An earlier version of this helper was inlined as::

        task_args = call_kwargs[1].get("args") or call_kwargs[0][0] if call_kwargs[0] else []

    which Python parses as ``(... or ...) if call_kwargs[0] else []`` — the
    conditional binds looser than ``or``.  With no positional args that is
    ``(...) if () else []``, so it silently evaluated to ``[]`` and every
    membership assertion below it failed regardless of what was dispatched.

    This raises on an unreadable call instead of degrading to an empty list, so
    the failure names the real cause rather than reporting a missing agent_id.
    """
    mock_apply_async.assert_called_once()
    call = mock_apply_async.call_args

    if "args" in call.kwargs:
        task_args = call.kwargs["args"]
    elif call.args:
        task_args = call.args[0]
    else:
        raise AssertionError(
            "apply_async was called with no task args in either position: "
            f"args={call.args!r} kwargs={call.kwargs!r}"
        )

    assert isinstance(task_args, (list, tuple)), (
        f"Celery task args should be a list/tuple, got {type(task_args)}: {task_args!r}"
    )
    return list(task_args)


# ---------------------------------------------------------------------------
# App factory with real local deps (mirrors test_sse.py pattern)
# ---------------------------------------------------------------------------


def _make_app_with_real_deps():
    """Return FastAPI app with DB/Redis overridden to use real local services."""
    from app.api.deps import get_async_redis
    from app.core.database import get_async_db
    from app.main import app

    test_async_engine = create_async_engine(
        _INTEGRATION_DB_ASYNC_URL,
        pool_pre_ping=True,
    )
    test_session_factory = async_sessionmaker(
        test_async_engine,
        expire_on_commit=False,
    )

    async def override_get_async_db():
        async with test_session_factory() as session:
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
async def test_post_query_returns_202():
    """POST /agents/{id}/query with a ready agent returns 202, job_id, events_url.

    Verifies:
    - HTTP 202 Accepted
    - Response body contains job_id (UUID), events_url (/jobs/…/events), status=pending
    - Celery task is dispatched (apply_async is called once)

    Mocks:
    - retrieve_and_rank.apply_async — prevents real task dispatch to broker;
      no runtime worker required for HTTP-layer tests.
    """
    tenant_id = uuid.uuid4()
    agent_id, raw_key = _create_ready_agent(tenant_id)
    app = _make_app_with_real_deps()

    try:
        with patch(
            "app.worker.tasks.runtime.retrieve.retrieve_and_rank.apply_async"
        ) as mock_dispatch:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                resp = await client.post(
                    f"/api/v1/agents/{agent_id}/query",
                    headers={"X-API-Key": raw_key},
                    json={"query": "What is the refund policy?"},
                )

        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        body = resp.json()

        assert "job_id" in body, f"Missing job_id in response: {body}"
        assert "events_url" in body, f"Missing events_url in response: {body}"
        assert "status" in body, f"Missing status in response: {body}"

        assert body["status"] == "pending", f"Expected status=pending, got {body['status']}"
        assert "/jobs/" in body["events_url"], (
            f"events_url should contain /jobs/, got: {body['events_url']}"
        )
        assert body["events_url"].endswith("/events"), (
            f"events_url should end with /events, got: {body['events_url']}"
        )

        # ------------------------------------------------------------------
        # Verify the dispatch actually carries the job's identity.
        #
        # Asserted POSITIONALLY against the retrieve_and_rank signature
        # — retrieve_and_rank(self, job_id, agent_id, query) — not by
        # membership.  job_id and agent_id are both UUID strings, so an
        # `in task_args` check passes even if the two are transposed and the
        # worker looks up the wrong row.
        # ------------------------------------------------------------------
        task_args = _celery_task_args(mock_dispatch)
        assert len(task_args) == 3, (
            "retrieve_and_rank takes (job_id, agent_id, query); "
            f"dispatch carried {len(task_args)} args: {task_args}"
        )
        assert task_args[0] == body["job_id"], (
            f"task arg 0 should be the response job_id {body['job_id']}, got {task_args[0]!r}"
        )
        assert task_args[1] == str(agent_id), (
            f"task arg 1 should be agent_id {agent_id}, got {task_args[1]!r}"
        )
        assert task_args[2] == "What is the refund policy?", (
            f"task arg 2 should be the query text, got {task_args[2]!r}"
        )

        # Routed to the runtime queue (CLAUDE.md: pipeline + runtime, always both)
        assert mock_dispatch.call_args.kwargs.get("queue") == "runtime", (
            f"query dispatch must target the runtime queue, got "
            f"{mock_dispatch.call_args.kwargs.get('queue')!r}"
        )

        # Connection strings NEVER in Celery task args (CLAUDE.md rule 1) —
        # the worker fetches and decrypts from the control DB at runtime.
        for i, arg in enumerate(task_args):
            assert "postgresql://" not in str(arg), (
                f"task arg {i} carries a connection string: {arg!r}"
            )

    finally:
        from app.main import app as main_app
        main_app.dependency_overrides.clear()
        _delete_test_rows(tenant_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_query_agent_not_found():
    """POST /agents/{id}/query with non-existent agent_id returns 404.

    Verifies:
    - HTTP 404 Not Found when agent_id does not exist for the authenticated tenant.
    """
    tenant_id = uuid.uuid4()
    # Create tenant only (no agent) — any agent UUID will be unknown
    from app.core.security import generate_api_key, hash_api_key

    raw_key = generate_api_key()
    api_key_hash = hash_api_key(raw_key)
    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tenants (id, name, api_key_hash, created_at) "
                    "VALUES (:id, :name, :api_key_hash, now())"
                ),
                {
                    "id": str(tenant_id),
                    "name": f"qtest404-tenant-{tenant_id}",
                    "api_key_hash": api_key_hash,
                },
            )
    finally:
        engine.dispose()

    app = _make_app_with_real_deps()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            resp = await client.post(
                f"/api/v1/agents/{uuid.uuid4()}/query",
                headers={"X-API-Key": raw_key},
                json={"query": "test query"},
            )

        assert resp.status_code == 404, (
            f"Expected 404, got {resp.status_code}: {resp.text}"
        )
        # The path must be the mounted one (/api/v1/...) and the 404 must come
        # from the agent-ownership check, not from an unmatched route.  Without
        # the detail assertion this test passes on a routing 404 even if the
        # ownership filter were deleted from post_agent_query entirely.
        assert resp.json().get("detail") == "Agent not found", (
            "404 must originate from the agent-ownership check, not a routing "
            f"miss; got body: {resp.text}"
        )

    finally:
        from app.main import app as main_app
        main_app.dependency_overrides.clear()
        _delete_test_rows(tenant_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_queries_returns_list():
    """GET /agents/{id}/queries returns list of query_agent jobs after a POST.

    Steps:
    1. POST /agents/{id}/query (with mocked task dispatch) — creates a job row.
    2. GET /agents/{id}/queries — must return HTTP 200 with 'jobs' list.

    Verifies:
    - HTTP 200 OK on GET
    - Response body has 'jobs' key (list)
    - At least one job present (the one we just created)
    - The job entry has kind='query_agent'
    """
    tenant_id = uuid.uuid4()
    agent_id, raw_key = _create_ready_agent(tenant_id)
    app = _make_app_with_real_deps()

    try:
        # Step 1: POST to create a query job (task dispatch mocked)
        with patch(
            "app.worker.tasks.runtime.retrieve.retrieve_and_rank.apply_async"
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                post_resp = await client.post(
                    f"/api/v1/agents/{agent_id}/query",
                    headers={"X-API-Key": raw_key},
                    json={"query": "How do I get a refund?"},
                )
                assert post_resp.status_code == 202, (
                    f"POST query failed: {post_resp.status_code} {post_resp.text}"
                )

        # Step 2: GET the query list
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            get_resp = await client.get(
                f"/api/v1/agents/{agent_id}/queries",
                headers={"X-API-Key": raw_key},
            )

        assert get_resp.status_code == 200, (
            f"Expected 200, got {get_resp.status_code}: {get_resp.text}"
        )
        body = get_resp.json()
        assert "jobs" in body, f"Missing 'jobs' key in response: {body}"
        assert isinstance(body["jobs"], list), (
            f"'jobs' should be a list, got {type(body['jobs'])}"
        )
        assert len(body["jobs"]) >= 1, (
            f"Expected at least 1 query job, got {len(body['jobs'])}"
        )

        # The job we created should be present and have kind=query_agent
        job = body["jobs"][0]
        assert job["kind"] == "query_agent", (
            f"Expected kind=query_agent, got {job['kind']}"
        )
        assert "job_id" in job, f"Missing job_id in job item: {job}"
        assert "status" in job, f"Missing status in job item: {job}"

    finally:
        from app.main import app as main_app
        main_app.dependency_overrides.clear()
        _delete_test_rows(tenant_id)
