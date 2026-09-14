"""What an Agent's model calls cost, priced from the ledger at read time.

`turn_metrics.cost_usd` is written when the agent task ends, and the three
validators and the sampled faithfulness judge run on a Celery chain after that
write. So the stored figure is the agent loop alone; on staging it was 38% of
what five turns cost (`.dev/reference/260914-unit-economics.md`). The whole
figure exists in one place, the tenant's `model_calls` table, where every call a
turn made carries the turn's `job_id`. This module reads that table and prices
it, so the judges are in the number by construction and a corrected price book
re-prices history for free.

THE GRAINS
    per purpose, per CAT day, per conversation, and the turns as a whole. A turn
    is a `turn_metrics` row; its calls are the ledger rows with its `job_id`.
    Ledger rows whose job is not a turn (an eval run, a red-team run, a draft)
    count in the purpose and day figures and in nothing per turn.

UNKNOWN IS NULL, NEVER ZERO
    `roll_up` refuses to price a model the book does not know and reports the
    gap by name. A group with any unpriced call has a NULL cost and a non-zero
    `unpriced_calls`, so a reader sees tokens spent for no recorded money
    instead of a cheaper day. A group with no calls at all is NULL for the same
    reason: recording is fail open, so a group nothing was written for is not a
    group that cost nothing.

Rung: the service reads a tenant database through psycopg2 and hands rows to
`app.domain.usage_rollup`. The route decrypts the connection string; it arrives
here and is never logged.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence, cast

import psycopg2

from app.core.config import settings

# Imported rather than copied for the reason app/services/eval_service.py gives
# near line 2480: a third copy of this column list is a third thing to keep in
# step with the writer.
from app.core.model_client import _COLUMNS as LEDGER_COLUMNS
from app.domain.model_call import ModelCall
from app.domain.pricing import CAT
from app.domain.usage_rollup import PurposeUsage, roll_up

#: One ledger row and the conversation its job served, or None for a job that
#: was not a turn. The join is LATERAL with LIMIT 1 rather than a plain LEFT
#: JOIN because `ix_turn_metrics_job_id` is not unique: two `turn_metrics` rows
#: under one `job_id` would return every ledger row of that job twice and double
#: its money. The subquery takes one conversation, and the row count stays the
#: ledger's own.
_SELECT_AGENT_LEDGER_SQL = (
    "SELECT " + ", ".join(f"mc.{c}" for c in LEDGER_COLUMNS) + ", tm.conversation_id"
    " FROM model_calls mc"
    " LEFT JOIN LATERAL ("
    "   SELECT conversation_id FROM turn_metrics WHERE job_id = mc.job_id LIMIT 1"
    " ) tm ON true"
    " WHERE mc.agent_id = %(agent_id)s"
    "   AND mc.at >= %(start)s"
)

#: How many conversations `by_conversation` carries. The costliest are the ones
#: a reader acts on, and `turns.conversations` says how many the cap left out.
_CONVERSATION_LIMIT = 50


@dataclass(frozen=True)
class LedgerRow:
    """A priced call and, when its job was a turn, the conversation it served."""

    call: ModelCall
    conversation_id: str | None


def window_start(window_days: int, now: datetime | None = None) -> datetime:
    """The UTC instant the window opens, which is always a CAT midnight.

    The window counts back whole CAT calendar days from today's CAT date and
    includes today, so a 7 day window opens at 00:00 CAT six days ago and every
    `by_day` bucket holds a whole day. An interval measured back from the
    instant of the read would leave the oldest bucket a part day, and a part day
    reads as a quiet day rather than a clipped one.

    Args:
        window_days: how many CAT days the window covers, today included.
        now: the instant the window is measured from. Injected so a test does
             not depend on the wall clock.
    """
    today = (now or datetime.now(timezone.utc)).astimezone(CAT).date()
    opens = today - timedelta(days=window_days - 1)
    return datetime(opens.year, opens.month, opens.day, tzinfo=CAT).astimezone(timezone.utc)


def read_agent_ledger(
    conn_str: str, agent_id: str, window_days: int, now: datetime | None = None
) -> list[LedgerRow]:
    """The Agent's ledger rows for the window, each tagged with its conversation."""
    start = window_start(window_days, now)
    conn = psycopg2.connect(conn_str, connect_timeout=settings.TENANT_DB_CONNECT_TIMEOUT_S)
    try:
        with conn.cursor() as cur:
            cur.execute(_SELECT_AGENT_LEDGER_SQL, {"agent_id": agent_id, "start": start})
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        LedgerRow(
            call=ModelCall(**dict(zip(LEDGER_COLUMNS, row[:-1], strict=True))),
            conversation_id=str(row[-1]) if row[-1] is not None else None,
        )
        for row in rows
    ]


def _gap_rows(usage: Sequence[PurposeUsage]) -> list[dict]:
    """The (provider, model) pairs the book refused, merged across the group.

    One model can be refused under several purposes. A reader wants it named
    once with the whole count, because the fix is one row in the price book.
    """
    counted: Counter = Counter()
    for row in usage:
        for gap in row.price_gaps:
            counted[(gap.provider, gap.served_model)] += gap.call_count
    return [
        {"provider": provider, "served_model": model, "call_count": count}
        for (provider, model), count in sorted(counted.items())
    ]


