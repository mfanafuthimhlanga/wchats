"""usage_service: the whole cost of a turn, priced from the ledger at read time.

The claim under test is the one `.dev/reference/260914-unit-economics.md` measured
on staging: `turn_metrics.cost_usd` is the agent loop alone, and the judges that
run after it are most of the money. `summarise_usage` is pure, so every figure
here is asserted against rows built by hand and priced by the seeded book.

EVERY EXPECTED FIGURE IS COMPUTED A SECOND WAY. The money assertions call
`app.domain.pricing.cost_usd` and `cost_zar` over the same rows, or state the
arithmetic as a literal. An assertion that reads a figure back out of the output
it is checking passes whatever the code does, and this file had five of those.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.core.model_client import _COLUMNS as LEDGER_COLUMNS
from app.domain.model_call import ModelCall
from app.domain.pricing import cost_usd, cost_zar
from app.services import usage_service
from app.services.usage_service import (
    LedgerRow,
    read_agent_ledger,
    summarise_usage,
    window_start,
)

AGENT = "0f5f1f0e-1d1f-4b8e-9c2a-000000000001"
TENANT = "0f5f1f0e-1d1f-4b8e-9c2a-000000000002"
TURN_A = "0f5f1f0e-1d1f-4b8e-9c2a-00000000000a"
TURN_B = "0f5f1f0e-1d1f-4b8e-9c2a-00000000000b"
EVAL_RUN = "0f5f1f0e-1d1f-4b8e-9c2a-00000000000e"
CONV_1 = "conv-1"
CONV_2 = "conv-2"
#: 23:30 UTC is already the next CAT day (CAT is UTC+2).
LATE_UTC = datetime(2026, 9, 13, 23, 30, tzinfo=timezone.utc)
MIDDAY_UTC = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
#: The only fx row is published 2026-08-24, so an August call has dollars and no rand.
BEFORE_FX_UTC = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
#: What the seeded book stamps on every row it prices.
BOOK_VERSION = "2026-08-23.1"


def call(
    purpose: str,
    *,
    job_id: str | None,
    at: datetime = MIDDAY_UTC,
    served_model: str = "gpt-5.6-luna",
    provider: str = "openai",
    input_tokens: int = 1000,
    output_tokens: int = 100,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> ModelCall:
    return ModelCall(
        purpose=purpose,
        provider=provider,
        requested_model=served_model,
        served_model=served_model,
        model_source="reported",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        at=at,
        tenant_id=TENANT,
        agent_id=AGENT,
        job_id=job_id,
    )


def turn(
    job_id: str,
    conversation_id: str,
    at: datetime = MIDDAY_UTC,
    input_tokens: int = 1000,
    output_tokens: int = 100,
) -> list[LedgerRow]:
    """One turn as the ledger holds it: the agent loop and its three judges."""
    return [
        LedgerRow(
            call(p, job_id=job_id, at=at, input_tokens=input_tokens, output_tokens=output_tokens),
            conversation_id,
        )
        for p in ("agent_turn", "agent_turn", "gatekeeper", "auditor", "strategist")
    ]


def usd(rows: list[LedgerRow]) -> float:
    """The dollars these rows cost, priced call by call through the price book."""
    return float(sum((cost_usd(r.call)[0] for r in rows), Decimal(0)))


def zar(rows: list[LedgerRow]) -> float:
    """The rand these rows cost, priced call by call through the book and fx table."""
    return float(sum((cost_zar(r.call)[0] for r in rows), Decimal(0)))


class TestTurnsIncludeTheJudges:
    def test_cost_per_turn_is_the_whole_chain_not_the_agent_loop(self):
        rows = turn(TURN_A, CONV_1)
        agent_only = usd([r for r in rows if r.call.purpose == "agent_turn"])

        out = summarise_usage(rows, window_days=7)

        assert out["turns"]["count"] == 1
        assert out["turns"]["conversations"] == 1
        assert out["turns"]["cost_per_turn_usd"] == pytest.approx(usd(rows))
        assert out["turns"]["cost_per_turn_usd"] > agent_only
        assert out["turns"]["calls"] == 5

    def test_an_eval_run_counts_in_total_and_purpose_but_not_in_turns(self):
        rows = turn(TURN_A, CONV_1) + [LedgerRow(call("judge_faithfulness", job_id=EVAL_RUN), None)]

        out = summarise_usage(rows, window_days=7)

        assert out["total"]["calls"] == 6
        assert out["turns"]["calls"] == 5
        assert out["turns"]["cost_usd"] < out["total"]["cost_usd"]
        assert {p["purpose"] for p in out["by_purpose"]} == {
            "agent_turn", "gatekeeper", "auditor", "strategist", "judge_faithfulness"
        }

    def test_a_conversation_row_carries_the_money_its_five_calls_cost(self):
        """The whole by_conversation row, against figures priced a second way."""
        rows = turn(TURN_A, CONV_1) + [LedgerRow(call("judge_faithfulness", job_id=EVAL_RUN), None)]

        out = summarise_usage(rows, window_days=7)

        assert out["by_conversation"] == [
            {
                "conversation_id": CONV_1,
                "calls": 5,
                "turns": 1,
                "cost_usd": pytest.approx(usd(rows[:5])),
                "cost_zar": pytest.approx(zar(rows[:5])),
                "unpriced_calls": 0,
                "unrated_calls": 0,
                "price_gaps": [],
            }
        ]

    def test_the_window_names_the_book_version_every_row_priced_against(self):
        out = summarise_usage(turn(TURN_A, CONV_1), window_days=7)

        assert out["price_versions"] == [BOOK_VERSION]


class TestTheTokenCounts:
    def test_by_purpose_reports_all_four_counts_per_purpose(self):
        """Eight distinct numbers, so swapping any two lines turns this red."""
        rows = [
            LedgerRow(
                call(
                    "agent_turn", job_id=TURN_A,
                    input_tokens=1100, output_tokens=210,
                    cache_read_tokens=320, cache_creation_tokens=43,
                ),
                CONV_1,
            ),
            LedgerRow(
                call(
                    "auditor", job_id=TURN_A,
                    input_tokens=1500, output_tokens=260,
                    cache_read_tokens=370, cache_creation_tokens=48,
                ),
                CONV_1,
            ),
        ]

        out = summarise_usage(rows, window_days=7)

        purposes = {p["purpose"]: p for p in out["by_purpose"]}
        assert purposes["agent_turn"]["input_tokens"] == 1100
        assert purposes["agent_turn"]["output_tokens"] == 210
        assert purposes["agent_turn"]["cache_read_tokens"] == 320
        assert purposes["agent_turn"]["cache_creation_tokens"] == 43
        assert purposes["auditor"]["input_tokens"] == 1500
        assert purposes["auditor"]["output_tokens"] == 260
        assert purposes["auditor"]["cache_read_tokens"] == 370
        assert purposes["auditor"]["cache_creation_tokens"] == 48


class TestCostPerTurn:
    def test_two_turns_of_different_sizes_average_to_the_stated_quotient(self):
        """The quotient is arithmetic on the tariff, not a reading of the output.

        Turn A: five calls of 1000 in and 100 out. Turn B: five of 2000 and 200.
        At $0.20 and $1.20 per million that is 5 x $0.00032 plus 5 x $0.00064,
        which is $0.0048 over two turns, so $0.0024 each.
        """
        rows = turn(TURN_A, CONV_1) + turn(
            TURN_B, CONV_2, input_tokens=2000, output_tokens=200
        )

        out = summarise_usage(rows, window_days=7)

        assert out["turns"]["count"] == 2
        assert out["turns"]["cost_usd"] == pytest.approx(0.0048)
        assert out["turns"]["cost_per_turn_usd"] == pytest.approx(0.0024)


class TestUnknownIsNull:
    def test_an_unpriced_call_nulls_its_group_and_is_named(self):
        rows = turn(TURN_A, CONV_1) + turn(TURN_B, CONV_2)
        rows[5] = LedgerRow(call("agent_turn", job_id=TURN_B, served_model="mystery-9"), CONV_2)

        out = summarise_usage(rows, window_days=7)

        assert out["total"]["cost_usd"] is None
        assert out["total"]["unpriced_calls"] == 1
        assert out["total"]["price_gaps"] == [
            {"provider": "openai", "served_model": "mystery-9", "call_count": 1}
        ]
        assert out["turns"]["cost_per_turn_usd"] is None
        by_conv = {c["conversation_id"]: c for c in out["by_conversation"]}
        assert by_conv[CONV_1]["cost_usd"] == pytest.approx(usd(rows[:5]))
        assert by_conv[CONV_2]["cost_usd"] is None
        assert by_conv[CONV_2]["unpriced_calls"] == 1
        purposes = {p["purpose"]: p for p in out["by_purpose"]}
        assert purposes["agent_turn"]["cost_usd"] is None
        assert purposes["auditor"]["cost_usd"] is not None

    def test_a_call_older_than_the_fx_table_keeps_its_dollars_and_loses_its_rand(self):
        """The two currencies fail separately, and each failure has its own count."""
        rows = [LedgerRow(call("agent_turn", job_id=TURN_A, at=BEFORE_FX_UTC), CONV_1)]

        out = summarise_usage(rows, window_days=90)

        assert out["total"]["cost_usd"] == pytest.approx(usd(rows))
        assert out["total"]["cost_zar"] is None
        assert out["total"]["unrated_calls"] == 1
        assert out["total"]["unpriced_calls"] == 0
        assert out["total"]["price_gaps"] == []

    def test_zero_calls_is_none_not_zero(self):
        """No rows means nothing was recorded, which is unknown money, not free."""
        out = summarise_usage([], window_days=7)

        assert out["turns"] == {
            "calls": 0,
            "cost_usd": None,
            "cost_zar": None,
            "unpriced_calls": 0,
            "unrated_calls": 0,
            "price_gaps": [],
            "count": 0,
            "conversations": 0,
            "cost_per_turn_usd": None,
        }
        assert out["total"]["cost_usd"] is None
        assert out["total"]["cost_zar"] is None
        assert out["by_day"] == [] and out["by_conversation"] == []
        assert out["price_versions"] == []


class TestDaysAreCat:
    def test_a_late_utc_call_lands_on_the_next_cat_day(self):
        rows = turn(TURN_A, CONV_1, at=MIDDAY_UTC) + turn(TURN_B, CONV_2, at=LATE_UTC)

        out = summarise_usage(rows, window_days=7)

        assert [(d["day"], d["turns"], d["calls"]) for d in out["by_day"]] == [
            ("2026-09-13", 1, 5),
            ("2026-09-14", 1, 5),
        ]
        assert sum(d["cost_usd"] for d in out["by_day"]) == pytest.approx(usd(rows))

    def test_two_turns_in_one_conversation_on_one_day(self):
        rows = turn(TURN_A, CONV_1) + turn(TURN_B, CONV_1, at=MIDDAY_UTC + timedelta(hours=1))

        out = summarise_usage(rows, window_days=30)

        assert out["window_days"] == 30
        assert out["turns"]["count"] == 2
        assert out["turns"]["conversations"] == 1
        assert out["by_day"][0]["turns"] == 2
        assert out["by_conversation"][0]["turns"] == 2


class TestByConversationIsARanking:
    def _one_call_conversations(self, count: int) -> list[LedgerRow]:
        """`count` conversations of one call each, every one a different price."""
        return [
            LedgerRow(
                call("agent_turn", job_id=f"job-{n:03d}", output_tokens=100 + n),
                f"conv-{n:03d}",
            )
            for n in range(count)
        ]

    def test_the_costliest_fifty_survive_the_cap_in_descending_order(self):
        rows = self._one_call_conversations(55)

        out = summarise_usage(rows, window_days=7)

        listed = out["by_conversation"]
        assert len(listed) == 50
        assert [row["conversation_id"] for row in listed] == [
            f"conv-{n:03d}" for n in range(54, 4, -1)
        ]
        costs = [row["cost_usd"] for row in listed]
        assert costs == sorted(costs, reverse=True)

    def test_the_turn_count_says_how_many_conversations_the_cap_left_out(self):
        rows = self._one_call_conversations(55)

        out = summarise_usage(rows, window_days=7)

        assert out["turns"]["conversations"] == 55
        assert out["turns"]["calls"] == 55
        assert out["turns"]["cost_usd"] == pytest.approx(usd(rows))

    def test_an_unpriced_conversation_sorts_below_every_priced_one(self):
        rows = self._one_call_conversations(3)
        rows.append(
            LedgerRow(call("agent_turn", job_id="job-mystery", served_model="mystery-9"), "conv-zzz")
        )

        out = summarise_usage(rows, window_days=7)

        assert [row["conversation_id"] for row in out["by_conversation"]][-1] == "conv-zzz"
        assert out["by_conversation"][-1]["cost_usd"] is None


class TestTheWindowOpensAtCatMidnight:
    def test_seven_days_back_from_a_september_morning(self):
        """A 7 day window covers today and six whole CAT days before it."""
        start = window_start(7, now=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc))

        assert start == datetime(2026, 9, 7, 22, 0, tzinfo=timezone.utc)

    def test_one_day_opens_at_the_start_of_today_in_cat(self):
        start = window_start(1, now=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc))

        assert start == datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc)

    def test_an_instant_before_cat_midnight_is_still_the_previous_cat_day(self):
        """21:00 UTC on the 13th is 23:00 CAT on the 13th, so today is the 13th."""
        start = window_start(7, now=datetime(2026, 9, 13, 21, 0, tzinfo=timezone.utc))

        assert start == datetime(2026, 9, 6, 22, 0, tzinfo=timezone.utc)


class TestReadAgentLedger:
    def test_rows_map_to_ledger_columns_and_conversation(self):
        row = (
            "auditor", "openai", "gpt-5.6-luna", "gpt-5.6-luna", "reported",
            1309, 202, 0, 0, MIDDAY_UTC, TENANT, AGENT, TURN_A, CONV_1,
        )
        cursor = MagicMock()
        cursor.fetchall.return_value = [row, row[:-1] + (None,)]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        now = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)

        with patch.object(usage_service.psycopg2, "connect", return_value=conn):
            rows = read_agent_ledger("postgresql://fake", AGENT, 7, now=now)

        assert [r.conversation_id for r in rows] == [CONV_1, None]
        assert rows[0].call.purpose == "auditor" and rows[0].call.job_id == TURN_A
        _, params = cursor.execute.call_args.args
        assert params == {
            "agent_id": AGENT,
            "start": datetime(2026, 9, 7, 22, 0, tzinfo=timezone.utc),
        }
        conn.close.assert_called_once()

    def test_the_select_takes_the_conversation_from_a_lateral_subquery(self):
        """A plain LEFT JOIN doubles a job's money when turn_metrics has two rows.

        `ix_turn_metrics_job_id` is not unique, so the join has to take one
        conversation rather than one row per match.
        """
        sql = usage_service._SELECT_AGENT_LEDGER_SQL
        columns = ", ".join(f"mc.{column}" for column in LEDGER_COLUMNS)

        assert sql.startswith(f"SELECT {columns}, tm.conversation_id FROM model_calls mc")
        assert "LEFT JOIN LATERAL" in sql
        assert "LIMIT 1" in sql
        assert "mc.agent_id = %(agent_id)s" in sql
        assert "mc.at >= %(start)s" in sql
        assert "ORDER BY" not in sql
