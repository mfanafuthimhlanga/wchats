"""Issue #79, the history tiebreak settled by a real PostgreSQL.

`tests/unit/test_agent_task.py` drives `_read_turn_history` against a fake cursor
that sorts by a key the test wrote itself. That proves the reversal arithmetic
and nothing about the server. The claim the tiebreak makes is a claim about a
QUERY PLAN: within one `transaction_timestamp()`, `created_at DESC` alone leaves
two rows in whatever order the scan produced, and the CASE is what settles them.
Only a server can be asked whether that is true.

So this module writes one turn the way `_persist_messages` writes it, the user
row and the assistant row in ONE transaction and therefore on one timestamp, then
reads it back through the production function.

THE INSERT ORDER IS THE WHOLE EXPERIMENT. The user row goes in FIRST, which is
what `_persist_messages` does, so the heap holds the question before the answer.
`created_at DESC` alone then preserves that order and the reversal hands the model
its own answer before the question it answered. Insert the assistant row first
instead and both queries return the same rows in the same order, and the test
passes over a query with no tiebreak in it, which is a tautology dressed as a guard.
`test_without_the_tiebreak_postgres_returns_the_turn_inside_out` is the control
that keeps that honest. It runs the same statement with the CASE removed, against
the same rows, and pins the wrong answer.

Requires INTEGRATION_TESTS_ENABLED=1 and a local PostgreSQL. The database is the
disposable tenant probe CLAUDE.md names, migrated by `run_tenant_migrations`.
"""

from __future__ import annotations

import os
import uuid

import psycopg2
import pytest

INTEGRATION_TESTS = os.environ.get("INTEGRATION_TESTS_ENABLED", "") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not INTEGRATION_TESTS,
        reason=(
            "INTEGRATION_TESTS_ENABLED=1 and a local PostgreSQL are required. The "
            "whole point of this module is that a real planner orders the rows"
        ),
    ),
]

#: The disposable local tenant database CLAUDE.md names, and the same env-var
#: override `_tenant_db.py` and `test_migrations.py` already read, so a machine
#: with non-default local credentials is configured from one place.
PROBE_DB_URL = os.getenv(
    "TEST_TENANT_PROBE_URL",
    os.getenv("TEST_LOCAL_BASE", "postgresql://wchats:wchats@localhost:5432")
    + "/wchats_tenant_probe",
)

QUESTION = "Do you deliver to Soweto?"
ANSWER = "Yes, within two working days."


@pytest.fixture
def probe_conn():
    """A psycopg2 connection to the probe database, migrated to head.

    `run_tenant_migrations` is the production path (CLAUDE.md), so the schema this
    test reads is the schema a tenant gets. It is idempotent, so a probe already
    at head costs one no-op upgrade.
    """
    from app.services.migrations import run_tenant_migrations

    run_tenant_migrations(PROBE_DB_URL)
    conn = psycopg2.connect(PROBE_DB_URL, connect_timeout=20)
    try:
        yield conn
    finally:
        conn.close()


def _one_turn(conn) -> str:
    """One conversation carrying one turn, both message rows in ONE transaction.

    The user row goes first, exactly as `_persist_messages` writes it, so the heap
    holds the question before the answer and `created_at DESC` alone would return
    them in that order. `NOW()` is `transaction_timestamp()`, so both rows carry
    the same instant and there is a tie for the CASE to settle.
    """
    conv_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO conversations (id, agent_id, created_at, metadata) "
            "VALUES (%s, %s, NOW(), %s::jsonb)",
            (conv_id, str(uuid.uuid4()), "{}"),
        )
        cur.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (%s, %s, 'user', %s, NOW())",
            (str(uuid.uuid4()), conv_id, QUESTION),
        )
        cur.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (%s, %s, 'assistant', %s, NOW())",
            (str(uuid.uuid4()), conv_id, ANSWER),
        )
    conn.commit()
    return conv_id


def _delete_turn(conn, conv_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM messages WHERE conversation_id = %s", (conv_id,))
        cur.execute("DELETE FROM conversations WHERE id = %s", (conv_id,))
    conn.commit()


def test_one_transaction_s_turn_comes_back_question_first(probe_conn) -> None:
    """The production reader against the production writer's row order."""
    from app.worker.tasks.runtime.agent import _read_turn_history

    conv_id = _one_turn(probe_conn)
    try:
        history = _read_turn_history(probe_conn, conv_id)
    finally:
        _delete_turn(probe_conn, conv_id)

    assert history == [
        {"role": "user", "content": QUESTION},
        {"role": "assistant", "content": ANSWER},
    ], (
        f"PostgreSQL returned the turn as {history}. Both rows share one "
        "transaction_timestamp(), so created_at alone cannot order them and the "
        "CASE tiebreak is what settles it (issue #79). Reversed, the model reads "
        "its own answer before the question it answered."
    )


def test_without_the_tiebreak_postgres_returns_the_turn_inside_out(probe_conn) -> None:
    """The control, so the test above is a guard rather than a tautology.

    Same rows, same LIMIT, the same reversal the reader applies, and the ORDER BY
    cut back to `created_at DESC`. If this came back question-first the heap order
    would be doing the work and dropping the tiebreak from the real query would
    cost nothing observable.
    """
    from app.worker.tasks.runtime.agent import TURN_HISTORY_MAX_MESSAGES

    conv_id = _one_turn(probe_conn)
    try:
        with probe_conn.cursor() as cur:
            cur.execute(
                """
                SELECT role, content
                FROM messages
                WHERE conversation_id = %s AND role IN ('user', 'assistant')
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (conv_id, TURN_HISTORY_MAX_MESSAGES),
            )
            rows = list(cur.fetchall())
    finally:
        _delete_turn(probe_conn, conv_id)

    rows.reverse()
    assert rows == [("assistant", ANSWER), ("user", QUESTION)], (
        f"a tiebreak-less ORDER BY returned {rows}, which is the RIGHT order. "
        "Then the heap is settling the tie by itself and the test above passes "
        "with or without the CASE, which makes it a tautology rather than a "
        "guard. Check the insert order, because the user row has to go in first."
    )
