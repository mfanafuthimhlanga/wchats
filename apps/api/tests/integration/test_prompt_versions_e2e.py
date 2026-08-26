"""
Integration tests for OPS-16 canary prompt-version routing (agent.py dispatch).

Gated by INTEGRATION_TESTS_ENABLED=1 alone — these tests exercise
resolve_prompt_version() and _resolve_turn_prompt_version() against a REAL
Postgres control schema with real prompt_versions rows, not mocks, because the
weighted-random distribution and label-filtering correctness genuinely need a
real SELECT to be trustworthy (not just a mocked-out one, as in
tests/unit/test_prompt_versions.py).

The database is a THROWAWAY one created per test (see `control_session`), not
CONTROL_DB_SYNC_URL. The original gate here was
"CONTROL_DB_SYNC_URL contains the substring 'test'", borrowed from
test_migration_0018.py, and it was the only thing standing between this
fixture's `command.upgrade(cfg, "head")` and whatever that variable named —
which CLAUDE.md records is live Neon production outside the integration
conftest. A substring is not an isolation mechanism; owning the database is.
So the fixture no longer reads CONTROL_DB_SYNC_URL at all, and with nothing
left to protect, the substring gate is gone rather than relaxed.

Scope boundary (honest, not a shortcut): this does NOT drive a live Claude
Agent SDK turn through run_agent_turn end-to-end — that would require
ANTHROPIC_API_KEY, a running Celery worker, and a live agent conversation
(see tests/integration/test_agent_e2e.py's AGENT_E2E_ENABLED gate for that
class of test). Per CLAUDE.md (local dev, no Docker, 4GB RAM) and this plan's
disk_note, that class of test is out of scope here and is deferred to the
live-verify gate (RESEARCH.md's "Live-DB verification gates" convention,
already established for Phases 13-16 in STATE.md). What IS tested here, with
a real control-DB Postgres:
  - resolve_prompt_version's weighted canary distribution and its structural
    exclusion of 'draft'/'archived' rows (T-21-09-01).
  - _resolve_turn_prompt_version's per-conversation stickiness: first-turn
    resolve + store vs. subsequent-turn reuse without re-rolling (A-CANARY).
    The tenant-DB conversations.metadata write/read is simulated with an
    in-memory fake connection (no live tenant Neon project is available in
    this environment — tenant DBs are per-tenant Neon projects per
    CLAUDE.md, not a local Postgres install); the fake faithfully replays
    the same jsonb_set-style contract _set_prompt_version_id uses.
  - turn_metrics.prompt_version_id bind-order, via a mock psycopg2 cursor
    (same reasoning — no live tenant Postgres available locally).
"""

from __future__ import annotations

import os
import random
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

_TESTS_DIR = os.path.dirname(__file__)

# Local-Postgres admin credentials. Same env-var pair every other ephemeral-DB
# fixture in this suite uses (test_act07_resolve_live.py:144,
# test_red_team_rtx.py:135, tests/integration/_tenant_db.py:32), so a machine
# with non-default local credentials configures them all from one place.
_ADMIN_DB_URL = os.environ.get(
    "TEST_ADMIN_DB_URL", "postgresql://wchats:wchats@localhost:5432/postgres"
)
_LOCAL_BASE = os.environ.get(
    "TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432"
)

# Pinned, not "head". The control chain's head is 0019 and this module's
# subject (prompt_versions) landed at 0018; pinning means a future 0020 that
# breaks on a fresh database fails in its own migration test rather than here,
# and matches what every other ephemeral control-DB fixture in this suite does.
_CONTROL_HEAD = "0019"


def _require_real_db():
    if not INTEGRATION_TESTS:
        pytest.skip(
            "INTEGRATION_TESTS_ENABLED=1 (with a local Postgres reachable at "
            "TEST_ADMIN_DB_URL) is required for this real-Postgres OPS-16 test"
        )


