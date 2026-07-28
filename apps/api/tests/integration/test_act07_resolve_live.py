"""
Integration substrate for ACT-07's live-database proof (Phase 22, plan 22-05).

Why this file exists: T-22-ACT-21 in 22-05-PLAN.md's threat model names the
gated integration module itself as the risk surface — a module that
accidentally reads or writes the real, configured control database instead
of an ephemeral one would execute adapter calls (even stubbed ones) and audit
writes against live tenant data. This module never imports, reads, or
constructs a connection from the platform's own production database
configuration anywhere in its body. Every database it touches is created fresh by the
fixture below, migrated to the control head, and dropped in a `finally` —
mirroring `tests/integration/test_red_team_rtx.py`'s `control_db_url`
fixture shape (create -> migrate -> yield -> terminate backends -> drop).

What is proved, and why unit-level mocks cannot prove it (22-VALIDATION.md's
"live proof" row): `test_confirmation_resolution.py` and
`test_pending_confirmation_routes.py` mock every database boundary, which is
exactly right for proving the RESOLVER's own decision logic in isolation —
but a mocked `db.execute()` can never prove that the real
`UPDATE ... WHERE resolved_at IS NULL ... RETURNING` claim actually excludes
a second concurrent winner, or that a real Postgres row transitions the way
`check_capability_access`/`apply_rate_and_constraint_checks` expect once a
ceiling is genuinely tightened between two real queries. That is what the
three tests below establish, against a real ephemeral Postgres database:

    test_approved_confirmation_executes_exactly_once_live
        Resolve a row 'approved' through the real route function (not
        mocked), then run the real execution path (the Celery task's own
        `run()` body, invoked directly rather than through a broker) and
        assert against the database: the row carries the approved
        resolution, exactly one `tool_calls_audit` row exists carrying the
        `approved_by_human` actor decision with no error, and the
        `tool_idempotency_keys` row for that key is `completed`.

    test_second_resolve_is_rejected_by_the_database_claim
        T-22-ACT-03's real-concurrency counterpart to the mocked unit proof
        (`test_confirmation_resolution.py::TestIdempotency`). Resolves the
        same row twice against the real database and asserts the second
        attempt is refused (409) and produces no second `tool_calls_audit`
        row — a fact only a real `UPDATE ... WHERE resolved_at IS NULL`
        claim, not a mock, can establish.

    test_tightened_ceiling_denies_a_previously_approved_confirmation
        SC3's live counterpart. Tightens the envelope's ceiling below the
        confirmation's stored amount AFTER approval but BEFORE execution,
        then runs the real execution path and asserts no successful audit
        row exists and the one audit row written carries the
        `capability.denial:max_amount_cents` error.

How the adapter is reached with no real provider credential and no real
money moved: every test wraps its execution-path call in
`app.services.red_team_probe.red_team_mode()` — the same module-private
ContextVar short-circuit `test_red_team_rtx.py` uses, which makes
`get_adapter_for_skill` return the in-memory `StubProviderAdapter` singleton
BEFORE any credential fetch (`provider_adapter.py:257-304`). Because
`asyncio.run()` copies the ambient `contextvars.Context` synchronously at
the moment it creates its Task (this happens inside the Celery task's own
`asyncio.run(execute_approved_confirmation(...))` call), entering
`red_team_mode()` in the surrounding synchronous test body before calling
the task is sufficient for the flag to be visible inside the coroutine it
spawns — no ContextVar has to be threaded through the task's own
(single, CLAUDE.md rule 4) `confirmation_id` argument.

Module-level import discipline (19-03/19-04's lesson, restated in
22-05-PLAN.md): standard library, `pytest`, and `unittest.mock` only at
module scope. No `app.*` import anywhere above the fixtures/tests that need
it — importing `app.services.red_team_probe`, `app.api.v1.pending_confirmations`,
or any other `app.*` module executes `app.core.config`'s module-level
`Settings()` validation, which requires environment variables this file
cannot guarantee are set outside a pytest run. Every `app.*` symbol below is
imported lazily, inside the function that uses it. This is what lets
`python -c "import tests.integration.test_act07_resolve_live"` succeed even
outside pytest, and what makes `--collect-only` (this module's automated
gate) succeed without a database, a broker, or `app.main` (whose own import
chain reaches `ragas` -> `langchain_community.chat_models.vertexai`, the
same reason `test_deploy_gate_redteam.py` and `test_aud03_audit_gap.py`
never import it either).

CLAUDE.md rule 9 (NO DOCKER): every fixture here uses local Postgres
(TEST_ADMIN_DB_URL / TEST_LOCAL_BASE) started directly as a local process.
No containerized orchestration tool and no container runtime is ever
referenced or started anywhere in this module.

Guards:
    - INTEGRATION_TESTS_ENABLED=1 gates the whole module (skip otherwise).
    - No local Redis is required: every seeded envelope carries
      `rate_limit=None`, so `apply_rate_and_constraint_checks` never reaches
      its Redis INCR+EXPIRE pipeline (`enforcement.py:308-318`) — the
      constraint check under test here is the ceiling, not the rate limit.

Deferred to plan 22-06 (autonomous:false): the operator's live run of this
gate against a real local Postgres, transcribed into 22-UAT.md. This module
has never been run live in this environment — there is no local PostgreSQL
server on this machine, and the configured control database is a live Neon
production endpoint this module must never touch.
"""

