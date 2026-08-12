"""BACKLOG 1.14 — the repaired statements, executed by a real PostgreSQL.

This module exists because of a specific gap, and the gap is the point:

    Every existing test of these three statements mocked the session, so no
    database ever parsed the SQL. `test_digest_service.py` has four tests, and
    the only one reaching the INSERT region seeds `fetchone` to return a row so
    the function returns EARLY — the INSERT never runs. `MagicMock.execute`
    accepts any string, including one Postgres rejects outright.

    The result: `deployment_service`'s blast-radius queries and `digest.py`'s
    WR-02 idempotency INSERT were syntactically invalid for their whole lives,
    and the suite was green throughout.

`tests/unit/test_sql_paramstyle_collisions.py` gates the class statically and
needs no database. This module is the other half: it takes the statements from
their real source and makes a real server parse and run them. A static scan
proves the shape; only execution proves the query.

Requires INTEGRATION_TESTS_ENABLED=1 and INTEGRATION_DB_URL (local PostgreSQL).
"""

from __future__ import annotations

import ast
import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy import text as sa_text

INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not INTEGRATION_TESTS,
        reason=(
            "INTEGRATION_TESTS_ENABLED=1 and a local PostgreSQL are required — the "
            "whole point of this module is that a real server parses the SQL"
        ),
    ),
]

APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def _statement_texts(path: Path) -> list[str]:
    """Every `text("...")` literal in a module, read from its real source.

    Reading the statements out of the file rather than copying them here is
    deliberate: a copy would drift, and a drifted copy that still passes is how
    `test_digest_service.py` stayed green over a broken INSERT.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "text":
            if node.args:
                try:
                    value = ast.literal_eval(node.args[0])
                except (ValueError, SyntaxError):
                    continue
                if isinstance(value, str):
                    out.append(value)
    return out


@pytest.fixture
def control_engine():
    url = os.environ.get("INTEGRATION_DB_URL")
    if not url:
        pytest.skip("INTEGRATION_DB_URL required")
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_the_blast_radius_queries_execute_against_postgres(control_engine) -> None:
    """Both statements from deployment_service, run by the real server.

    Before the fix each raised `psycopg2.errors.SyntaxError: syntax error at or
    near ":"`, the caller caught it, and the checklist reported every
    configured_max_* / observed_max_* as None beside healthy-looking thresholds.
    """
    path = APP_ROOT / "services" / "deployment_service.py"
    stmts = [s for s in _statement_texts(path) if "tool_calls_audit" in s and "window_days" in s]
    assert len(stmts) == 2, f"expected the 2 blast-radius statements, found {len(stmts)}"

    agent_id = str(uuid.uuid4())
    for sql in stmts:
        with control_engine.connect() as conn:
            # Must not raise. The value is None for an agent with no audit rows,
            # which is the honest empty state — as distinct from the broken one.
            conn.execute(sa_text(sql), {"agent_id": agent_id, "window_days": 7}).scalar()


def test_the_blast_radius_query_returns_a_real_maximum(control_engine) -> None:
    """Not merely 'does not raise' — it must compute the right number.

    A statement can execute and still be wrong. This seeds two audit rows, one
    inside the trailing window and one outside it, and pins that the window
    bound (the very parameter that was never bound) actually discriminates.
    """
    path = APP_ROOT / "services" / "deployment_service.py"
    sql = next(
        s
        for s in _statement_texts(path)
        if "tool_calls_audit" in s and "window_days" in s and "MAX(COALESCE" in s
    )
    agent_id = str(uuid.uuid4())

    with control_engine.begin() as conn:
        conn.execute(
            sa_text(
                "INSERT INTO tool_calls_audit (agent_id, skill, arguments, created_at) "
                "VALUES (:aid, 'issue_refund', CAST(:args AS jsonb), now() - INTERVAL '1 day')"
            ),
            {"aid": agent_id, "args": json.dumps({"refund_amount_cents": 4200})},
        )
        conn.execute(
            sa_text(
                "INSERT INTO tool_calls_audit (agent_id, skill, arguments, created_at) "
                "VALUES (:aid, 'issue_refund', CAST(:args AS jsonb), now() - INTERVAL '90 days')"
            ),
            {"aid": agent_id, "args": json.dumps({"refund_amount_cents": 999_999})},
        )
    try:
        with control_engine.connect() as conn:
            observed = conn.execute(
                sa_text(sql), {"agent_id": agent_id, "window_days": 7}
            ).scalar()
        assert observed == 4200, (
            f"expected the in-window 4200, got {observed}. 999999 is 90 days old, so "
            "if it appears here the window bound is not being applied — which is "
            "exactly what an unbound :window_days would look like if Postgres had "
            "accepted the statement instead of rejecting it."
        )
    finally:
        with control_engine.begin() as conn:
            conn.execute(
                sa_text("DELETE FROM tool_calls_audit WHERE agent_id = :aid"),
                {"aid": agent_id},
            )


def test_the_digest_insert_executes_against_postgres(control_engine) -> None:
    """The WR-02 idempotency anchor, actually inserted.

    This INSERT raised on every `run_weekly_digest` since OPS-04. Because it is
    committed BEFORE `send_digest_email` precisely so a failed send cannot
    double-send, the raise meant no digest_runs row was ever written and the
    send was never reached. digest_runs.agent_id is a FK to agents(id), so this
    seeds a tenant and an agent rather than using a bare uuid — an FK is a
    constraint statement compilation cannot see (the lesson from 1.13's
    "zero foreign keys" claim).
    """
    path = APP_ROOT / "worker" / "tasks" / "runtime" / "digest.py"
    sql = next(s for s in _statement_texts(path) if "digest_runs" in s and "INSERT" in s)

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    stats = {"conversations": 12, "escalations": 1}

    with control_engine.begin() as conn:
        conn.execute(
            sa_text(
                "INSERT INTO tenants (id, name, api_key_hash) "
                "VALUES (:id, 'paramstyle digest tenant', :h)"
            ),
            {"id": tenant_id, "h": f"paramstyle-{tenant_id}"},
        )
        conn.execute(
            sa_text(
                "INSERT INTO agents (id, tenant_id, name, soul, role) VALUES "
                "(:id, :tid, 'paramstyle digest agent', CAST('{}' AS JSONB), 'customer_service')"
            ),
            {"id": agent_id, "tid": tenant_id},
        )
    try:
        with control_engine.begin() as conn:
            conn.execute(sa_text(sql), {"agent_id": agent_id, "payload": json.dumps(stats)})

        with control_engine.connect() as conn:
            row = conn.execute(
                sa_text("SELECT payload FROM digest_runs WHERE agent_id = :aid"),
                {"aid": agent_id},
            ).fetchone()

        assert row is not None, "the digest_runs row was not written"
        assert row[0] == stats, (
            f"payload round-tripped as {row[0]!r}, expected {stats!r} — the jsonb cast "
            "must preserve the object, not store a quoted string"
        )
    finally:
        with control_engine.begin() as conn:
            conn.execute(sa_text("DELETE FROM digest_runs WHERE agent_id = :aid"), {"aid": agent_id})
            conn.execute(sa_text("DELETE FROM agents WHERE id = :aid"), {"aid": agent_id})
            conn.execute(sa_text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
