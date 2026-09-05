"""
Integration test: POST /agents/{agent_id}/chat -> run_agent_turn -> SSE event emission.

Guards:
    - INTEGRATION_TESTS_ENABLED=1 required — skipped by default in CI.

Infrastructure required when running:
    - Real local Postgres (the control DB; CONTROL_DB_SYNC_URL, set by
      tests/integration/conftest.py from INTEGRATION_DB_URL)
    - Real local Redis (REDIS_URL) — events.emit publishes before it commits.

WHY THE BROKER HOP IS REPLAYED IN-PROCESS RATHER THAN RUN "EAGERLY".
    This module used to claim CELERY_TASK_ALWAYS_EAGER=True made the dispatch
    run inline. It does not, and never did: nothing under app/ reads that
    variable (the only occurrence is a comment at app/worker/celery_app.py:272)
    and celery_app.conf never sets task_always_eager. On top of that,
    tests/integration/conftest.py:50 sets it to "False". So
    agent_chat.py:160 `run_agent_turn.apply_async(queue="runtime")` always
    published to Redis, no runtime worker was listening, zero job_events rows
    were ever written — and a live `run_agent_turn` message was left parked on
    the runtime queue for whichever worker started next.

    So the dispatch is captured at the route boundary and replayed here with
    `.apply()`, which executes locally by definition. Everything either side of
    that one hop is real: the HTTP route runs against real Postgres, and the
    real task body runs against the same real Postgres and real Redis.

Model and side-effect mock strategy. Every one of these is a boundary that would
otherwise leave this process:
    - `agent.asyncio.run`        -> canned dict. This is THE seam that keeps any
                                    model call out of the test. Patched only
                                    around `.apply()`, never around the httpx
                                    client: the target resolves to the global
                                    asyncio module, so a wider window would break
                                    `asyncio.run` for anything else running in it.
    - `agent.build_agent_turn`   -> the turn assembly. The real seam builds a
                                    provider client from live credentials and a
                                    tool server bound to the tenant connection
                                    string; both are boundaries this test has no
                                    business crossing.
    - `_create_conversation_row`,
      `_persist_messages`,
      `_write_turn_metrics`      -> tenant-DB writes. There is no tenant DB on
                                    this machine, and turn_metrics has no
                                    control-DB migration at all.
    - `celery_chain`             -> the validator chain (Gatekeeper, Auditor,
                                    Strategist, retrieval faithfulness). Those
                                    are Claude judges. Unpatched they either
                                    reach the model or sit on the runtime queue
                                    waiting to.
    - `_emit_langfuse_turn_trace`-> the root conftest clears the Langfuse keys, so
                                    the module-level client is already None. The
                                    patch keeps the test independent of a shell
                                    that sets them back.

    `fernet_decrypt` and `psycopg2.connect` are deliberately NOT mocked. The
    agent row carries a real Fernet ciphertext (encrypted with the app's own
    key) of a real, reachable URL, so the CTL-08 decrypt-at-runtime path
    executes for real.

Canned dict contract. It must match run_agent_loop's return shape.
"""

import json
import os
import uuid
from types import SimpleNamespace
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
# DB URLs — mirrors tests/integration/conftest.py, which has already written
# CONTROL_DB_SYNC_URL into the environment before any app module was imported.
# Reading it back (rather than re-deriving) is what guarantees the route, the
# task and the assertions below all address one database.
# ---------------------------------------------------------------------------
_SYNC_DB_URL = (
    os.environ.get("CONTROL_DB_SYNC_URL")
    or os.environ.get("INTEGRATION_DB_URL")
    or "postgresql://wchats:wchats@localhost:5432/wchats_control"
).replace("postgresql+asyncpg://", "postgresql://")

_ASYNC_DB_URL = _SYNC_DB_URL.replace("postgresql://", "postgresql+asyncpg://")


# ---------------------------------------------------------------------------
# The turn's two doubles. One is the seam that would build a provider client, and
# the other is the canned loop result standing in for the model call itself.
# ---------------------------------------------------------------------------


def _seam(**_kwargs):
    """Stand-in for build_agent_turn. `calls` and `bound` are what the task reads."""
    return SimpleNamespace(calls=[], bound=())


CANNED_TURN_RESULT = {
    "response_text": (
        "Our business hours are Monday-Friday, 8am-6pm.\n\n"
        "CITATIONS:\n- Document: FAQ.pdf | Section: Business Hours\n"
    ),
    "tool_calls_log": [],
    "escalated": False,
    "escalation_reason": None,
    "escalation_context": None,
    "num_turns": 1,
    "stop_reason": "stop",
}

CHAT_MESSAGE = "What are your hours?"


