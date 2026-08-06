"""
Integration test: POST /agents/{agent_id}/chat → run_agent_turn (eager) → SSE event emission.

Guards:
    - INTEGRATION_TESTS_ENABLED=1 required — skipped by default in CI.

Infrastructure required when running:
    - Real local Postgres (CONTROL_DB_SYNC_URL)
    - Real local Redis (REDIS_URL)
    - CELERY_TASK_ALWAYS_EAGER=True (set in conftest.py by default)

SDK mock strategy:
    - `app.worker.tasks.runtime.agent.asyncio.run` is patched to return a canned dict.
    - This avoids real Claude API calls and real claude-agent-sdk subprocess spawning.
    - _create_conversation_row and _persist_messages are patched to avoid real tenant DB calls.

Canned dict contract — must match run_agent_turn's expected result shape from _run_sdk_turn:
    {
        "response_text": str,
        "tool_calls_log": list[dict],
        "escalated": bool,
        "escalation_reason": str | None,
        "escalation_context": str | None,
        "sdk_session_id": str | None,
    }
"""

import os
import uuid
from unittest.mock import patch

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("INTEGRATION_TESTS_ENABLED"),
        reason="Set INTEGRATION_TESTS_ENABLED=1 to run integration tests against local Postgres + Redis",
    ),
]

# ---------------------------------------------------------------------------
# Canned SDK result — matches run_agent_turn's contract from Plan 04-03
# ---------------------------------------------------------------------------
CANNED_SDK_RESULT = {
    "response_text": (
        "Our business hours are Monday-Friday, 8am-6pm.\n\n"
        "CITATIONS:\n- Document: FAQ.pdf | Section: Business Hours\n"
    ),
    "tool_calls_log": [],
    "escalated": False,
    "escalation_reason": None,
    "escalation_context": None,
    "sdk_session_id": "sdk-test-session-123",
}