def _money(usage: Sequence[PurposeUsage]) -> dict:
    """Sum a group's purpose rows into one figure, NULL if any purpose is NULL.

    Three things put a cost at NULL and each carries its own counter. A model
    the book refuses nulls both currencies and lands in `unpriced_calls` and
    `price_gaps`. A CAT date older than every fx row nulls the rand alone and
    lands in `unrated_calls`. A group with no calls nulls both, because the
    ledger hook is fail open and nothing recorded is not nothing spent.
    """
    calls = sum(row.call_count for row in usage)
    unpriced = sum(gap.call_count for row in usage for gap in row.price_gaps)
    unrated = sum(row.unrated_call_count for row in usage)
    priced = bool(calls) and not unpriced
    # The casts carry the guard: `priced` is false unless every row priced, and
    # `roll_up` nulls a row's money only when it reports the gap that nulled it.
    # A `row.cost_usd or Decimal(0)` here would swallow a rollup that nulled a
    # cost without counting it, which is the one bug this module exists to stop.
    usd = sum((cast(Decimal, row.cost_usd) for row in usage), Decimal(0)) if priced else None
    zar = (
        sum((cast(Decimal, row.cost_zar) for row in usage), Decimal(0))
        if priced and not unrated
        else None
    )
    return {
        "calls": calls,
        "cost_usd": None if usd is None else float(usd),
        "cost_zar": None if zar is None else float(zar),
        "unpriced_calls": unpriced,
        "unrated_calls": unrated,
        "price_gaps": _gap_rows(usage),
    }


def _purpose_rows(usage: Sequence[PurposeUsage]) -> list[dict]:
    """One row per purpose, carrying all four token counts.

    The cache counts sit beside the fresh ones because they price differently: a
    DeepSeek cache read costs a fifth of a fresh input token, so a purpose whose
    input is mostly cached is cheap for a reason a reader can act on.
    """
    return [
        {
            "purpose": row.purpose,
            "calls": row.call_count,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "cache_read_tokens": row.cache_read_tokens,
            "cache_creation_tokens": row.cache_creation_tokens,
            "cost_usd": None if row.cost_usd is None else float(row.cost_usd),
        }
        for row in usage
    ]


def _grouped(rows: Sequence[LedgerRow], key) -> list[tuple]:
    """`(key, figures)` per group, in key order, each group priced by `roll_up`.

    `turns` is the number of distinct turn jobs in the group, so a day row says
    how many customer messages its money served.

    THE MONEY AND THE TURNS ARE NOT A RATIO. A `by_day` row prices the whole
    day's ledger, the eval runs, the red-team runs and the golden drafts
    included, while `turns` counts customer turns alone. Dividing the one by the
    other reads as a per-turn cost and is not one. `turns.cost_per_turn_usd` is
    the per-turn cost, and it is built from turn rows alone.

    A turn whose judge calls land after CAT midnight has calls on two dates, so
    it counts in both days' `turns`, and the day counts add up to more than the
    window's turn count.
    """
    groups: dict = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    out = []
    for k in sorted(groups):
        figures = _money(roll_up(row.call for row in groups[k]))
        figures["turns"] = len(
            {row.call.job_id for row in groups[k] if row.conversation_id is not None}
        )
        out.append((k, figures))
    return out


def _dearest_first(conversations: list[dict]) -> list[dict]:
    """The costliest conversations first, the unpriced ones last, capped.

    Key order put whichever conversation ids sorted lowest in front of the ones
    the money is in. The cap is a reading aid: `total` and `turns` hold the whole
    window's money either way, and `turns.conversations` says how many
    conversations the list leaves out.
    """
    ordered = sorted(
        conversations,
        key=lambda row: (row["cost_usd"] is None, -(row["cost_usd"] or 0.0)),
    )
    return ordered[:_CONVERSATION_LIMIT]


def summarise_usage(rows: Sequence[LedgerRow], window_days: int) -> dict:
    """The four grains over one window of ledger rows. Pure; the tests live here.

    `turns` covers only the calls whose job was a turn, judges included, so
    `cost_per_turn_usd` is what serving one customer message costs. It is None
    when there were no turns or any turn call went unpriced. `turns.count` is
    turn jobs and `turns.conversations` is the conversations they belong to, so
    a reader can tell a truncated `by_conversation` list from a whole one.

    `price_versions` names every book version the window priced against, sorted.
    It is a list rather than one name because a window can span a correction to
    the book.
    """
    by_purpose = roll_up(row.call for row in rows)
    turn_rows = [row for row in rows if row.conversation_id is not None]
    turn_jobs = {row.call.job_id for row in turn_rows}
    turns = _money(roll_up(row.call for row in turn_rows))
    turns["count"] = len(turn_jobs)
    turns["conversations"] = len({row.conversation_id for row in turn_rows})
    turns["cost_per_turn_usd"] = (
        turns["cost_usd"] / len(turn_jobs) if turn_jobs and turns["cost_usd"] is not None else None
    )
    return {
        "window_days": window_days,
        "price_versions": sorted({row.price_version for row in by_purpose if row.price_version}),
        "total": _money(by_purpose),
        "turns": turns,
        "by_purpose": _purpose_rows(by_purpose),
        "by_day": [
            {"day": day.isoformat(), **money}
            for day, money in _grouped(rows, lambda r: r.call.at.astimezone(CAT).date())
        ],
        "by_conversation": _dearest_first(
            [
                {"conversation_id": conversation_id, **money}
                for conversation_id, money in _grouped(turn_rows, lambda r: r.conversation_id)
            ]
        ),
    }


def read_agent_usage(conn_str: str, agent_id: str, window_days: int = 7) -> dict:
    """Blocking. The route wraps this in `asyncio.to_thread`."""
    return summarise_usage(read_agent_ledger(conn_str, agent_id, window_days), window_days)