# ---------------------------------------------------------------------------
# Session-scoped fixture — seeds a real Tenant + Agent row in the control DB
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def seed_tenant_agent():
    """Insert test Tenant + Agent rows in the real control DB; clean up after session.

    Three things here are load-bearing and were each wrong before:

    1.  `agents.soul` (JSONB) and `agents.role` (TEXT) are both NOT NULL with no
        server default (alembic/versions/0001_control_db_initial.py;
        app/models/agent.py:33-35). Omitting them made this INSERT a guaranteed
        NotNullViolation, so the fixture could never produce an agent.

    2.  The connection string is encrypted with the application's own key via
        `fernet_encrypt`, not with a freshly generated one. run_agent_turn
        decrypts it at agent.py:1115 — outside its try/except — so a foreign key
        is an InvalidToken that fails the task before it can emit anything. The
        old comment claiming it is "never actually decrypted because asyncio.run
        is mocked" was wrong about where the decrypt happens.

    3.  The plaintext points at the control DB, which is reachable. agent.py:1124
        opens it with psycopg2 unconditionally, also outside the try. Every
        helper that would touch a *tenant* table through that handle is patched,
        so the connection is opened, held and closed and nothing else — but it
        has to be openable.

    `api_key_prefix` is set so authentication takes the O(1) indexed path
    (deps.py:169-179) instead of the legacy fallback, which argon2-verifies
    every prefix-less tenant row in a shared dev database one at a time.
    """
    import psycopg2

    from app.core.security import (
        fernet_encrypt,
        generate_api_key,
        hash_api_key,
        hmac_key_prefix,
    )

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    api_key_plaintext = generate_api_key()
    api_key_hash = hash_api_key(api_key_plaintext)
    api_key_prefix = hmac_key_prefix(api_key_plaintext)

    encrypted_conn_str = fernet_encrypt(_SYNC_DB_URL)

    conn = psycopg2.connect(_SYNC_DB_URL, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tenants (id, name, api_key_hash, api_key_prefix, deleted_at)
                VALUES (%s, %s, %s, %s, NULL)
                """,
                (tenant_id, "Integration Test Tenant", api_key_hash, api_key_prefix),
            )
            cur.execute(
                """
                INSERT INTO agents
                    (id, tenant_id, name, soul, role, status,
                     neon_connection_string, deleted_at)
                VALUES
                    (%s, %s, %s, CAST(%s AS jsonb), %s, 'ready', %s, NULL)
                """,
                (
                    agent_id,
                    tenant_id,
                    "Integration Test Agent",
                    json.dumps({"tone": "professional", "language": "en"}),
                    "support",
                    psycopg2.Binary(encrypted_conn_str),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    yield tenant_id, agent_id, api_key_plaintext, api_key_hash

    # Teardown — FK-safe order, and it sweeps any job rows a test left behind.
    # jobs.agent_id references agents(id) and job_events.job_id references
    # jobs(id), so a single leaked job row would make the agent undeletable and
    # leak the tenant with it.
    conn = psycopg2.connect(_SYNC_DB_URL, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM job_events WHERE job_id IN "
                "(SELECT id FROM jobs WHERE tenant_id = %s)",
                (tenant_id,),
            )
            cur.execute("DELETE FROM jobs WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM agents WHERE id = %s", (agent_id,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
        conn.commit()
    finally:
        conn.close()


def _describe(rows: list) -> str:
    """Render the job_events rows for an assertion message.

    A turn that dies inside run_agent_turn's own try/except emits `agent.failed`
    carrying the real cause, and the task then returns normally — so without
    this the only signal is "agent.response missing", which names the symptom
    and hides the defect.
    """
    parts = []
    for event_type, payload in rows:
        if event_type == "agent.failed":
            parts.append(f"{event_type}(error={(payload or {}).get('error')!r})")
        else:
            parts.append(event_type)
    return "[" + ", ".join(parts) + "]"


# ---------------------------------------------------------------------------
# Test 1: POST /agents/{id}/chat -> dispatched task -> job_events rows
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_agent_chat_emits_thinking_then_response(seed_tenant_agent):
    """
    Full chain test:
      POST /agents/{agent_id}/chat with X-API-Key
        -> 202 Accepted with job_id, events_url
        -> the dispatch carries exactly (job_id, agent_id, message, None) to the
           runtime queue
        -> replaying that dispatch runs run_agent_turn for real
        -> job_events gains agent.thinking then agent.response
    """
    import psycopg2
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.database import get_async_db
    from app.main import app
    from app.worker.tasks.runtime.agent import run_agent_turn

    _tenant_id, agent_id, api_key_plaintext, _ = seed_tenant_agent

    sentinel_conversation_id = str(uuid.uuid4())
    sentinel_message_id = str(uuid.uuid4())

    # The route's session is pinned to the same database this test reads with
    # psycopg2. Without this the route depends on whichever conftest imported
    # app.core.database first having won the CONTROL_DB_URL race — the exact
    # failure shape where a fixture seeds one database and the code reads
    # another.
    async_engine = create_async_engine(_ASYNC_DB_URL, pool_pre_ping=True)
    async_session_factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def _override_get_async_db():
        async with async_session_factory() as session:
            yield session

    app.dependency_overrides[get_async_db] = _override_get_async_db

    try:
        # ------------------------------------------------------------------
        # 1. POST — the task object is replaced so the dispatch is captured
        #    instead of being published to a broker nothing is consuming.
        # ------------------------------------------------------------------
        with patch("app.api.v1.agent_chat.run_agent_turn") as dispatched:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    # /api/v1 — main.py:175 mounts agent_chat.router under that
                    # prefix. Unprefixed this is a 404.
                    f"/api/v1/agents/{agent_id}/chat",
                    json={"message": CHAT_MESSAGE, "conversation_id": None},
                    headers={"X-API-Key": api_key_plaintext},
                )

        assert response.status_code == 202, (
            f"Expected 202, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert "job_id" in body, f"job_id missing from response: {body}"
        assert "events_url" in body, f"events_url missing from response: {body}"
        job_id_seen = body["job_id"]

        # ------------------------------------------------------------------
        # 2. The dispatch contract. Asserted positionally against
        #    run_agent_turn(self, job_id, agent_id, message, conversation_id):
        #    job_id and agent_id are both UUID strings, so a membership check
        #    passes even when the two are transposed and the worker then looks
        #    up the wrong row.
        # ------------------------------------------------------------------
        dispatched.apply_async.assert_called_once()
        call = dispatched.apply_async.call_args
        task_args = list(call.kwargs["args"])
        assert task_args == [job_id_seen, str(agent_id), CHAT_MESSAGE, None], (
            f"dispatch args do not match the task signature: {task_args!r}"
        )
        assert call.kwargs.get("queue") == "runtime", (
            f"chat dispatch must target the runtime queue, got {call.kwargs.get('queue')!r}"
        )
        for i, arg in enumerate(task_args):
            assert "postgresql://" not in str(arg), (
                f"task arg {i} carries a connection string (CLAUDE.md rule 1): {arg!r}"
            )

        # ------------------------------------------------------------------
        # 3. Replay the dispatch in-process. `.apply()` executes locally by
        #    definition, so this needs no worker and no broker.
        # ------------------------------------------------------------------
        with (
            patch(
                "app.worker.tasks.runtime.agent.asyncio.run",
                return_value=CANNED_TURN_RESULT,
            ) as turn_seam,
            patch(
                "app.worker.tasks.runtime.agent._create_conversation_row",
                return_value=sentinel_conversation_id,
            ),
            patch(
                "app.worker.tasks.runtime.agent._persist_messages",
                # A bare MagicMock here is not a detail: agent.py:1368 puts this
                # return value on the terminal event as `message_id`, and
                # events.emit json.dumps() the payload (events.py:79). An
                # unserialisable id means agent.response is never written —
                # precisely the row this test exists to assert on.
                return_value=sentinel_message_id,
            ),
            patch("app.worker.tasks.runtime.agent.build_agent_turn", side_effect=_seam),
            patch("app.worker.tasks.runtime.agent._write_turn_metrics"),
            patch("app.worker.tasks.runtime.agent._emit_langfuse_turn_trace"),
            patch("app.worker.tasks.runtime.agent.celery_chain") as validator_chain,
        ):
            eager = run_agent_turn.apply(args=task_args, throw=True)

        assert eager.successful(), f"run_agent_turn raised: {eager.traceback}"
        turn_seam.assert_called_once()
        validator_chain.assert_called_once()

        # ------------------------------------------------------------------
        # 4. Verify job_events in the control DB
        # ------------------------------------------------------------------
        conn = psycopg2.connect(_SYNC_DB_URL, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT event_type, payload
                    FROM job_events
                    WHERE job_id = %s
                    ORDER BY id ASC
                    """,
                    (job_id_seen,),
                )
                rows = cur.fetchall()
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM job_events WHERE job_id = %s", (job_id_seen,))
                cur.execute("DELETE FROM jobs WHERE id = %s", (job_id_seen,))
            conn.commit()
            conn.close()

        event_types = [r[0] for r in rows]
        assert "agent.thinking" in event_types, f"agent.thinking missing; got: {_describe(rows)}"
        assert "agent.response" in event_types, f"agent.response missing; got: {_describe(rows)}"

        # Order check: agent.thinking must come before agent.response
        thinking_idx = event_types.index("agent.thinking")
        response_idx = event_types.index("agent.response")
        assert thinking_idx < response_idx, (
            f"agent.thinking must precede agent.response; order: {_describe(rows)}"
        )

        # Payload check on agent.response
        response_payload = rows[response_idx][1]
        assert "text" in response_payload, f"agent.response payload missing 'text': {response_payload}"
        assert "conversation_id" in response_payload, (
            f"agent.response payload missing 'conversation_id': {response_payload}"
        )
        assert response_payload["conversation_id"] == sentinel_conversation_id, (
            "agent.response must carry the conversation the turn actually used, got "
            f"{response_payload['conversation_id']!r}"
        )
        assert response_payload.get("message_id") == sentinel_message_id, (
            "agent.response must carry the persisted assistant message id (WIRE-05), got "
            f"{response_payload.get('message_id')!r}"
        )
    finally:
        app.dependency_overrides.clear()
        await async_engine.dispose()