# ---------------------------------------------------------------------------
# Session-scoped fixture — seeds a real Tenant + Agent row in the control DB
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def seed_tenant_agent():
    """Insert test Tenant + Agent rows in the real control DB; clean up after session."""
    import hashlib

    import psycopg2
    from cryptography.fernet import Fernet

    sync_url = os.environ.get(
        "CONTROL_DB_SYNC_URL",
        "postgresql://test:test@localhost:5432/test_wchats",
    )

    # Strip asyncpg driver prefix if present
    pg_url = sync_url.replace("postgresql+asyncpg://", "postgresql://")

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    api_key_plaintext = f"vrd_inttest_{uuid.uuid4().hex[:16]}"
    # Argon2-hash the key (same as the real app). Fall back to a simple sha256 if
    # argon2-cffi is not installed — tests will still skip in CI.
    try:
        from argon2 import PasswordHasher
        ph = PasswordHasher()
        api_key_hash = ph.hash(api_key_plaintext)
    except ImportError:
        api_key_hash = hashlib.sha256(api_key_plaintext.encode()).hexdigest()

    # Generate a dummy Fernet-encrypted connection string (never actually decrypted
    # because asyncio.run is mocked — just needs to be non-null bytes).
    fernet_key = Fernet.generate_key()
    f = Fernet(fernet_key)
    dummy_conn_str = f.encrypt(b"postgresql://dummy:dummy@localhost:5432/dummy_tenant")

    conn = psycopg2.connect(pg_url, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            # Insert tenant
            cur.execute(
                """
                INSERT INTO tenants (id, name, api_key, deleted_at)
                VALUES (%s, %s, %s, NULL)
                ON CONFLICT (id) DO NOTHING
                """,
                (tenant_id, "Integration Test Tenant", api_key_hash),
            )
            # Insert agent with status='ready'
            cur.execute(
                """
                INSERT INTO agents (id, tenant_id, name, status, neon_connection_string, deleted_at)
                VALUES (%s, %s, %s, 'ready', %s, NULL)
                ON CONFLICT (id) DO NOTHING
                """,
                (agent_id, tenant_id, "Integration Test Agent", dummy_conn_str),
            )
        conn.commit()
    finally:
        conn.close()

    yield tenant_id, agent_id, api_key_plaintext, api_key_hash

    # Teardown — delete in FK-safe order
    conn = psycopg2.connect(pg_url, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agents WHERE id = %s", (agent_id,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 1: POST /agents/{id}/chat → eager Celery → SSE events in job_events table
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_post_agent_chat_emits_thinking_then_response_via_eager_task(seed_tenant_agent):
    """
    Full chain test:
      POST /agents/{agent_id}/chat with X-API-Key
        → 202 Accepted with job_id, events_url, conversation_id
        → run_agent_turn runs eagerly (CELERY_TASK_ALWAYS_EAGER)
        → job_events table gains agent.thinking then agent.response rows
    """
    import psycopg2
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    tenant_id, agent_id, api_key_plaintext, _ = seed_tenant_agent

    sync_url = os.environ.get(
        "CONTROL_DB_SYNC_URL",
        "postgresql://test:test@localhost:5432/test_wchats",
    ).replace("postgresql+asyncpg://", "postgresql://")

    sentinel_conversation_id = str(uuid.uuid4())
    job_id_seen = None

    with (
        patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=CANNED_SDK_RESULT) as mock_run,
        patch(
            "app.worker.tasks.runtime.agent._create_conversation_row",
            return_value=sentinel_conversation_id,
        ),
        patch("app.worker.tasks.runtime.agent._persist_messages"),
        patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                f"/agents/{agent_id}/chat",
                json={"message": "What are your hours?", "conversation_id": None},
                headers={"X-API-Key": api_key_plaintext},
            )

    assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"
    body = response.json()
    assert "job_id" in body, f"job_id missing from response: {body}"
    assert "events_url" in body, f"events_url missing from response: {body}"
    job_id_seen = body["job_id"]

    # Verify job_events table in control DB
    conn = psycopg2.connect(sync_url, connect_timeout=10)
    rows = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_type, payload
                FROM job_events
                WHERE job_id = %s
                ORDER BY created_at ASC
                """,
                (job_id_seen,),
            )
            rows = cur.fetchall()
    finally:
        # Cleanup job_events + jobs rows
        with conn.cursor() as cur:
            cur.execute("DELETE FROM job_events WHERE job_id = %s", (job_id_seen,))
            cur.execute("DELETE FROM jobs WHERE id = %s", (job_id_seen,))
        conn.commit()
        conn.close()

    event_types = [r[0] for r in rows]
    assert "agent.thinking" in event_types, f"agent.thinking missing; got: {event_types}"
    assert "agent.response" in event_types, f"agent.response missing; got: {event_types}"

    # Order check: agent.thinking must come before agent.response
    thinking_idx = event_types.index("agent.thinking")
    response_idx = event_types.index("agent.response")
    assert thinking_idx < response_idx, (
        f"agent.thinking must precede agent.response; order: {event_types}"
    )

    # Payload check on agent.response
    response_row = rows[response_idx]
    response_payload = response_row[1]
    assert "text" in response_payload, f"agent.response payload missing 'text': {response_payload}"
    assert "conversation_id" in response_payload, (
        f"agent.response payload missing 'conversation_id': {response_payload}"
    )


# ---------------------------------------------------------------------------
# Test 2: Idempotency — pre-existing agent.response skips re-execution
# ---------------------------------------------------------------------------

def test_post_agent_chat_idempotent_on_retry():
    """
    Idempotency guard test:
      Pre-insert a job_events row with event_type='agent.response' for a fresh job_id.
      Trigger run_agent_turn.apply (eager).
      Assert no new job_events rows are added — idempotency guard fires.
    """
    import psycopg2

    from app.worker.tasks.runtime.agent import run_agent_turn

    sync_url = os.environ.get(
        "CONTROL_DB_SYNC_URL",
        "postgresql://test:test@localhost:5432/test_wchats",
    ).replace("postgresql+asyncpg://", "postgresql://")

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())  # Doesn't need to exist — idempotency fires first

    conn = psycopg2.connect(sync_url, connect_timeout=10)
    rows_before = 0
    rows_after = 0
    try:
        # Pre-insert agent.response event
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO job_events (id, job_id, event_type, payload, created_at)
                VALUES (gen_random_uuid(), %s, 'agent.response', '{"pre_existing": true}'::jsonb, NOW())
                """,
                (job_id,),
            )
        conn.commit()

        # Count rows before
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM job_events WHERE job_id = %s",
                (job_id,),
            )
            rows_before = cur.fetchone()[0]

        # Trigger eager task — idempotency guard should short-circuit
        result = run_agent_turn.apply(args=[job_id, agent_id, "test message", None])

        # Count rows after — should be unchanged
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM job_events WHERE job_id = %s",
                (job_id,),
            )
            rows_after = cur.fetchone()[0]

    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM job_events WHERE job_id = %s", (job_id,))
        conn.commit()
        conn.close()

    assert rows_after == rows_before, (
        f"Idempotency guard failed: rows grew from {rows_before} to {rows_after}"
    )
