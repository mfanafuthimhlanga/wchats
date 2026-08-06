"""
Integration tests for OPS-16 canary prompt-version routing (agent.py dispatch).

Gated by INTEGRATION_TESTS_ENABLED=1 + CONTROL_DB_SYNC_URL containing 'test'
(same convention as test_migration_0018.py's DB-roundtrip gate) — these tests
exercise resolve_prompt_version() and _resolve_turn_prompt_version() against a
REAL local control-DB Postgres instance with real prompt_versions rows, not
mocks, because the weighted-random distribution and label-filtering
correctness genuinely need a real SELECT to be trustworthy (not just a
mocked-out one, as in tests/unit/test_prompt_versions.py).

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
_CONTROL_DB_SYNC_URL = os.environ.get("CONTROL_DB_SYNC_URL", "")
_HAS_TEST_DB = INTEGRATION_TESTS and "test" in _CONTROL_DB_SYNC_URL.lower()


def _require_real_db():
    if not _HAS_TEST_DB:
        pytest.skip(
            "INTEGRATION_TESTS_ENABLED=1 and a CONTROL_DB_SYNC_URL containing "
            "'test' are required for this real-Postgres OPS-16 test"
        )


@pytest.fixture()
def control_session():
    """Real sync SQLAlchemy session against CONTROL_DB_SYNC_URL, migrated to head.

    Creates and tears down a throwaway `agents` row + its `prompt_versions`
    rows so this test never pollutes a shared test DB across runs.
    """
    _require_real_db()

    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from alembic import command

    _tests_dir = os.path.dirname(os.path.dirname(__file__))
    alembic_ini = os.path.normpath(os.path.join(_tests_dir, "../alembic.ini"))
    cfg = Config(alembic_ini)
    cfg.set_main_option("sqlalchemy.url", _CONTROL_DB_SYNC_URL)
    command.upgrade(cfg, "head")

    engine = create_engine(_CONTROL_DB_SYNC_URL)
    Session = sessionmaker(engine)
    session = Session()

    created_agent_ids: list = []

    yield session, created_agent_ids

    # Cleanup: delete only what this test created.
    from app.models.agent import Agent
    from app.models.prompt_version import PromptVersion

    for agent_id in created_agent_ids:
        session.query(PromptVersion).filter(PromptVersion.agent_id == agent_id).delete()
        session.query(Agent).filter(Agent.id == agent_id).delete()
    session.commit()
    session.close()
    engine.dispose()


def _make_real_agent(session, created_agent_ids: list):
    """Insert a throwaway Agent row (control DB) and return it."""
    from app.models.agent import Agent

    agent = Agent(
        id=uuid4(),
        tenant_id=uuid4(),
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
    created_agent_ids.append(agent.id)
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

    session, created_agent_ids = control_session
    agent = _make_real_agent(session, created_agent_ids)

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

    session, created_agent_ids = control_session
    agent = _make_real_agent(session, created_agent_ids)

    version_id, soul_override = resolve_prompt_version(session, str(agent.id))
    assert version_id is None
    assert soul_override is None


# ---------------------------------------------------------------------------
# _resolve_turn_prompt_version — per-conversation stickiness (A-CANARY)
# ---------------------------------------------------------------------------


class _FakeTenantConn:
    """In-memory replay of the conversations.metadata jsonb_set contract that
    _set_prompt_version_id/_set_sdk_session_id use — no live tenant Neon
    project is available in this local-dev environment (CLAUDE.md: tenant DBs
    are per-tenant Neon projects, not a local Postgres install)."""

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
    """First turn resolves + stores; second turn reuses without re-rolling
    (A-CANARY: canary stickiness is per-conversation, never mid-conversation)."""
    from app.worker.tasks.runtime.agent import _resolve_turn_prompt_version

    session, created_agent_ids = control_session
    agent = _make_real_agent(session, created_agent_ids)
    _make_real_version(session, agent.id, 1, "production", soul_role="sticky role")

    fake_tenant_conn = _FakeTenantConn()

    # First turn: no existing_prompt_version_id -> resolves and stores.
    first_id, first_override = _resolve_turn_prompt_version(
        session,
        fake_tenant_conn,
        agent_id=str(agent.id),
        local_conversation_id=str(uuid4()),
        existing_prompt_version_id=None,
    )
    assert first_id is not None
    assert first_override["soul_role"] == "sticky role"
    assert fake_tenant_conn.metadata.get("prompt_version_id") == first_id

    # Second turn: existing_prompt_version_id passed through (as the
    # conversation-branching code in run_agent_turn would read it back from
    # conv_row["metadata"]) -> must reuse the SAME version, never re-roll.
    second_id, second_override = _resolve_turn_prompt_version(
        session,
        fake_tenant_conn,
        agent_id=str(agent.id),
        local_conversation_id=str(uuid4()),
        existing_prompt_version_id=fake_tenant_conn.metadata["prompt_version_id"],
    )
    assert second_id == first_id
    assert second_override["soul_role"] == "sticky role"


def test_resolve_turn_prompt_version_never_raises_on_bad_db(control_session):
    """T-21-09-05: a resolution failure never raises — caller falls back to
    the live agent soul (None, None)."""
    from app.worker.tasks.runtime.agent import _resolve_turn_prompt_version

    session, created_agent_ids = control_session
    agent = _make_real_agent(session, created_agent_ids)

    broken_conn = MagicMock()
    broken_conn.cursor.side_effect = RuntimeError("simulated tenant DB outage")

    result_id, result_override = _resolve_turn_prompt_version(
        session,
        broken_conn,
        agent_id=str(agent.id),
        local_conversation_id=str(uuid4()),
        existing_prompt_version_id=None,
    )
    # No prompt_versions rows exist for this agent either, but even if one
    # did, the broken tenant_conn write must not propagate as an exception.
    assert result_id is None
    assert result_override is None


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