# ---------------------------------------------------------------------------
# Test 2: Idempotency — a pre-existing agent.response skips re-execution
# ---------------------------------------------------------------------------

def test_post_agent_chat_idempotent_on_retry(seed_tenant_agent):
    """
    Idempotency guard test (agent.py:1079-1088):
      Pre-insert an agent.response row for a fresh job_id, then run the task.
      Assert it short-circuits — no new job_events rows, and the *sentinel
      return value* that only the guard produces.

    The return-value assertion is the point. Counting rows alone cannot fail:
    the agent_id below is random, so if the guard did NOT fire the task would
    fall through to `db.get(Agent, ...) is None` (agent.py:1094-1101), log
    agent_not_found and return {} — adding zero rows and passing a
    rows_after == rows_before assertion. That test would have been a tautology.
    `already_complete` is returned on exactly one path.
    """
    import psycopg2

    from app.worker.tasks.runtime.agent import run_agent_turn

    tenant_id, _seeded_agent_id, _, _ = seed_tenant_agent

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())  # Doesn't need to exist — idempotency fires first

    conn = psycopg2.connect(_SYNC_DB_URL, connect_timeout=10)
    rows_before = 0
    rows_after = 0
    try:
        with conn.cursor() as cur:
            # job_events.job_id is `UUID NOT NULL REFERENCES jobs(id)`
            # (0001_control_db_initial.py:86), and jobs.tenant_id references
            # tenants(id) — so the event row cannot exist on its own. jobs.id is
            # the only anchor the task reads; jobs.agent_id stays NULL to make it
            # obvious this row exists purely to satisfy the constraint.
            cur.execute(
                """
                INSERT INTO jobs (id, tenant_id, agent_id, kind, status)
                VALUES (%s, %s, NULL, 'agent_turn', 'pending')
                """,
                (job_id, tenant_id),
            )
            # `id` is deliberately omitted: job_events.id is BIGSERIAL, so the
            # gen_random_uuid() this used to supply was a uuid into a bigint
            # column — the INSERT could not parse, let alone run.
            cur.execute(
                """
                INSERT INTO job_events (job_id, event_type, payload, created_at)
                VALUES (%s, 'agent.response', '{"pre_existing": true}'::jsonb, NOW())
                """,
                (job_id,),
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM job_events WHERE job_id = %s",
                (job_id,),
            )
            rows_before = cur.fetchone()[0]

        # Tripwires, not mocks. Neither should be reachable behind the guard; if
        # the guard ever stops firing, this fails loudly here rather than
        # attempting a model turn or queueing the judge chain.
        with (
            patch(
                "app.worker.tasks.runtime.agent.asyncio.run",
                side_effect=AssertionError(
                    "idempotency guard did not fire, so the turn was attempted"
                ),
            ) as turn_seam,
            patch(
                "app.worker.tasks.runtime.agent.celery_chain",
                side_effect=AssertionError(
                    "idempotency guard did not fire — the validator chain was dispatched"
                ),
            ) as validator_chain,
        ):
            eager = run_agent_turn.apply(
                args=[job_id, agent_id, "test message", None], throw=True
            )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM job_events WHERE job_id = %s",
                (job_id,),
            )
            rows_after = cur.fetchone()[0]

    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM job_events WHERE job_id = %s", (job_id,))
            cur.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
        conn.commit()
        conn.close()

    assert eager.successful(), f"run_agent_turn raised: {eager.traceback}"
    assert eager.result == {"status": "already_complete", "job_id": job_id}, (
        "the idempotent short-circuit is the only path returning already_complete; "
        f"got {eager.result!r}"
    )
    turn_seam.assert_not_called()
    validator_chain.assert_not_called()
    assert rows_after == rows_before, (
        f"Idempotency guard failed: rows grew from {rows_before} to {rows_after}"
    )