@pytest.fixture()
def control_session():
    """Sync SQLAlchemy session against a THROWAWAY control database.

    Create -> migrate to 0019 -> yield -> close -> terminate backends -> drop,
    mirroring test_act07_resolve_live.py's `control_db_url` fixture. Requires
    alembic/env.py's 2026-08-11 fix, without which the ambient
    CONTROL_DB_SYNC_URL silently retargets the migration at the shared control
    DB and every INSERT below lands in an unmigrated database.

    Why a database of its own, rather than the previous
    "migrate CONTROL_DB_SYNC_URL if it has 'test' in it":

    1. That fixture ran `alembic upgrade head` against a database it did not
       own. CLAUDE.md records that CONTROL_DB_SYNC_URL/CONTROL_DB_URL point at
       live Neon production outside the integration conftest, so a substring
       match was the entire safety boundary around a schema migration. It also
       INSERTed into and DELETEd from that database while other tests in the
       same session were reading it.
    2. Owning the database makes teardown total. The old row-by-row cleanup
       could not run at all if a test left the session in a failed
       transaction, and it only deleted `agents`/`prompt_versions` rows it had
       recorded -- anything a code path under test wrote was left behind.
       DROP DATABASE in a `finally` has neither failure mode, which is why the
       `created_agent_ids` accumulator is gone rather than kept.

    Create and migrate sit INSIDE the try, unlike the fixtures this mirrors: a
    migration that raises would otherwise leak the database it just created.
    """
    _require_real_db()

    from alembic.config import Config
    from sqlalchemy import create_engine, pool
    from sqlalchemy import text as sa_text
    from sqlalchemy.orm import sessionmaker

    from alembic import command

    db_name = f"wchats_test_ops16_{uuid4().hex[:12]}"

    def _admin_engine():
        return create_engine(
            _ADMIN_DB_URL, isolation_level="AUTOCOMMIT", poolclass=pool.NullPool
        )

    admin_engine = _admin_engine()
    try:
        with admin_engine.connect() as conn:
            conn.execute(sa_text(f'CREATE DATABASE "{db_name}"'))
    finally:
        admin_engine.dispose()

    conn_url = f"{_LOCAL_BASE}/{db_name}"
    try:
        alembic_ini = os.path.normpath(os.path.join(_TESTS_DIR, "../../alembic.ini"))
        cfg = Config(alembic_ini)
        cfg.set_main_option("sqlalchemy.url", conn_url)
        command.upgrade(cfg, _CONTROL_HEAD)

        engine = create_engine(conn_url, poolclass=pool.NullPool)
        session = sessionmaker(engine)()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()
    finally:
        admin_engine = _admin_engine()
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


def _make_real_agent(session):
    """Insert a throwaway Agent row (control DB) and return it.

    Seeds the owning tenant first. `agents.tenant_id` carries a real FK
    (`agents_tenant_id_fkey`), so a bare `tenant_id=uuid4()` raises
    ForeignKeyViolation against a live database -- a constraint that is
    invisible to statement compilation and only shows up when the INSERT
    actually reaches Postgres.
    """
    from sqlalchemy import text as sa_text

    from app.models.agent import Agent

    tenant_id = uuid4()
    session.execute(
        sa_text(
            "INSERT INTO tenants (id, name, api_key_hash) "
            "VALUES (:id, 'OPS-16 E2E test tenant', :hash)"
        ),
        {"id": str(tenant_id), "hash": f"ops16-e2e-tenant-hash-{tenant_id}"},
    )

    agent = Agent(
        id=uuid4(),
        tenant_id=tenant_id,
        name="OPS-16 E2E test agent",
        soul={},
        role="support",
        status="ready",
        soul_role="live role",
        soul_voice="live voice",
        soul_do_list=["live do"],
        soul_donot_list=["live dont"],
    )
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return agent


def _make_real_version(session, agent_id, version_number, label, canary_percent=0, **soul):
    from app.models.prompt_version import PromptVersion

    v = PromptVersion(
        agent_id=agent_id,
        version_number=version_number,
        label=label,
        canary_percent=canary_percent,
        soul_role=soul.get("soul_role", f"role-v{version_number}"),
        soul_voice=soul.get("soul_voice", f"voice-v{version_number}"),
        soul_do_list=soul.get("soul_do_list", [f"do-v{version_number}"]),
        soul_donot_list=soul.get("soul_donot_list", [f"dont-v{version_number}"]),
    )
    session.add(v)
    session.commit()
    session.refresh(v)
    return v