from __future__ import annotations

import os
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

INTEGRATION_TESTS_ENABLED = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not INTEGRATION_TESTS_ENABLED,
        reason=(
            "INTEGRATION_TESTS_ENABLED=1 required for ACT-07's live-database "
            "resolve/execute roundtrips (real local Postgres; no Redis, no "
            "real provider credential, no real ANTHROPIC_API_KEY needed)"
        ),
    ),
]

_TESTS_DIR = os.path.dirname(__file__)
_MUTATING_SKILL = "issue_refund"


# ---------------------------------------------------------------------------
# Fixture: ephemeral control Postgres DB, migrated to head "0019" — the same
# head this phase's own migration ledger confirms is unchanged (OD-3, no new
# column, no 0020). Mirrors test_red_team_rtx.py's control_db_url fixture
# exactly: create -> migrate -> yield -> terminate backends -> drop, in a
# `finally` so a raised assertion still drops the ephemeral database.
# ---------------------------------------------------------------------------


@pytest.fixture
def control_db_url():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, pool, text as sa_text

    admin_url = os.environ.get(
        "TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres"
    )
    local_base = os.environ.get("TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432")
    db_name = f"wchats_test_2205_act07_{uuid.uuid4().hex[:12]}"

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool)
    with admin_engine.connect() as conn:
        conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    conn_url = f"{local_base}/{db_name}"
    alembic_ini = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic.ini"))
    cfg = Config(alembic_ini)
    cfg.set_main_option("sqlalchemy.url", conn_url)
    command.upgrade(cfg, "0019")

    try:
        yield conn_url
    finally:
        admin_engine = create_engine(
            admin_url, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
        )
        try:
            with admin_engine.connect() as conn:
                conn.execute(
                    sa_text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :dbname AND pid <> pg_backend_pid()"
                    ),
                    {"dbname": db_name},
                )
                conn.execute(sa_text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        finally:
            admin_engine.dispose()


# ---------------------------------------------------------------------------
# Control-DB redirection for every module the resolver's execution path
# touches via get_sync_db — each does its own `from app.core.database import
# get_sync_db`, so patching app.core.database.get_sync_db alone would not
# reach any of them (a direct `from X import Y` binds Y into the importing
# module's own namespace at import time). Mirrors
# test_red_team_rtx.py's `_control_db_redirected`, extended with the one
# module that harness never needed: the runtime execution task itself.
# ---------------------------------------------------------------------------


@contextmanager
def _control_db_redirected(control_conn_url: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(control_conn_url)
    factory = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def _fake_get_sync_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    with ExitStack() as stack:
        for target in (
            "app.core.database.get_sync_db",
            "app.services.transactional.enforcement.get_sync_db",
            "app.services.transactional.audit.get_sync_db",
            "app.services.transactional.idempotency.get_sync_db",
            "app.services.transactional.tools.get_sync_db",
            "app.worker.tasks.runtime.confirmations.get_sync_db",
        ):
            stack.enter_context(patch(target, _fake_get_sync_db))
        try:
            yield
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# Seeding helpers — direct SQL against the ephemeral control DB, mirroring
# clean_tenant's own INSERT style in test_red_team_rtx.py rather than the
# ORM, so seeding never depends on the enforcement/resolver code under test.
# ---------------------------------------------------------------------------


def _seed_agent_and_envelope(control_db_url: str, *, max_amount_cents: int) -> tuple[str, str]:
    """Insert one tenant, one agent (with a Fernet-encrypted dummy connection
    string), and one enabled issue_refund capability_envelopes row.

    rate_limit is deliberately NULL — this is the ceiling under test, and a
    NULL rate_limit keeps apply_rate_and_constraint_checks out of its Redis
    pipeline entirely (enforcement.py:308-318), so this module needs no
    local Redis. The connection string only has to decrypt successfully;
    red_team_mode's short-circuit means its contents are never dialled.
    """
    import json

    from sqlalchemy import create_engine, text as sa_text

    from app.core.security import fernet_encrypt

    tenant_id = str(uuid4())
    agent_id = str(uuid4())
    encrypted_conn_str = fernet_encrypt("postgresql://unused-under-red-team-mode/tenant")

    engine = create_engine(control_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO tenants (id, name, api_key_hash) "
                    "VALUES (:id, 'ACT-07 Live Tenant', :hash)"
                ),
                {"id": tenant_id, "hash": f"act07-live-tenant-hash-{tenant_id}"},
            )
            conn.execute(
                sa_text(
                    "INSERT INTO agents (id, tenant_id, name, soul, role, neon_connection_string) "
                    "VALUES (:id, :tenant_id, 'ACT-07 Live Agent', CAST('{}' AS JSONB), "
                    "'customer_service', :conn_str)"
                ),
                {"id": agent_id, "tenant_id": tenant_id, "conn_str": encrypted_conn_str},
            )
            conn.execute(
                sa_text(
                    "INSERT INTO capability_envelopes "
                    "(agent_id, skill, enabled, rate_limit, constraints, "
                    "requires_confirmation, requires_identity_verification, actor_mode) "
                    "VALUES (:agent_id, :skill, true, NULL, CAST(:constraints AS JSONB), "
                    "true, false, 'always-on')"
                ),
                {
                    "agent_id": agent_id,
                    "skill": _MUTATING_SKILL,
                    "constraints": json.dumps({"max_amount_cents": max_amount_cents}),
                },
            )
    finally:
        engine.dispose()

    return agent_id, tenant_id


def _seed_pending_confirmation(
    control_db_url: str,
    agent_id: str,
    *,
    refund_amount_cents: int,
    idempotency_key: str | None = None,
) -> str:
    """Insert one unresolved pending_confirmations row for issue_refund."""
    import json

    from sqlalchemy import create_engine, text as sa_text

    confirmation_id = str(uuid4())
    arguments = {
        "idempotency_key": idempotency_key or str(uuid4()),
        "order_id": "act07-live-order",
        "refund_amount_cents": refund_amount_cents,
        "reason": "ACT-07 live-database proof",
    }
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    engine = create_engine(control_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO pending_confirmations "
                    "(id, agent_id, skill, arguments, expires_at) "
                    "VALUES (:id, :agent_id, :skill, CAST(:arguments AS JSONB), :expires_at)"
                ),
                {
                    "id": confirmation_id,
                    "agent_id": agent_id,
                    "skill": _MUTATING_SKILL,
                    "arguments": json.dumps(arguments),
                    "expires_at": expires_at,
                },
            )
    finally:
        engine.dispose()

    return confirmation_id


