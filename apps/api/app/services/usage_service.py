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
    per purpose, per CAT day, per conversation, per job, and the turns as a
    whole. A turn is a `turn_metrics` row; its calls are the ledger rows with its
    `job_id`. Ledger rows whose job is not a turn (an eval run, a red-team run, a
    draft) count in the purpose, day and job figures and in nothing per turn.

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

#: How many jobs `by_job` carries. A job is one unit of work the platform did,
#: so a busy week has thousands of them and a reader chasing a bill acts on the
#: dearest few. `total` still holds the window's whole money either way.
_JOB_LIMIT = 20


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


def _dearest_first(rows: list[dict], limit: int = _CONVERSATION_LIMIT) -> list[dict]:
    """The costliest rows first, the unpriced ones last, capped at `limit`.

    Key order put whichever ids sorted lowest in front of the ones the money is
    in. The cap is a reading aid, and `total` holds the whole window's money
    whatever it leaves out; for the conversation list, `turns.conversations` also
    says how many were left out.

    An unpriced row sorts LAST rather than as zero. It is the row most likely to
    hold real money, and putting it at the bottom of a cheap-looking list is how
    a price gap goes unnoticed; `unpriced_calls` on the row says why it is there.
    """
    ordered = sorted(
        rows,
        key=lambda row: (row["cost_usd"] is None, -(row["cost_usd"] or 0.0)),
    )
    return ordered[:limit]


def _job_purposes(rows: Sequence[LedgerRow]) -> dict[str, list[str]]:
    """Every purpose each job's calls were made under, sorted, one entry per job.

    Sorted rather than first-seen, so two runs of the same shape of work read the
    same and a reader can compare two jobs' lists by eye.
    """
    seen: dict[str, set] = defaultdict(set)
    for row in rows:
        # `ModelCall.job_id` is optional, and a call belonging to no job is not a
        # job. `_job_rows` filters those out before it calls this; the test here
        # is what lets the annotation say `dict[str, ...]` and mean it.
        if row.call.job_id is not None:
            seen[row.call.job_id].add(row.call.purpose)
    return {job_id: sorted(purposes) for job_id, purposes in seen.items()}


def _job_rows(rows: Sequence[LedgerRow]) -> tuple[list[dict], int]:
    """The dearest `_JOB_LIMIT` jobs in the window, and how many jobs there were.

    A JOB IS THE GRAIN A BILL IS ARGUED AT. `by_purpose` says what the money was
    spent on and `by_conversation` says which Customer it served. Neither answers
    "which single piece of work cost the most". One eval run and one customer
    turn both appear as a job here, and `is_turn` is the difference. A turn is
    work a Customer waited for; everything else is work the platform chose to do.

    THE COUNT TRAVELS BESIDE THE LIST because the list is capped.
    `turns.conversations` already does this for `by_conversation`, and without
    the same thing here a window holding four hundred jobs and a window holding
    twenty are the same twenty rows on the screen.

    A LEDGER ROW WITH NO JOB IS NOT A JOB. `ModelCall.job_id` is None for a call
    made outside any unit of work, a rollup among them. Those calls stay in
    `total` and in `by_purpose`, where they are honest, and are absent here
    rather than pooled under a null that would read as one enormous job.
    """
    of_a_job = [row for row in rows if row.call.job_id]
    purposes = _job_purposes(of_a_job)
    jobs = []
    for job_id, figures in _grouped(of_a_job, lambda r: r.call.job_id):
        # `_grouped` counts the distinct turn jobs in a group, and a group that
        # IS one job holds one or none. That is the same question `is_turn` asks.
        is_turn = bool(figures.pop("turns"))
        jobs.append(
            {"job_id": job_id, **figures, "purposes": purposes[job_id], "is_turn": is_turn}
        )
    return _dearest_first(jobs, _JOB_LIMIT), len(jobs)


def summarise_usage(rows: Sequence[LedgerRow], window_days: int) -> dict:
    """The five grains over one window of ledger rows. Pure; the tests live here.

    `turns` covers only the calls whose job was a turn, judges included, so
    `cost_per_turn_usd` is what serving one customer message costs. It is None
    when there were no turns or any turn call went unpriced. `turns.count` is
    turn jobs and `turns.conversations` is the conversations they belong to, so
    a reader can tell a truncated `by_conversation` list from a whole one.

    `price_versions` names every book version the window priced against, sorted.
    It is a list rather than one name because a window can span a correction to
    the book.

    `by_job` is the dearest twenty single pieces of work, turns and eval runs in
    one list, which is the grain a surprising bill is read at. `jobs` is how many
    there were, so a capped list cannot read as the whole window.
    """
    by_purpose = roll_up(row.call for row in rows)
    by_job, job_count = _job_rows(rows)
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
        "by_job": by_job,
        # How many jobs the window held, so the capped list above can be read as
        # a top twenty rather than as the whole of it.
        "jobs": job_count,
    }


def read_agent_usage(conn_str: str, agent_id: str, window_days: int = 7) -> dict:
    """Blocking. The route wraps this in `asyncio.to_thread`."""
    return summarise_usage(read_agent_ledger(conn_str, agent_id, window_days), window_days)
