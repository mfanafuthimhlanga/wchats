"""
Unit companion for the AUD-03 gated harness
(tests/integration/test_aud03_audit_gap.py).

Proves every correctness claim compute_audit_gap's docstring makes — UTC
bucketing, the inclusive 30-day window, midnight adjacency, order
independence, zero-day presence, and the vacuous-pass guard — against
hand-built fixture data. No Postgres, no Redis, no app.* service call, no
mock DB object: the logic under test is pure, so a mock DB would only signal
the helper was not factored out cleanly.

Imports compute_audit_gap FROM the gated integration module rather than
duplicating it, so the two files can never silently drift apart. Importing
the module does not run its fixtures or apply its pytestmark skip to this
file — tests/, tests/unit/, and tests/integration/ are all packages with
__init__.py, so pytest's default prepend import mode puts apps/api on
sys.path.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.test_aud03_audit_gap import AUDIT_WINDOW_DAYS, compute_audit_gap

WINDOW_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _row(offset: timedelta | None = None, *, skill: str = "issue_refund") -> dict:
    """Build one invocation/audit-row fixture dict at WINDOW_START + offset."""
    created_at = WINDOW_START + (offset if offset is not None else timedelta())
    return {"created_at": created_at, "skill": skill}


def test_matched_counts_across_the_window_report_zero_delta():
    invocations = [_row(timedelta(days=n)) for n in range(AUDIT_WINDOW_DAYS)]
    audit_rows = [_row(timedelta(days=n)) for n in range(AUDIT_WINDOW_DAYS)]

    result = compute_audit_gap(invocations, audit_rows, window_start=WINDOW_START)

    assert result["max_abs_delta"] == 0
    assert result["vacuous"] is False
    assert result["total_invocations"] == AUDIT_WINDOW_DAYS
    assert result["total_audit_rows"] == AUDIT_WINDOW_DAYS
    assert result["days_with_traffic"] == AUDIT_WINDOW_DAYS


def test_single_missing_audit_row_is_reported_on_its_day():
    invocations = [_row(timedelta(days=n)) for n in range(AUDIT_WINDOW_DAYS)]
    audit_rows = [_row(timedelta(days=n)) for n in range(AUDIT_WINDOW_DAYS) if n != 15]

    result = compute_audit_gap(invocations, audit_rows, window_start=WINDOW_START)

    assert result["max_abs_delta"] == 1
    missing_day = (WINDOW_START + timedelta(days=15)).date()
    assert result["per_day"][missing_day]["invocations"] == 1
    assert result["per_day"][missing_day]["audit_rows"] == 0
    assert result["per_day"][missing_day]["delta"] == -1


def test_window_covers_day_0_through_day_29_inclusive():
    first_day_row = _row(timedelta(0))
    last_day_row = _row(timedelta(days=AUDIT_WINDOW_DAYS - 1))

    result = compute_audit_gap(
        [first_day_row, last_day_row],
        [first_day_row, last_day_row],
        window_start=WINDOW_START,
    )

    assert len(result["per_day"]) == AUDIT_WINDOW_DAYS
    assert WINDOW_START.date() in result["per_day"]
    last_day = (WINDOW_START + timedelta(days=AUDIT_WINDOW_DAYS - 1)).date()
    assert last_day in result["per_day"]
    assert result["out_of_window"]["invocations"] == 0
    assert result["out_of_window"]["audit_rows"] == 0


def test_row_at_day_30_lands_out_of_window_not_in_per_day():
    out_row = _row(timedelta(days=AUDIT_WINDOW_DAYS))

    result = compute_audit_gap([out_row], [out_row], window_start=WINDOW_START)

    out_day = (WINDOW_START + timedelta(days=AUDIT_WINDOW_DAYS)).date()
    assert out_day not in result["per_day"]
    assert result["out_of_window"]["invocations"] == 1
    assert result["out_of_window"]["audit_rows"] == 1
    assert result["total_invocations"] == 0
    assert result["vacuous"] is True


def test_row_before_window_start_lands_out_of_window():
    early_row = _row(timedelta(seconds=-1))

    result = compute_audit_gap([early_row], [], window_start=WINDOW_START)

    assert result["out_of_window"]["invocations"] == 1
    assert result["out_of_window"]["audit_rows"] == 0
    assert result["total_invocations"] == 0
    assert result["vacuous"] is True


def test_day_bucket_is_computed_in_utc_not_local_time():
    non_utc_start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    # 23:30 at UTC-5 is 2026-07-02T04:30:00+00:00 — the UTC date is 2026-07-02,
    # not the local calendar date (2026-07-01).
    row = {
        "created_at": datetime(2026, 7, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5))),
        "skill": "issue_refund",
    }

    result = compute_audit_gap([row], [row], window_start=non_utc_start)

    utc_day = datetime(2026, 7, 2, tzinfo=timezone.utc).date()
    assert result["per_day"][utc_day]["invocations"] == 1
    assert result["per_day"][utc_day]["audit_rows"] == 1


def test_midnight_boundary_belongs_to_the_later_day_only():
    midnight_row = _row(timedelta(days=5))  # exactly WINDOW_START + 5 days, 00:00:00 UTC

    result = compute_audit_gap([midnight_row], [midnight_row], window_start=WINDOW_START)

    day = (WINDOW_START + timedelta(days=5)).date()
    previous_day = (WINDOW_START + timedelta(days=4)).date()
    assert result["per_day"][day]["invocations"] == 1
    assert result["per_day"][previous_day]["invocations"] == 0


def test_result_is_independent_of_input_row_order():
    invocations = [_row(timedelta(days=n)) for n in range(AUDIT_WINDOW_DAYS)]
    audit_rows = [_row(timedelta(days=n)) for n in range(AUDIT_WINDOW_DAYS) if n != 3]

    baseline = compute_audit_gap(invocations, audit_rows, window_start=WINDOW_START)

    rng = random.Random(1903)
    shuffled_invocations = list(invocations)
    shuffled_audit_rows = list(audit_rows)
    rng.shuffle(shuffled_invocations)
    rng.shuffle(shuffled_audit_rows)

    shuffled = compute_audit_gap(
        shuffled_invocations, shuffled_audit_rows, window_start=WINDOW_START
    )

    assert shuffled == baseline


def test_zero_traffic_window_is_reported_vacuous_not_clean():
    result = compute_audit_gap([], [], window_start=WINDOW_START)

    assert result["vacuous"] is True
    assert result["max_abs_delta"] == 0
    assert result["total_invocations"] == 0
    assert result["total_audit_rows"] == 0


def test_naive_datetime_raises_value_error():
    naive_row = {"created_at": datetime(2026, 1, 5, 12, 0, 0), "skill": "issue_refund"}

    with pytest.raises(ValueError):
        compute_audit_gap([naive_row], [], window_start=WINDOW_START)


def test_every_day_in_window_is_present_even_with_no_traffic():
    single_row = _row(timedelta(days=10))

    result = compute_audit_gap([single_row], [single_row], window_start=WINDOW_START)

    assert len(result["per_day"]) == AUDIT_WINDOW_DAYS
    zero_day = (WINDOW_START + timedelta(days=0)).date()
    assert result["per_day"][zero_day] == {"invocations": 0, "audit_rows": 0, "delta": 0}