def _tighten_ceiling(control_db_url: str, agent_id: str, *, max_amount_cents: int) -> None:
    import json

    from sqlalchemy import create_engine, text as sa_text

    engine = create_engine(control_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "UPDATE capability_envelopes SET constraints = CAST(:constraints AS JSONB) "
                    "WHERE agent_id = :agent_id AND skill = :skill"
                ),
                {
                    "agent_id": agent_id,
                    "skill": _MUTATING_SKILL,
                    "constraints": json.dumps({"max_amount_cents": max_amount_cents}),
                },
            )
    finally:
        engine.dispose()


def _query_all(control_db_url: str, sql: str, params: dict) -> list[dict]:
    from sqlalchemy import create_engine, text as sa_text

    engine = create_engine(control_db_url)
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(sa_text(sql), params).mappings().all()]
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# The real resolve route, called directly — no ASGI transport, no app.main.
# Calling the route's own async function with a hand-built AsyncSession and a
# minimal tenant stand-in reaches the exact production code path
# (_get_owned_agent, the atomic UPDATE ... RETURNING claim, the commit-
# before-enqueue ordering) without needing FastAPI's dependency-injection
# machinery or a running server.
# ---------------------------------------------------------------------------


def _resolve_via_route(
    control_db_url: str,
    agent_id: str,
    tenant_id: str,
    confirmation_id: str,
    resolution: str,
) -> tuple[object | None, Exception | None, bool]:
    """Return (response_or_None, http_exception_or_None, dispatched: bool).

    dispatched is True only when the route reached its dispatch branch
    (claimed resolution == 'approved' and a mutating skill) — the Celery
    `.delay()` call itself is patched out here so this helper never touches a
    broker; the execution path is run separately, for real, via
    `_run_execution_task` below, exactly mirroring how a worker consumes a
    dispatched task out of process in production.
    """
    import asyncio
    from types import SimpleNamespace
    from uuid import UUID as _UUID

    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async_url = control_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    async def _run() -> tuple[object | None, Exception | None, bool]:
        from app.api.v1.pending_confirmations import resolve_pending_confirmation
        from app.schemas.pending_confirmation import PendingConfirmationResolve

        engine = create_async_engine(async_url)
        dispatched = {"called": False}

        def _capture_dispatch(_confirmation_id: str) -> None:
            dispatched["called"] = True

        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                tenant = SimpleNamespace(id=_UUID(tenant_id))
                with patch(
                    "app.worker.tasks.runtime.confirmations.resolve_approved_confirmation.delay",
                    side_effect=_capture_dispatch,
                ):
                    try:
                        response = await resolve_pending_confirmation(
                            agent_id=_UUID(agent_id),
                            confirmation_id=_UUID(confirmation_id),
                            body=PendingConfirmationResolve(resolution=resolution),
                            db=session,
                            tenant=tenant,
                        )
                        return response, None, dispatched["called"]
                    except HTTPException as exc:
                        return None, exc, dispatched["called"]
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _run_execution_task(control_db_url: str, confirmation_id: str) -> dict:
    """Run the real Celery task body directly (no broker) inside
    red_team_mode(), against the ephemeral control DB.

    Calling the task instance directly (`task(confirmation_id)`) rather than
    `.delay()`/`.apply()` invokes its bound `run()` synchronously — the
    standard way to exercise a `bind=True` Celery task without a broker.
    """
    from app.services.red_team_probe import red_team_mode
    from app.worker.tasks.runtime.confirmations import resolve_approved_confirmation

    with _control_db_redirected(control_db_url):
        with red_team_mode():
            return resolve_approved_confirmation(confirmation_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_approved_confirmation_executes_exactly_once_live(control_db_url):
    agent_id, tenant_id = _seed_agent_and_envelope(control_db_url, max_amount_cents=100_000)
    confirmation_id = _seed_pending_confirmation(
        control_db_url, agent_id, refund_amount_cents=5_000
    )

    response, exc, dispatched = _resolve_via_route(
        control_db_url, agent_id, tenant_id, confirmation_id, "approved"
    )
    assert exc is None, f"the resolve route raised unexpectedly: {exc}"
    assert response.resolution == "approved"
    assert dispatched is True, "an approved mutating-skill row must dispatch execution"

    outcome = _run_execution_task(control_db_url, confirmation_id)
    assert outcome["status"] == "executed", outcome

    rows = _query_all(
        control_db_url,
        "SELECT resolved_at, resolution FROM pending_confirmations WHERE id = :id",
        {"id": confirmation_id},
    )
    assert len(rows) == 1
    assert rows[0]["resolved_at"] is not None
    assert rows[0]["resolution"] == "approved"

    audit_rows = _query_all(
        control_db_url,
        "SELECT error FROM tool_calls_audit "
        "WHERE agent_id = :agent_id AND skill = :skill AND actor_decision = 'approved_by_human'",
        {"agent_id": agent_id, "skill": _MUTATING_SKILL},
    )
    assert len(audit_rows) == 1, (
        "exactly one tool_calls_audit row must exist for this human-approved execution"
    )
    assert audit_rows[0]["error"] is None

    idem_rows = _query_all(
        control_db_url,
        "SELECT status, result FROM tool_idempotency_keys "
        "WHERE agent_id = :agent_id AND skill = :skill",
        {"agent_id": agent_id, "skill": _MUTATING_SKILL},
    )
    assert len(idem_rows) == 1
    assert idem_rows[0]["status"] == "completed"
    assert idem_rows[0]["result"] is not None


def test_second_resolve_is_rejected_by_the_database_claim(control_db_url):
    agent_id, tenant_id = _seed_agent_and_envelope(control_db_url, max_amount_cents=100_000)
    confirmation_id = _seed_pending_confirmation(
        control_db_url, agent_id, refund_amount_cents=5_000
    )

    first_response, first_exc, first_dispatched = _resolve_via_route(
        control_db_url, agent_id, tenant_id, confirmation_id, "approved"
    )
    assert first_exc is None
    assert first_response.resolution == "approved"
    assert first_dispatched is True

    # Run the winner's execution for real, so this test's audit-row count
    # assertion below is meaningful (one row must exist and stay at one),
    # not vacuously true because nothing was ever executed.
    outcome = _run_execution_task(control_db_url, confirmation_id)
    assert outcome["status"] == "executed", outcome

    second_response, second_exc, second_dispatched = _resolve_via_route(
        control_db_url, agent_id, tenant_id, confirmation_id, "rejected"
    )
    assert second_response is None
    assert second_exc is not None
    assert second_exc.status_code == 409, (
        "the atomic claim's WHERE resolved_at IS NULL guard must refuse a second "
        "resolve of the same row with 409, against a REAL database"
    )
    assert second_dispatched is False, "a refused second resolve must never dispatch"

    rows = _query_all(
        control_db_url,
        "SELECT resolution FROM pending_confirmations WHERE id = :id",
        {"id": confirmation_id},
    )
    assert len(rows) == 1
    assert rows[0]["resolution"] == "approved", (
        "the second (rejected) resolve attempt must never overwrite the row the "
        "first caller already claimed as approved"
    )

    audit_rows = _query_all(
        control_db_url,
        "SELECT error FROM tool_calls_audit "
        "WHERE agent_id = :agent_id AND skill = :skill AND actor_decision = 'approved_by_human'",
        {"agent_id": agent_id, "skill": _MUTATING_SKILL},
    )
    assert len(audit_rows) == 1, (
        "the refused second resolve must produce no second tool_calls_audit row"
    )


def test_tightened_ceiling_denies_a_previously_approved_confirmation(control_db_url):
    agent_id, tenant_id = _seed_agent_and_envelope(control_db_url, max_amount_cents=100_000)
    confirmation_id = _seed_pending_confirmation(
        control_db_url, agent_id, refund_amount_cents=5_000
    )

    response, exc, dispatched = _resolve_via_route(
        control_db_url, agent_id, tenant_id, confirmation_id, "approved"
    )
    assert exc is None
    assert response.resolution == "approved"
    assert dispatched is True

    # Tighten AFTER approval, BEFORE execution — SC3's live scenario: an
    # approval granted under looser settings must not execute under them.
    _tighten_ceiling(control_db_url, agent_id, max_amount_cents=1_000)

    outcome = _run_execution_task(control_db_url, confirmation_id)
    assert outcome["status"] == "denied", outcome
    assert outcome["reason"] == "capability.denial:max_amount_cents", outcome

    audit_rows = _query_all(
        control_db_url,
        "SELECT error FROM tool_calls_audit "
        "WHERE agent_id = :agent_id AND skill = :skill AND actor_decision = 'approved_by_human'",
        {"agent_id": agent_id, "skill": _MUTATING_SKILL},
    )
    assert len(audit_rows) == 1
    assert audit_rows[0]["error"] == "capability.denial:max_amount_cents", (
        "no successful audit row may exist once the ceiling has been tightened "
        "below the confirmation's stored amount"
    )
