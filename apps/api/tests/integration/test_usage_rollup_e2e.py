"""rollup_model_calls end to end, against the two LOCAL databases (ticket #46, issue #22).

    INTEGRATION_TESTS_ENABLED=1 .venv/Scripts/python.exe -m pytest \
        tests/integration/test_usage_rollup_e2e.py -q -s

WHAT IT PROVES THAT THE UNIT TESTS CANNOT
    The unit tests patch every database seam, so the SQL itself is never issued.
    Here the ledger rows are written by the production writer
    (`record_model_call`), read back by the task's own SELECT, priced through the
    pure functions, and upserted by the task's own ON CONFLICT statement into the
    real control table. Then the task runs a second time and the rows are compared
    to the first run's, which is the idempotency claim actually observed rather
    than asserted.

WHICH DATABASES
    The tenant ledger is `wchats_tenant_probe` and the control table is
    `wchats_control`, both on the local disposable cluster documented in CLAUDE.md.
    `CONTROL_DB_URL` in `.env` points at live Neon production and is never read
    here: tests/integration/conftest.py overrides both control URLs to the local
    cluster before any app module is imported.

WHY THE FAN-OUT IS NARROWED TO THE SEEDED TENANT
    The local control DB already carries agent rows whose connection strings name
    real Neon projects. The real `tenant_dsn_ciphertexts` query runs and is
    asserted on, and the task is then handed only the seeded tenant's entry, so no
    foreign connection string is ever opened by a test.

TEARDOWN
    Every row this test writes is deleted in a finally block: the ledger rows and
    the usage rows by tenant id, the agent and the tenant by id.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg2
import pytest

# A key of this test's own, set before any app module is imported. It is what
# makes the pre-existing agent rows in the local control DB undecryptable here,
# and it never touches the encrypted columns those rows already hold.
os.environ["NEON_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(os.urandom(32)).decode()

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS_ENABLED", "") != "1",
    reason="INTEGRATION_TESTS_ENABLED=1 required: this test writes to the local cluster",
)

PROBE_DSN = os.getenv(
    "TEST_TENANT_PROBE_DSN", "postgresql://wchats:wchats@localhost:5432/wchats_tenant_probe"
)
CONTROL_DSN = os.getenv(
    "TEST_CONTROL_DSN", "postgresql://wchats:wchats@localhost:5432/wchats_control"
)

#: 2026-08-25 is a Tuesday. 08:00 UTC is 10:00 CAT, inside the second peak window,
#: and 18:00 UTC is 20:00 CAT, off peak.
DAY = "2026-08-25"
PEAK = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
OFF_PEAK = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
#: The next day. Its call must not reach the row for DAY.
NEXT_DAY = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)

MILLION = 1_000_000

USAGE_COLUMNS = (
    "purpose, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
    "call_count, cost_usd, cost_zar, price_version, fx_version"
)


def seed_ledger(tenant_id: str) -> None:
    """Four calls through the production writer: three on DAY, one on the next day."""
    from app.core.model_client import record_model_call
    from app.domain.model_call import ModelCall

    def call(purpose, at, **tokens):
        return ModelCall(
            purpose=purpose,
            provider="deepseek",
            requested_model="claude-haiku-4-5",
            served_model="deepseek-v4-flash",
            model_source="mapped_by_docs",
            input_tokens=tokens.get("input_tokens", 0),
            output_tokens=tokens.get("output_tokens", 0),
            cache_read_tokens=tokens.get("cache_read_tokens", 0),
            cache_creation_tokens=tokens.get("cache_creation_tokens", 0),
            at=at,
            tenant_id=tenant_id,
        )

    for one in (
        call("judge", PEAK, input_tokens=MILLION),
        call("judge", OFF_PEAK, output_tokens=MILLION),
        call("agent_turn", PEAK, cache_read_tokens=2 * MILLION),
        call("judge", NEXT_DAY, input_tokens=5 * MILLION),
    ):
        record_model_call(one, PROBE_DSN)


def seed_control(tenant_id: str, agent_id: str) -> None:
    """A tenant and an agent whose connection string names the probe database."""
    from app.core.security import fernet_encrypt

    conn = psycopg2.connect(CONTROL_DSN, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants (id, name, api_key_hash) VALUES (%s, %s, %s)",
                (tenant_id, "usage rollup e2e", f"hash-{tenant_id}"),
            )
            cur.execute(
                "INSERT INTO agents (id, tenant_id, name, soul, role, neon_connection_string) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    agent_id,
                    tenant_id,
                    "usage rollup e2e agent",
                    # soul is jsonb. The rollup never reads it; the column is NOT NULL.
                    "{}",
                    "support",
                    fernet_encrypt(PROBE_DSN),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def read_usage(tenant_id: str) -> list[tuple]:
    """Every tenant_usage_daily row this tenant has, ordered so two runs compare."""
    conn = psycopg2.connect(CONTROL_DSN, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {USAGE_COLUMNS} FROM tenant_usage_daily "
                "WHERE tenant_id = %s ORDER BY purpose",
                (tenant_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def clean_up(tenant_id: str, agent_id: str) -> None:
    for dsn, statements in (
        (PROBE_DSN, ["DELETE FROM model_calls WHERE tenant_id = %s"]),
        (
            CONTROL_DSN,
            [
                "DELETE FROM tenant_usage_daily WHERE tenant_id = %s",
                "DELETE FROM agents WHERE tenant_id = %s",
                "DELETE FROM tenants WHERE id = %s",
            ],
        ),
    ):
        conn = psycopg2.connect(dsn, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement, (tenant_id,))
            conn.commit()
        finally:
            conn.close()
    assert agent_id  # deleted by tenant_id above; named so the caller reads it


def test_a_day_of_ledger_rows_becomes_priced_usage_rows_and_a_re_run_changes_nothing():
    """Seed, roll up, check the money, roll up again, check nothing moved."""
    from unittest.mock import patch

    from app.core.database import get_sync_db
    from app.worker.tasks.runtime import usage as task_module

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    try:
        seed_control(tenant_id, agent_id)
        seed_ledger(tenant_id)

        # The real query, against the real control database.
        with get_sync_db() as db:
            found = task_module.tenant_dsn_ciphertexts(db)
        assert tenant_id in found, "the seeded tenant's connection string was not found"
        assert len(found[tenant_id]) == 1

        seeded_only = {tenant_id: found[tenant_id]}
        with patch.object(task_module, "tenant_dsn_ciphertexts", return_value=seeded_only):
            first_summary = task_module.rollup_model_calls(day=DAY)
            first = read_usage(tenant_id)
            second_summary = task_module.rollup_model_calls(day=DAY)
            second = read_usage(tenant_id)

        print("\nrun 1 summary:", first_summary)
        for row in first:
            print("  run 1:", row)
        print("run 2 summary:", second_summary)
        for row in second:
            print("  run 2:", row)

        # Two purposes on DAY. The next day's five million input tokens are absent.
        assert [row[0] for row in first] == ["agent_turn", "judge"]

        agent_turn, judge = first
        # Two million cache reads at $0.088 per million is $0.176, and 0.176 at
        # 16.0237 rand per dollar is 2.8201712.
        assert agent_turn[1:6] == (0, 0, 2 * MILLION, 0, 1)
        assert agent_turn[6] == Decimal("0.176")
        assert agent_turn[7] == Decimal("2.8201712")
        # A million peak input at $0.44 plus a million off-peak output at $0.264.
        assert judge[1:6] == (MILLION, MILLION, 0, 0, 2)
        assert judge[6] == Decimal("0.704")
        assert judge[7] == Decimal("11.2806848")
        for row in first:
            assert row[8] == "2026-08-23.1"
            assert row[9] == "usd_zar-2026-08-24"

        assert first_summary == {
            "day": DAY,
            "tenants_done": 1,
            "tenants_skipped": 0,
            "rows_written": 2,
        }
        assert second_summary == first_summary
        assert second == first, "the second run changed the rows the first run wrote"
    finally:
        clean_up(tenant_id, agent_id)


def test_the_day_the_task_is_asked_for_is_the_only_day_it_prices():
    """The next day's call is in the same table and must not reach DAY's row."""
    from unittest.mock import patch

    from app.core.database import get_sync_db
    from app.worker.tasks.runtime import usage as task_module

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    try:
        seed_control(tenant_id, agent_id)
        seed_ledger(tenant_id)
        with get_sync_db() as db:
            seeded_only = {tenant_id: task_module.tenant_dsn_ciphertexts(db)[tenant_id]}
        with patch.object(task_module, "tenant_dsn_ciphertexts", return_value=seeded_only):
            task_module.rollup_model_calls(day="2026-08-26")

        conn = psycopg2.connect(CONTROL_DSN, connect_timeout=10)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT day, purpose, input_tokens, cost_usd FROM tenant_usage_daily "
                    "WHERE tenant_id = %s",
                    (tenant_id,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        print("\n2026-08-26 rows:", rows)
        assert rows == [(date(2026, 8, 26), "judge", 5 * MILLION, Decimal("2.2"))]
    finally:
        clean_up(tenant_id, agent_id)
