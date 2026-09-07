"""read_run_ledger against the LOCAL tenant probe database (#199).

    INTEGRATION_TESTS_ENABLED=1 .venv/Scripts/python.exe -m pytest \
        tests/integration/test_run_ledger_read.py -q

WHAT IT PROVES THAT THE UNIT TESTS CANNOT
    The unit tests hand read_run_ledger a recording cursor, so its SELECT is never
    issued. `model_calls.job_id` is TEXT (tenant migration 0019) and the SELECT
    compared it to a uuid, which PostgreSQL has no operator for, so on staging
    every eval run's cost read as unknown. Here one row goes in through the
    production writer and comes back through the production reader.
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


@pytest.fixture
def run_id():
    """A run id that exists only for this test; its rows are deleted afterwards."""
    value = str(uuid.uuid4())
    yield value
    conn = psycopg2.connect(PROBE_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM model_calls WHERE job_id = %s", (value,))
        conn.commit()
    finally:
        conn.close()


def test_a_judge_call_written_under_a_run_is_read_back_by_the_run_id(run_id):
    from app.core.model_client import record_model_call
    from app.domain.model_call import ModelCall
    from app.services.eval_service import read_run_ledger

    record_model_call(
        ModelCall(
            purpose="judge_faithfulness",
            provider="openai",
            requested_model="gpt-5.6-luna",
            served_model="gpt-5.6-luna",
            model_source="reported",
            input_tokens=120,
            output_tokens=30,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            at=datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc),
            tenant_id=str(uuid.uuid4()),
            agent_id=str(uuid.uuid4()),
            job_id=run_id,
        ),
        PROBE_DSN,
    )

    calls = read_run_ledger(run_id, PROBE_DSN)

    assert [c.purpose for c in calls] == ["judge_faithfulness"]
    assert calls[0].input_tokens == 120
    assert calls[0].job_id == run_id