# ---------------------------------------------------------------------------
# resolve_prompt_version — real DB, weighted distribution + draft exclusion
# ---------------------------------------------------------------------------


def test_resolve_prompt_version_weighted_distribution_real_db(control_session):
    """Seeded weighted-random pick against REAL prompt_versions rows lands
    close to canary_percent (target 40%, tolerance +-6pp over 3000 draws)."""
    from app.services.prompt_version_service import resolve_prompt_version

    session = control_session
    agent = _make_real_agent(session)

    production = _make_real_version(session, agent.id, 1, "production")
    canary = _make_real_version(session, agent.id, 2, "canary", canary_percent=40)
    # A draft row for the same agent — must NEVER be selected (T-21-09-01).
    _make_real_version(session, agent.id, 3, "draft")

    random.seed(20260716)
    n = 3000
    canary_hits = 0
    for _ in range(n):
        version_id, _ = resolve_prompt_version(session, str(agent.id))
        assert version_id in (str(production.id), str(canary.id)), (
            "resolve_prompt_version returned a version outside "
            "{production, canary} — the draft row leaked through"
        )
        if version_id == str(canary.id):
            canary_hits += 1

    observed_pct = (canary_hits / n) * 100
    assert 34.0 <= observed_pct <= 46.0, (
        f"Expected ~40% canary routing against a real DB, observed {observed_pct:.1f}%"
    )


def test_resolve_prompt_version_no_versions_falls_back_to_none(control_session):
    """An agent with zero prompt_versions rows resolves to (None, None) — the
    caller's fallback to the live agent soul, never a crash."""
    from app.services.prompt_version_service import resolve_prompt_version

    session = control_session
    agent = _make_real_agent(session)

    version_id, soul_override = resolve_prompt_version(session, str(agent.id))
    assert version_id is None
    assert soul_override is None


# ---------------------------------------------------------------------------
# _resolve_turn_prompt_version — per-conversation stickiness (A-CANARY)
# ---------------------------------------------------------------------------


class _FakeTenantConn:
    """In-memory replay of the conversations.metadata jsonb_set contract that
    _set_prompt_version_id uses. No live tenant Neon project is available in
    this local-dev environment (CLAUDE.md: tenant DBs are per-tenant Neon
    projects, not a local Postgres install)."""

    def __init__(self):
        self.metadata: dict = {}
        self.commits = 0

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1


class _FakeCursor:
    def __init__(self, conn: "_FakeTenantConn"):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        # _set_prompt_version_id's SQL: (prompt_version_id, conv_id)
        prompt_version_id, _conv_id = params
        self._conn.metadata["prompt_version_id"] = prompt_version_id


