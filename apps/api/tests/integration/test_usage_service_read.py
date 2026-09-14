"""read_agent_ledger against the LOCAL tenant probe database.

    INTEGRATION_TESTS_ENABLED=1 .venv/Scripts/python.exe -m pytest \
        tests/integration/test_usage_service_read.py -q

WHAT IT PROVES THAT THE UNIT TESTS CANNOT
    The unit tests hand read_agent_ledger a recording cursor, so its SELECT is
    never parsed by PostgreSQL and its join is never executed. Two facts only a
    real server can settle:

    `ix_turn_metrics_job_id` is not unique. A turn written twice, by a retry or
    by two workers, leaves two `turn_metrics` rows under one `job_id`. Under a
    plain LEFT JOIN every ledger row of that turn comes back once per matching
    row, and the turn's money doubles. The LATERAL subquery with LIMIT 1 takes
    one conversation and leaves the row count the ledger's own, which is what
    the duplicate row seeded below is here to catch.

    The window bind is a timestamptz, not the `(n || ' days')::interval` string
    the first draft built. A comparison PostgreSQL has no operator for fails at
    execute time and nowhere else.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg2
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS_ENABLED", "") != "1",
    reason="INTEGRATION_TESTS_ENABLED=1 required: this test writes to the local cluster",
)

PROBE_DSN = os.getenv(
    "TEST_TENANT_PROBE_DSN", "postgresql://wchats:wchats@localhost:5432/wchats_tenant_probe"
)

#: The read is measured from a fixed instant so the seeded rows sit in the window
#: whatever day the suite runs. A 7 day window opened from here starts at
#: 2026-09-07 22:00 UTC, which is 00:00 CAT on the 8th.
NOW = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)
IN_WINDOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
BEFORE_WINDOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def seeded():
    """One turn's three calls, one eval call, one call older than the window.

    The turn has TWO `turn_metrics` rows under its single `job_id`, which is what
    a retried write leaves behind. Everything seeded here is deleted afterwards,
    keyed on the agent id, which exists only for this test.
    """
    agent_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    turn_job = str(uuid.uuid4())
    eval_job = str(uuid.uuid4())

    from app.core.model_client import record_model_call
    from app.domain.model_call import ModelCall

    def ledger_row(purpose, job_id, at):
        record_model_call(
            ModelCall(
                purpose=purpose,
                provider="openai",
                requested_model="gpt-5.6-luna",
                served_model="gpt-5.6-luna",
                model_source="reported",
                input_tokens=1000,
                output_tokens=100,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                at=at,
                tenant_id=tenant_id,
                agent_id=agent_id,
                job_id=job_id,
            ),
            PROBE_DSN,
        )

    ledger_row("agent_turn", turn_job, IN_WINDOW)
    ledger_row("gatekeeper", turn_job, IN_WINDOW)
    ledger_row("auditor", turn_job, IN_WINDOW)
    ledger_row("judge_faithfulness", eval_job, IN_WINDOW)
    ledger_row("agent_turn", turn_job, BEFORE_WINDOW)

    conn = psycopg2.connect(PROBE_DSN)
    try:
        with conn.cursor() as cur:
            for _ in range(2):
                cur.execute(
                    "INSERT INTO turn_metrics (job_id, conversation_id, agent_id, latency_ms)"
                    " VALUES (%s, %s, %s, %s)",
                    (turn_job, conversation_id, agent_id, 1200),
                )
        conn.commit()
    finally:
        conn.close()

    yield {
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "turn_job": turn_job,
        "eval_job": eval_job,
    }

    conn = psycopg2.connect(PROBE_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM model_calls WHERE agent_id = %s", (agent_id,))
            cur.execute("DELETE FROM turn_metrics WHERE agent_id = %s::uuid", (agent_id,))
        conn.commit()
    finally:
        conn.close()


def test_a_duplicate_turn_metrics_row_does_not_duplicate_the_ledger(seeded):
    from app.services.usage_service import read_agent_ledger

    rows = read_agent_ledger(PROBE_DSN, seeded["agent_id"], 7, now=NOW)

    assert len(rows) == 4
    assert sorted(row.call.purpose for row in rows) == [
        "agent_turn",
        "auditor",
        "gatekeeper",
        "judge_faithfulness",
    ]


def test_a_turns_calls_carry_its_conversation_and_an_eval_call_carries_none(seeded):
    from app.services.usage_service import read_agent_ledger

    rows = read_agent_ledger(PROBE_DSN, seeded["agent_id"], 7, now=NOW)

    by_job = {}
    for row in rows:
        by_job.setdefault(row.call.job_id, set()).add(row.conversation_id)
    assert by_job[seeded["turn_job"]] == {seeded["conversation_id"]}
    assert by_job[seeded["eval_job"]] == {None}


def test_a_call_older_than_the_window_is_left_out(seeded):
    """The window bind is a real timestamptz comparison, executed by the server."""
    from app.services.usage_service import read_agent_ledger

    wide = read_agent_ledger(PROBE_DSN, seeded["agent_id"], 30, now=NOW)
    narrow = read_agent_ledger(PROBE_DSN, seeded["agent_id"], 7, now=NOW)

    assert len(wide) == 5
    assert len(narrow) == 4
