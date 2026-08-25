"""Tests for rollup_model_calls, the daily task that prices a tenant's ledger day.

The arithmetic is proved in test_usage_rollup.py against calls built in memory, and
the live databases are exercised in tests/integration/test_usage_rollup_e2e.py. What
is left, and what this file owns, is the task's own conduct.

    it carries no connection string in its arguments (project rule 1)
    it takes a day override and nothing else
    it fetches and decrypts inside the task, per tenant, at run time
    one unreachable tenant is skipped and every other tenant still lands
    a tenant with two databases sums across both or is skipped whole
    the upsert names the primary key, which is what makes a re-run idempotent
    a price gap is logged with a provider, a model and a count
    the beat fires at 00:30 UTC on the runtime queue

The seams (`tenant_dsn_ciphertexts`, `tenant_calls`, `write_usage`) are patched by
name on the task module, so the task's own loop is what runs.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.domain.usage_rollup import PriceGap, PurposeUsage
from app.worker.celery_app import celery_app
from app.worker.tasks.runtime import usage as task_module
from app.worker.tasks.runtime.usage import day_window, rollup_model_calls

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"

TASK_NAME = "app.worker.tasks.runtime.usage.rollup_model_calls"
BEAT_NAME = "usage-rollup-daily"


def usage_row(purpose: str = "judge", **overrides) -> PurposeUsage:
    """One priced purpose, as roll_up returns it."""
    fields = {
        "purpose": purpose,
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 30,
        "cache_creation_tokens": 40,
        "call_count": 2,
        "cost_usd": Decimal("0.44"),
        "cost_zar": Decimal("7.050428"),
        "price_version": "2026-08-23.1",
        "fx_version": "usd_zar-2026-08-24",
        "price_gaps": (),
        "unrated_call_count": 0,
    }
    fields.update(overrides)
    return PurposeUsage(**fields)


def run_task(dsns: dict, calls_by_tenant, rows_by_tenant=None):
    """Run the task with its three database seams replaced.

    Args:
        dsns:            tenant id -> list of encrypted connection strings.
        calls_by_tenant: callable taking the ciphertext list, returning the calls
                         that tenant made, or raising to stand for an unreachable
                         database.
        rows_by_tenant:  what roll_up should return for the calls it is handed.

    Returns:
        (the task's summary dict, the mock that recorded every write).
    """
    writes = MagicMock()
    rows = rows_by_tenant if rows_by_tenant is not None else (usage_row(),)
    with (
        patch.object(task_module, "get_sync_db") as db,
        patch.object(task_module, "tenant_dsn_ciphertexts", return_value=dsns),
        patch.object(task_module, "tenant_calls", side_effect=calls_by_tenant),
        patch.object(task_module, "write_usage", writes),
        patch.object(task_module, "roll_up", return_value=rows),
    ):
        db.return_value.__enter__.return_value = MagicMock()
        summary = rollup_model_calls(day="2026-08-24")
    return summary, writes


# ---------------------------------------------------------------------------
# The day, and what the task is allowed to be told
# ---------------------------------------------------------------------------


def test_the_task_takes_a_day_and_nothing_else():
    """Project rule 1: a task receives ids, never a connection string."""
    names = set(inspect.signature(rollup_model_calls.run).parameters)
    assert names <= {"self", "day"}, (
        f"rollup_model_calls takes {sorted(names)}. Anything beyond a day override "
        "is a value the beat cannot supply and a credential the queue must not hold"
    )


@pytest.mark.parametrize("forbidden", ["dsn", "conn", "url", "secret", "key"])
def test_no_argument_could_ever_hold_a_credential(forbidden):
    names = " ".join(inspect.signature(rollup_model_calls.run).parameters)
    assert forbidden not in names.lower(), (
        f"rollup_model_calls names an argument containing {forbidden!r}. Connection "
        "strings are fetched and decrypted inside the task, never passed to it"
    )


def test_no_day_means_yesterday_in_utc():
    """The beat runs at 00:30 UTC, so the day that just closed is the one to price."""
    day, _start, _end = day_window(None, now=datetime(2026, 8, 25, 0, 30, tzinfo=timezone.utc))
    assert day == date(2026, 8, 24)


def test_yesterday_is_read_from_the_clock_not_from_the_hour_the_task_runs():
    day, _start, _end = day_window(None, now=datetime(2026, 8, 25, 23, 59, tzinfo=timezone.utc))
    assert day == date(2026, 8, 24)


def test_an_explicit_day_wins():
    """A corrected book is re-derived against a named day, which is why this exists."""
    day, _start, _end = day_window("2026-08-20")
    assert day == date(2026, 8, 20)


def test_the_window_is_half_open_from_midnight_to_midnight():
    """A call at exactly the next midnight belongs to the next day, not to both."""
    _day, start, end = day_window("2026-08-20")
    assert start == datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)


def test_a_day_that_is_not_a_date_is_refused():
    with pytest.raises(ValueError):
        day_window("yesterday")


# ---------------------------------------------------------------------------
# The fan-out
# ---------------------------------------------------------------------------


def test_every_tenant_with_a_dsn_is_rolled_up():
    summary, writes = run_task(
        {TENANT_A: [b"cipher-a"], TENANT_B: [b"cipher-b"]},
        calls_by_tenant=lambda *_args: [],
    )
    assert summary["tenants_done"] == 2
    assert writes.call_count == 2


def test_the_row_lands_under_the_tenant_the_dsn_came_from():
    _summary, writes = run_task({TENANT_A: [b"cipher-a"]}, calls_by_tenant=lambda *_a: [])
    _db, tenant_id, day, row = writes.call_args.args
    assert tenant_id == TENANT_A
    assert day == date(2026, 8, 24)
    assert row.purpose == "judge"


def test_a_tenant_whose_database_cannot_be_reached_is_skipped():
    """One tenant's Neon project being down must not lose every other tenant's day."""

    def calls(ciphertexts, *_args):
        if ciphertexts == [b"cipher-a"]:
            raise OSError("could not connect to server")
        return []

    summary, writes = run_task(
        {TENANT_A: [b"cipher-a"], TENANT_B: [b"cipher-b"]}, calls_by_tenant=calls
    )
    assert summary["tenants_skipped"] == 1
    assert summary["tenants_done"] == 1
    assert writes.call_count == 1


def test_a_skipped_tenant_writes_no_row_at_all():
    """A partial row would read as a real day, so a broken read writes nothing."""

    def calls(*_args):
        raise OSError("could not connect to server")

    summary, writes = run_task({TENANT_A: [b"cipher-a", b"cipher-second"]}, calls_by_tenant=calls)
    assert writes.call_count == 0
    assert summary["rows_written"] == 0


def test_the_summary_counts_tenants_done_and_skipped_and_rows_written():
    summary, _writes = run_task({TENANT_A: [b"cipher-a"]}, calls_by_tenant=lambda *_a: [])
    assert summary == {
        "day": "2026-08-24",
        "tenants_done": 1,
        "tenants_skipped": 0,
        "rows_written": 1,
    }


def test_a_tenant_with_two_databases_is_read_once_across_both():
    """Both of a tenant's agent projects feed the one (tenant, purpose, day) row."""
    seen = []

    def calls(ciphertexts, *_args):
        seen.append(ciphertexts)
        return []

    run_task({TENANT_A: [b"cipher-one", b"cipher-two"]}, calls_by_tenant=calls)
    assert seen == [[b"cipher-one", b"cipher-two"]]


# ---------------------------------------------------------------------------
# What a gap does
# ---------------------------------------------------------------------------


def test_an_unpriced_purpose_still_writes_its_row():
    """The gap belongs in the table, where it is visible, not in a crashed task."""
    unpriced = usage_row(
        cost_usd=None,
        cost_zar=None,
        price_version=None,
        fx_version=None,
        price_gaps=(PriceGap("deepseek", "deepseek-v4-pro", 7),),
    )
    _summary, writes = run_task(
        {TENANT_A: [b"cipher-a"]}, calls_by_tenant=lambda *_a: [], rows_by_tenant=(unpriced,)
    )
    assert writes.call_count == 1
    assert writes.call_args.args[3].cost_usd is None


def test_the_gap_is_logged_with_the_provider_the_model_and_the_count():
    """An unpriced model is a name and a number, so somebody can go and price it."""
    unpriced = usage_row(
        cost_usd=None,
        price_gaps=(PriceGap("deepseek", "deepseek-v4-pro", 7),),
    )
    with patch.object(task_module, "log") as log:
        run_task(
            {TENANT_A: [b"cipher-a"]},
            calls_by_tenant=lambda *_a: [],
            rows_by_tenant=(unpriced,),
        )
    logged = [c.kwargs for c in log.error.call_args_list]
    assert any(
        entry.get("provider") == "deepseek"
        and entry.get("served_model") == "deepseek-v4-pro"
        and entry.get("call_count") == 7
        for entry in logged
    ), f"no gap was logged with its provider, model and count: {logged}"


def test_a_priced_purpose_logs_no_gap():
    with patch.object(task_module, "log") as log:
        run_task({TENANT_A: [b"cipher-a"]}, calls_by_tenant=lambda *_a: [])
    assert log.error.call_args_list == []


# ---------------------------------------------------------------------------
# The statements
# ---------------------------------------------------------------------------


def test_the_upsert_target_is_the_primary_key():
    """This is the whole idempotency: a second run lands on the row the first wrote."""
    sql = " ".join(task_module.UPSERT_USAGE.split()).lower()
    assert "on conflict (tenant_id, purpose, day) do update" in sql, (
        "the upsert must conflict on the primary key, or a re-run appends a second "
        f"row for the same day: {sql}"
    )


@pytest.mark.parametrize(
    "column",
    [
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "call_count",
        "cost_usd",
        "cost_zar",
        "price_version",
        "fx_version",
    ],
)
def test_the_upsert_overwrites_every_derived_column(column):
    """A re-derive against a corrected book must leave nothing from the old one."""
    sql = " ".join(task_module.UPSERT_USAGE.split()).lower()
    _insert, _sep, update = sql.partition("do update")
    assert f"{column} = excluded.{column}" in update, (
        f"{column} survives a re-derive, so a corrected book cannot reach it: {update}"
    )


def test_the_reader_names_exactly_the_columns_the_writer_writes():
    """The ledger has one writer and now one reader. They cannot be allowed to drift."""
    from app.core.model_client import _COLUMNS

    assert task_module.LEDGER_COLUMNS == _COLUMNS, (
        "the rollup reads a different column list from the one record_model_call "
        f"writes: {task_module.LEDGER_COLUMNS} against {_COLUMNS}"
    )


def test_the_select_reads_a_half_open_window():
    sql = " ".join(task_module.SELECT_CALLS.split()).lower()
    assert "at >= %s and at < %s" in sql, (
        f"the day window must be half open, or a midnight call is counted twice: {sql}"
    )


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_the_task_is_registered_under_its_module_path():
    assert rollup_model_calls.name == TASK_NAME


def test_the_task_acknowledges_late():
    """CLAUDE.md: acks_late AND idempotency, both, on every task."""
    assert rollup_model_calls.acks_late is True


def test_the_task_runs_on_the_runtime_queue():
    assert rollup_model_calls.queue == "runtime"


def test_the_worker_imports_the_task_module():
    """A task the worker never imports is a beat entry that fires into nothing."""
    assert "app.worker.tasks.runtime.usage" in celery_app.conf.include


def test_the_beat_fires_the_rollup_daily_at_0030_utc():
    entry = celery_app.conf.beat_schedule[BEAT_NAME]
    assert entry["task"] == TASK_NAME
    assert entry["schedule"].hour == {0}
    assert entry["schedule"].minute == {30}


def test_the_beat_passes_no_arguments():
    """The day override exists for a re-derive by hand, not for the schedule."""
    entry = celery_app.conf.beat_schedule[BEAT_NAME]
    assert not entry.get("args")
    assert not entry.get("kwargs")