def test_resolve_turn_prompt_version_sticky_across_turns(control_session):
    """First turn resolves and asks to be stored; second turn reuses without
    re-rolling (A-CANARY: canary stickiness is per-conversation, never
    mid-conversation).

    Updated for BACKLOG 2.6 (settled 2026-08-07, "resolve before, commit
    after"): `_resolve_turn_prompt_version` no longer writes. It is a control-DB
    read that RETURNS whether the caller must persist, and `run_agent_turn`
    does the write once `build_agent_turn` has returned, so a turn that dies in
    the seam re-rolls instead of leaving the conversation sticky to a
    version that never served it. The persist call itself is exercised through
    `_set_prompt_version_id` directly here, against the same fake tenant conn,
    because that is the contract the caller now invokes.
    """
    from app.worker.tasks.runtime.agent import (
        _resolve_turn_prompt_version,
        _set_prompt_version_id,
    )

    session = control_session
    agent = _make_real_agent(session)
    _make_real_version(session, agent.id, 1, "production", soul_role="sticky role")

    fake_tenant_conn = _FakeTenantConn()
    conv_id = str(uuid4())

    # First turn: no existing_prompt_version_id -> resolves, and reports that
    # the caller must persist.
    first_id, first_override, needs_persist = _resolve_turn_prompt_version(
        session,
        agent_id=str(agent.id),
        local_conversation_id=conv_id,
        existing_prompt_version_id=None,
    )
    assert first_id is not None
    assert first_override["soul_role"] == "sticky role"
    assert needs_persist is True
    assert fake_tenant_conn.metadata.get("prompt_version_id") is None, (
        "the resolver wrote to the tenant DB. It must not: the write belongs "
        "behind a successful build_agent_turn (BACKLOG 2.6)."
    )

    # What run_agent_turn does next, once the options exist.
    _set_prompt_version_id(fake_tenant_conn, conv_id, first_id)
    assert fake_tenant_conn.metadata.get("prompt_version_id") == first_id

    # Second turn: existing_prompt_version_id passed through (as the
    # conversation-branching code in run_agent_turn would read it back from
    # conv_row["metadata"]) -> must reuse the SAME version, never re-roll, and
    # nothing to persist because the id is already stored.
    second_id, second_override, second_needs_persist = _resolve_turn_prompt_version(
        session,
        agent_id=str(agent.id),
        local_conversation_id=str(uuid4()),
        existing_prompt_version_id=fake_tenant_conn.metadata["prompt_version_id"],
    )
    assert second_id == first_id
    assert second_override["soul_role"] == "sticky role"
    assert second_needs_persist is False


def test_resolve_turn_prompt_version_never_raises_on_bad_control_db(control_session):
    """T-21-09-05: a resolution failure never raises — caller falls back to
    the live agent soul (None, None, False).

    The failure injected is now a CONTROL-DB one, because that is the only
    database this function still touches: the tenant write moved out to
    `run_agent_turn` under BACKLOG 2.6, and `run_agent_turn` wraps it in its own
    try/except so a tenant outage still cannot fail a turn.
    """
    from app.worker.tasks.runtime.agent import _resolve_turn_prompt_version

    session = control_session
    agent = _make_real_agent(session)

    broken_session = MagicMock()
    broken_session.get.side_effect = RuntimeError("simulated control DB outage")

    result_id, result_override, needs_persist = _resolve_turn_prompt_version(
        broken_session,
        agent_id=str(agent.id),
        local_conversation_id=str(uuid4()),
        existing_prompt_version_id=str(uuid4()),
    )
    assert result_id is None
    assert result_override is None
    assert needs_persist is False


# ---------------------------------------------------------------------------
# turn_metrics.prompt_version_id bind order (mock cursor — see module docstring)
# ---------------------------------------------------------------------------


def test_write_turn_metrics_binds_prompt_version_id():
    """turn_metrics INSERT must bind prompt_version_id (not hardcode NULL)."""
    from app.worker.tasks.runtime.agent import _write_turn_metrics

    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    a_prompt_version_id = str(uuid4())

    _write_turn_metrics(
        fake_conn,
        job_id="job-1",
        conversation_id="conv-1",
        agent_id="agent-1",
        cost_usd=0.01,
        num_turns=2,
        latency_ms=100,
        escalated=False,
        tool_count=1,
        stop_reason="end_turn",
        prompt_version_id=a_prompt_version_id,
    )

    args, _ = fake_cursor.execute.call_args
    _sql, bind_params = args
    assert a_prompt_version_id in bind_params, (
        "prompt_version_id must be a bound parameter on the turn_metrics INSERT"
    )


def test_write_turn_metrics_defaults_prompt_version_id_to_none():
    """When no version was resolved, the column is bound NULL (not omitted)."""
    from app.worker.tasks.runtime.agent import _write_turn_metrics

    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    _write_turn_metrics(
        fake_conn,
        job_id="job-2",
        conversation_id="conv-2",
        agent_id="agent-2",
        cost_usd=None,
        num_turns=None,
        latency_ms=50,
        escalated=False,
        tool_count=0,
        stop_reason=None,
    )

    args, _ = fake_cursor.execute.call_args
    _sql, bind_params = args
    assert bind_params[-1] is None
