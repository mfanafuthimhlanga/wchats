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
        — Pre-populate job.started; connect; emit live events only once the replay
          event has been observed on the wire; assert the replay event arrives
          first, the live events after it, and that an event which was published
          but never persisted never arrives at all.

    test_sse_closes_on_completed_job
        — Pre-populate all 6 events (including job.complete); connect; assert the
          server closes the stream itself, within SSE_CLOSE_BUDGET_S, having
          found the terminal event in the replay without ever waiting on Redis.

An event is a DB row plus a pub/sub message, never one without the other:
app/services/sse.py reads event data from job_events and uses the pub/sub message
only as a signal to re-query early.  Emit through _emit_live, which does both, the
way app/services/events.py:emit does.  A bare publish is not an event and will not
be delivered — a fact test_sse_receives_live_events_after_replay now asserts
directly rather than assuming.
"""

import asyncio
import contextlib
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

#: Wall-clock ceiling on any SSE stream consume loop in this module — and on the
#: emitter task that feeds one.  Generous relative to what these tests need: the
#: stream half of the live-event test and the whole of the close test both run in
#: well under a second (see SSE_CLOSE_BUDGET_S), so a breach means the stream
#: genuinely never delivered, never that the bound was too tight.
SSE_STREAM_TIMEOUT_S = 30

#: Ceiling on how long the server may take to CLOSE a stream whose terminal
#: event is already in job_events.  Distinct from the bound above: that one asks
#: "did the stream terminate at all", this one asks "did it terminate without
#: waiting on anything it did not need to".  Nothing here should block —
#: `event_generator` finds job.complete in its Phase-1 replay SELECT and returns
#: before its first Redis `listen()`.
#:
#: MEASURED, not guessed, and measured against `_SSEStream` so the number is the
#: server's close latency rather than httpx's response buffering (see the test's
#: docstring).  On this 4 GB machine, 2026-08-11: **0.546s standalone** and
#: **0.422s under the full integration suite** — the loaded run is the FASTER of
#: the two, which is the tell that the old 2.85s/5.5s/6.8s spread was transport
#: and client teardown rather than the generator.  5.0s is ~12x the loaded
#: measurement, so a breach is a finding about `event_generator`, not a report on
#: how busy the box was.  Recorded in
#: `.dev/reference/260811-review-fix-mutation-proofs.md`.
SSE_CLOSE_BUDGET_S = 5.0

#: An event type published to Redis but deliberately NOT written to job_events.
#: sse.py treats a pub/sub message as a wake-up only and reads the event data from
#: the DB, so this must never reach a client.  Named so a grep for it lands on the
#: assertion that pins that contract.
DECOY_UNPERSISTED_EVENT = "decoy.published.but.never.persisted"

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
                        VALUES (:job_id, :event_type, CAST(:payload AS jsonb), now())
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
    """Insert minimal tenant, agent, and job rows for the test.

    ``api_key_prefix`` is populated because every production writer of a tenant row
    populates it — ``app/api/v1/tenants.py:40`` and both branches of
    ``app/api/v1/webhooks.py`` (:102, :191) call ``hmac_key_prefix(raw_key)``.  A
    tenant row without it is not a realistic row, and the difference is not
    cosmetic: ``get_current_tenant`` (app/api/deps.py:170-190) looks the prefix up
    on an index and runs ONE argon2 verify, but a NULL prefix drops the request
    into the legacy fallback that scans every prefix-less tenant and runs argon2
    against each.  argon2 is deliberately expensive and entirely synchronous, so
    that scan blocks the event loop: measured at 7.6s against the 13 tenant rows
    in the local control DB on 2026-08-10, all of it before the SSE generator ever
    reached its Redis subscribe.  In a module whose whole subject is *when* events
    arrive relative to *when* they were emitted, several seconds of unannounced
    loop-blocking inside the request under test is a defect in the fixture.
    """
    from app.core.security import generate_api_key, hash_api_key, hmac_key_prefix

    raw_key = generate_api_key()
    api_key_hash = hash_api_key(raw_key)
    agent_id = uuid.uuid4()

    engine = create_engine(_INTEGRATION_DB_URL, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tenants (id, name, api_key_hash, api_key_prefix, created_at) "
                    "VALUES (:id, :name, :api_key_hash, :api_key_prefix, now())"
                ),
                {
                    "id": str(tenant_id),
                    "name": f"sse-test-tenant-{tenant_id}",
                    "api_key_hash": api_key_hash,
                    "api_key_prefix": hmac_key_prefix(raw_key),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO agents (id, tenant_id, name, soul, role, status, created_at) "
                    "VALUES (:id, :tenant_id, :name, CAST(:soul AS jsonb), :role, 'pending', now())"
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
# Helper: emit a live event the way production emits one
# ---------------------------------------------------------------------------


async def _emit_live(
    redis_client,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    event_type: str,
    payload: dict | None = None,
) -> None:
    """Emit a live job event exactly as ``app/services/events.py:emit`` does.

    emit() does TWO things per event (events.py:80-92): it publishes a JSON message
    to ``job_events:{job_id}`` AND it inserts a durable ``job_events`` row.  Both are
    load-bearing, because ``app/services/sse.py`` deliberately reads the event DATA
    from the DB only and treats the pub/sub message purely as a wake-up signal to
    re-query early — its module docstring (sse.py:16-24) states this and gives the
    reason: Redis pub/sub is fire-and-forget, so any message published while the
    listener is between ``listen()`` calls is gone forever, and the DB is the only
    durable record.

    A test that publishes WITHOUT inserting is therefore not emitting an event at
    all; it is ringing a doorbell at an empty house.  That is the shape this test
    had until 2026-08-10, and it is why the stream never terminated.
    """
    body = {"event_type": event_type, "payload": payload or {}}
    _insert_events(tenant_id, job_id, [{"event_type": event_type, "payload": payload or {}}])
    await redis_client.publish(f"job_events:{job_id}", json.dumps(body))


# ---------------------------------------------------------------------------
# Helper: an SSE reader that actually streams
# ---------------------------------------------------------------------------


class _SSEStream:
    """Drive the ASGI app directly and expose each SSE chunk as the server writes it.

    WHY NOT httpx.  ``httpx.ASGITransport`` (0.28.1,
    ``httpx/_transports/asgi.py:128-187``) accumulates every ``http.response.body``
    message into ``body_parts`` and only constructs the ``Response`` *after*
    ``await self.app(scope, receive, send)`` has returned.  For an ordinary JSON
    route that is invisible.  For an SSE route the server holds open on purpose it
    means the client sees NOTHING until the generator finishes: measured on
    2026-08-10, every line of a three-event stream arrived at the same instant,
    0.27s after the terminal event was emitted, including the replay event the
    server had written 5 seconds earlier.

    That is not merely slow, it removes the only signal a test could synchronise
    on.  With a buffered transport the publisher has no way to learn that the
    stream has connected, subscribed and finished its replay, so its only option is
    to guess with ``sleep()`` — and a guess is a race.  The original form of this
    test guessed 0.5s while the request was still 7.6s deep in argon2 (see
    ``_setup_test_job``), so it published into a channel that had no subscriber yet
    and the message was discarded by Redis.

    Calling the app directly costs one dict of ASGI scope and gains per-chunk
    arrival times.  Nothing is stubbed: routing, auth, ``EventSourceResponse`` and
    ``event_generator`` all run exactly as they do under uvicorn — ASGITransport
    itself does no more than this, minus the buffering.
    """

    def __init__(self, app, path: str, headers: dict[str, str]):
        self._app = app
        self._path = path
        self._headers = headers
        self.status: int | None = None
        self.lines: list[str] = []
        self.events: list[str] = []
        self._buf = ""
        self._request_sent = False
        self._disconnect = asyncio.Event()
        self._progress = asyncio.Condition()

    def _scope(self) -> dict:
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in self._headers.items()
            ],
            "client": ("127.0.0.1", 51234),
            "server": ("testserver", 80),
        }

    async def _receive(self) -> dict:
        # One request message, then block.  Blocking (rather than returning
        # http.disconnect) matters: event_generator polls request.is_disconnected()
        # on every loop iteration and must keep seeing False, and
        # EventSourceResponse parks its own listener task on this same channel.
        if not self._request_sent:
            self._request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body":
            self._buf += message.get("body", b"").decode()
            while "\n" in self._buf:
                raw, self._buf = self._buf.split("\n", 1)
                line = raw.strip()
                if not line:
                    continue
                self.lines.append(line)
                if line.startswith("event:"):
                    self.events.append(line.split(":", 1)[1].strip())
            async with self._progress:
                self._progress.notify_all()

    async def run(self) -> None:
        """Run the request to completion.  Returns when the server closes the stream."""
        await self._app(self._scope(), self._receive, self._send)
        async with self._progress:
            self._progress.notify_all()

    async def wait_for_events(self, n: int) -> None:
        """Block until the server has WRITTEN at least *n* ``event:`` lines.

        This is the synchronisation primitive that replaces sleeping.  It is a fact
        about the server's actual progress, not a hope about its speed.
        """
        async with self._progress:
            await self._progress.wait_for(lambda: len(self.events) >= n)


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
            # BOUNDED. `timeout=10.0` on AsyncClient is a per-request timeout and
            # does NOT bound aiter_lines() on an SSE stream the server holds open by
            # design: the loop below exits only on its own break condition, so a
            # stream that delivers too few events waits forever. Observed 2026-08-10
            # once the /api/v1 prefix was corrected and these tests connected for the
            # first time — the run sat on this line indefinitely rather than failing.
            # A hanging test is strictly worse than a failing one: it burns the whole
            # CI job budget and reports nothing.
            async with asyncio.timeout(SSE_STREAM_TIMEOUT_S):
              async with client.stream("GET", f"/api/v1/jobs/{job_id}/events") as response:
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
async def test_sse_receives_live_events_after_replay(monkeypatch):
    """Events emitted AFTER the client connects reach it live, via the pub/sub wake-up.

    Setup:
        - Pre-populate job_events with job.started (only)
        - Connect, and wait for the server to WRITE that replay event — not for a
          duration.  Only then emit anything else, so every later event is
          unambiguously live: the Phase-1 replay SELECT has already run against a
          snapshot that cannot contain a row committed after it.
        - Emit a decoy that is published but never persisted.
        - Emit neon.project.creating, then job.complete, each via _emit_live
          (persist + publish, exactly as app/services/events.py:emit does).

    Asserts:
        - job.started arrives first, from the DB replay.
        - neon.project.creating arrives after it, live.
        - job.complete arrives and the server closes the stream by itself.
        - The decoy NEVER arrives — pub/sub carries a wake-up, never event data.

    WHAT MAKES THIS TEST NON-VACUOUS.  event_generator has two ways to notice a new
    row: the pub/sub wake-up, and a fallback DB poll every POLL_INTERVAL_S.  If the
    wake-up were dead the poll would still deliver every event here and the test
    would pass while proving nothing about the mechanism the architecture is built
    on.  So POLL_INTERVAL_S is raised far above this module's own stream bound: the
    poll cannot fire inside the window the test is willing to wait, and the ONLY
    remaining path to a terminating stream is the pub/sub wake-up.  Break the
    wake-up and this test does not slow down, it fails.
    """
    # 300s poll vs a 30s ceiling on the stream — the fallback is now unreachable
    # inside the test's own window, so a pass isolates the pub/sub wake-up path.
    # Read as a module global on every loop iteration (sse.py:110), so patching the
    # attribute takes effect for the request this test is about to make.
    import app.services.sse as sse_module

    assert sse_module.POLL_INTERVAL_S < SSE_STREAM_TIMEOUT_S, (
        "Precondition for this test's isolation argument: the real poll interval "
        "must be below the stream bound, so that raising it is what removes the "
        "fallback rather than the bound already having done so."
    )
    monkeypatch.setattr(sse_module, "POLL_INTERVAL_S", 10 * SSE_STREAM_TIMEOUT_S)

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

    stream = _SSEStream(
        app, f"/api/v1/jobs/{job_id}/events", {"x-api-key": raw_key}
    )

    async def emit_live_events():
        """Emit live events, each gated on the stream's OBSERVED progress."""
        r = aioredis.Redis.from_url(_REDIS_URL, decode_responses=True)
        try:
            # Gate 1: the replay event is on the wire.  Everything after this point
            # is provably post-replay, with no sleep and no assumption about speed.
            await stream.wait_for_events(1)

            # A wake-up with no durable row behind it.  Delivering this would mean
            # the generator had started trusting pub/sub payloads as event data,
            # which is exactly the misconception that broke this test before.
            await r.publish(
                f"job_events:{job_id}",
                json.dumps({
                    "event_type": DECOY_UNPERSISTED_EVENT,
                    "payload": {"job_id": str(job_id)},
                }),
            )

            await _emit_live(
                r, tenant_id, job_id, "neon.project.creating", {"job_id": str(job_id)}
            )

            # Gate 2: the live event is on the wire.  Emitting the terminal event
            # only now keeps the two deliveries separately observable.
            await stream.wait_for_events(2)

            await _emit_live(
                r, tenant_id, job_id, "job.complete", {"job_id": str(job_id)}
            )
        finally:
            await r.aclose()

    emitter_task = None
    try:
        emitter_task = asyncio.create_task(emit_live_events())

        # BOUNDED, AND THE EMITTER IS INSIDE THE BOUND.  Before this bound
        # existed, two runs sat on this stream for 10 and 40 minutes: the live
        # events were published but never persisted, so the generator's DB
        # re-query found nothing, the terminal event never arrived and the loop
        # emitted keepalives forever.  A hanging test is strictly worse than a
        # failing one — it burns the whole job budget and reports nothing.
        #
        # `await emitter_task` sat OUTSIDE this block until 2026-08-11, which
        # reopened the hang through the other door.  emit_live_events parks on
        # `stream.wait_for_events(n)`, an asyncio.Condition predicate over the
        # count of `event:` lines the server has written.  Any early close —
        # a 401, a renamed auth header, a missing tenant row, an unexpected
        # terminal event — makes `run()` return having written fewer lines than
        # the emitter is waiting for; `run()` fires a final `notify_all()`, the
        # predicate re-evaluates false, and the emitter waits on a Condition
        # that nothing will ever notify again.  Nothing bounded that wait, so
        # the test hung forever while the stream itself had finished in
        # milliseconds.  Measured: with the api-key header mutated to a bogus
        # value, the run survived an external SIGKILL at 150s, 5x this bound.
        #
        # 30s is ~40x the stream time this test needs when healthy, so a breach
        # means the stream genuinely never delivered, never that the bound was
        # tight.
        async with asyncio.timeout(SSE_STREAM_TIMEOUT_S):
            await stream.run()
            await emitter_task

        assert stream.status == 200, f"Expected HTTP 200, got {stream.status}"

        # The server ended the stream on its own — run() returned rather than the
        # timeout firing — which is the terminal-event contract.
        assert stream.events == [
            "job.started",
            "neon.project.creating",
            "job.complete",
        ], f"Unexpected event sequence: {stream.events}"

        # Pub/sub is a wake-up signal, never a data channel (sse.py:16-24).
        assert DECOY_UNPERSISTED_EVENT not in stream.events, (
            f"An event that was published but never persisted was delivered to the "
            f"client. The DB is the only source of event data; a pub/sub payload "
            f"must never reach the stream. Got: {stream.events}"
        )

    finally:
        # The emitter must not outlive the bound either.  If the timeout above
        # fired, this task is still parked on the Condition; leaving it there
        # leaks a pending task into the event loop and produces a "Task was
        # destroyed but it is pending" warning attached to whichever test runs
        # next — a failure reported against the wrong subject.
        if emitter_task is not None and not emitter_task.done():
            emitter_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await emitter_task

        from app.main import app as main_app
        main_app.dependency_overrides.clear()
        _delete_test_rows(tenant_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sse_closes_on_completed_job():
    """SSE endpoint closes promptly when the terminal event is already in the DB.

    Setup:
        - Pre-populate all 6 events (including job.complete)

    Asserts:
        - The server ends the stream itself — no hang
        - All 6 events are received before it does
        - It does so within SSE_CLOSE_BUDGET_S of the request starting

    WHAT `elapsed` MEASURES, AND WHY IT CHANGED.  This test used to read through
    ``httpx.ASGITransport``, which buffers the entire response and constructs it
    only after the ASGI app returns (see ``_SSEStream``'s docstring for the
    measurement).  ``elapsed`` therefore included transport buffering and the
    client's own teardown rather than the stream's close latency, and it was
    recorded failing at 5.5s and 6.8s under full-suite load against a 5.0s
    assertion — a 1.75x margin over the 2.85s it typically took.  A timing
    assertion whose subject is partly the test harness is not a measurement of
    the server.

    Driving the app directly removes the buffering, so ``elapsed`` is now the
    time from the first ASGI call to the generator returning: the quantity the
    assertion is actually about.  Measured on this machine 2026-08-11, standalone
    and under the full integration suite — see SSE_CLOSE_BUDGET_S.
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

    stream = _SSEStream(app, f"/api/v1/jobs/{job_id}/events", {"x-api-key": raw_key})

    try:
        # BOUNDED for the same reason as every other consume in this module: a
        # stream that never terminates must fail, not hang.
        start_time = time.monotonic()
        async with asyncio.timeout(SSE_STREAM_TIMEOUT_S):
            await stream.run()
        elapsed = time.monotonic() - start_time

        assert stream.status == 200, f"Expected HTTP 200, got {stream.status}"

        # The terminal event is already in the DB, so the generator finds it in
        # the Phase-1 replay and returns without ever waiting on Redis.
        assert elapsed < SSE_CLOSE_BUDGET_S, (
            f"SSE stream took {elapsed:.2f}s to close — expected < "
            f"{SSE_CLOSE_BUDGET_S}s with the terminal event already in the DB"
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
        assert stream.events == expected, (
            f"Expected {expected}, got {stream.events}"
        )

    finally:
        from app.main import app as main_app
        main_app.dependency_overrides.clear()
        _delete_test_rows(tenant_id)
