"""Tests for app.domain.usage_rollup, one day of ledger rows to one row per purpose.

The rollup task owns databases. This module owns the arithmetic, so every rule the
table encodes is proved here against calls built in memory, with no connection
anywhere and every figure hand computed from the seeded price book.

THE FIVE RULES UNDER TEST
    1. Tokens and calls sum per purpose, and the purposes come out sorted so two
       runs write the same rows in the same order.
    2. Money is summed PER CALL, never derived from the summed tokens. The price
       window is read off each call's own hour, so a day that spans peak and off
       peak prices at two rates and a sum of tokens would price at one.
    3. A purpose group holding a model the book refuses loses its money entirely,
       keeps its counts, and names the gap. A partial sum is worse than a NULL,
       because it looks like a real figure.
    4. A call with no fx rate on or before its CAT date loses the rand and keeps
       the dollars. The two figures fail independently, so one gap never hides the
       other.
    5. Each version column names exactly one version. A group is one purpose on one
       CAT day, priced against one book, so there is nothing for a second name to
       come from.

WHERE THE EXPECTED FIGURES COME FROM
    PRICE_BOOK 2026-08-23.1, DeepSeek V4 Flash. Peak input is $0.44 per million and
    peak output $1.32; off peak and cache reads are a fifth. 2026-08-25 is a
    Tuesday, so 08:00 UTC is 10:00 CAT and sits in the second peak window, while
    18:00 UTC is 20:00 CAT and is off peak. FX_RATES carries usd_zar 16.0237 as of
    2026-08-24.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.domain.model_call import ModelCall
from app.domain.pricing import FxRate
from app.domain.usage_rollup import roll_up

TENANT = "11111111-1111-1111-1111-111111111111"

#: 10:00 CAT on a Tuesday. Inside the 08:00 to 12:00 peak window.
PEAK = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
#: 20:00 CAT on the same day. Outside every peak window.
OFF_PEAK = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
#: 01:00 CAT on 2026-08-26. The same UTC day, and a CAT day the rollup never mixes
#: with the one above, which is what makes a single fx version right.
NEXT_CAT_DAY = datetime(2026, 8, 25, 23, 0, tzinfo=timezone.utc)

MILLION = 1_000_000


def call(
    purpose: str = "judge",
    *,
    at: datetime = PEAK,
    served_model: str = "deepseek-v4-flash",
    model_source: str = "mapped_by_docs",
    provider: str = "deepseek",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> ModelCall:
    """One ledger row, defaulting to a judge call that spent nothing."""
    return ModelCall(
        purpose=purpose,
        provider=provider,
        requested_model="claude-haiku-4-5",
        served_model=served_model,
        model_source=model_source,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        at=at,
        tenant_id=TENANT,
    )


def by_purpose(usage) -> dict:
    return {row.purpose: row for row in usage}


# ---------------------------------------------------------------------------
# Grouping and counting
# ---------------------------------------------------------------------------


def test_no_calls_roll_up_to_no_rows():
    """A tenant that made no call that day gets no row, not a row of zeros."""
    assert roll_up([]) == ()


def test_each_purpose_gets_exactly_one_row():
    usage = roll_up([call("judge"), call("judge"), call("agent_turn")])
    assert [row.purpose for row in usage] == ["agent_turn", "judge"]


def test_the_purposes_come_out_sorted():
    """Two runs must write the same rows in the same order for a diff to mean anything."""
    usage = roll_up([call("scenario_gen"), call("agent_turn"), call("judge")])
    assert [row.purpose for row in usage] == ["agent_turn", "judge", "scenario_gen"]


def test_call_count_counts_every_row_including_the_ones_that_spent_nothing():
    """A call with no tokens still happened, and a count that skips it hides a bug."""
    usage = by_purpose(roll_up([call("judge"), call("judge"), call("judge")]))
    assert usage["judge"].call_count == 3


def test_all_four_token_counts_sum_within_a_purpose():
    usage = by_purpose(
        roll_up(
            [
                call("judge", input_tokens=10, output_tokens=1, cache_read_tokens=100,
                     cache_creation_tokens=1000),
                call("judge", input_tokens=20, output_tokens=2, cache_read_tokens=200,
                     cache_creation_tokens=2000),
            ]
        )
    )
    row = usage["judge"]
    assert (row.input_tokens, row.output_tokens) == (30, 3)
    assert (row.cache_read_tokens, row.cache_creation_tokens) == (300, 3000)


def test_one_purpose_never_borrows_another_purpose_tokens():
    usage = by_purpose(
        roll_up([call("judge", input_tokens=10), call("agent_turn", input_tokens=99)])
    )
    assert usage["judge"].input_tokens == 10
    assert usage["agent_turn"].input_tokens == 99


# ---------------------------------------------------------------------------
# Money, priced per call and then summed
# ---------------------------------------------------------------------------


def test_a_peak_call_prices_at_the_peak_rate():
    """A million peak input tokens is $0.44 in the seeded book."""
    usage = by_purpose(roll_up([call("judge", at=PEAK, input_tokens=MILLION)]))
    assert usage["judge"].cost_usd == Decimal("0.44")


def test_an_off_peak_call_prices_at_a_fifth():
    """A million off-peak output tokens is $0.264, a fifth of the peak $1.32."""
    usage = by_purpose(roll_up([call("judge", at=OFF_PEAK, output_tokens=MILLION)]))
    assert usage["judge"].cost_usd == Decimal("0.264")


def test_a_day_that_spans_both_windows_prices_each_call_in_its_own_window():
    """The reason money is summed per call rather than derived from summed tokens.

    A million input at peak and a million output off peak is 0.44 + 0.264. Pricing
    the summed tokens in one window would report 1.76 or 0.352, and both are wrong.
    """
    usage = by_purpose(
        roll_up(
            [
                call("judge", at=PEAK, input_tokens=MILLION),
                call("judge", at=OFF_PEAK, output_tokens=MILLION),
            ]
        )
    )
    assert usage["judge"].cost_usd == Decimal("0.704")


def test_the_rand_is_the_dollars_at_the_dated_rate():
    """0.704 USD at 16.0237 rand per dollar, exact, with no rounding anywhere."""
    usage = by_purpose(
        roll_up(
            [
                call("judge", at=PEAK, input_tokens=MILLION),
                call("judge", at=OFF_PEAK, output_tokens=MILLION),
            ]
        )
    )
    assert usage["judge"].cost_zar == Decimal("11.2806848")


def test_a_cache_read_is_priced_at_the_cache_rate_not_the_input_rate():
    """Two million cache reads is $0.176, not the $0.88 fresh input would cost."""
    usage = by_purpose(roll_up([call("judge", at=PEAK, cache_read_tokens=2 * MILLION)]))
    assert usage["judge"].cost_usd == Decimal("0.176")
    assert usage["judge"].cost_zar == Decimal("2.8201712")


def test_every_figure_names_the_book_and_the_rate_that_produced_it():
    usage = by_purpose(roll_up([call("judge", at=PEAK, input_tokens=MILLION)]))
    assert usage["judge"].price_version == "2026-08-23.1"
    assert usage["judge"].fx_version == "usd_zar-2026-08-24"


def test_a_cat_day_names_one_fx_version_even_when_the_table_holds_a_later_rate():
    """The rate is picked by the call's CAT date, and a group holds one CAT date.

    The 26th's rate sits in the table and prices nothing here, because every call
    in this group happened on the 25th in CAT. A comma-joined pair of versions used
    to be possible when a group was a UTC day spanning 02:00 CAT to 02:00 CAT.
    """
    rates = (
        FxRate(usd_zar=Decimal("16.0237"), as_of=date(2026, 8, 25), source="test"),
        FxRate(usd_zar=Decimal("17.0000"), as_of=date(2026, 8, 26), source="test"),
    )
    usage = by_purpose(
        roll_up(
            [
                call("judge", at=PEAK, input_tokens=MILLION),
                call("judge", at=OFF_PEAK, output_tokens=MILLION),
            ],
            rates=rates,
        )
    )
    row = usage["judge"]
    assert row.fx_version == "usd_zar-2026-08-25"
    # 0.44 peak input plus 0.264 off-peak output, all at 16.0237.
    assert row.cost_zar == Decimal("0.704") * Decimal("16.0237")


def test_the_later_cat_day_is_priced_by_the_later_rate_when_it_is_asked_for():
    """The other half of the pair above. The task hands each CAT day over on its own."""
    rates = (
        FxRate(usd_zar=Decimal("16.0237"), as_of=date(2026, 8, 25), source="test"),
        FxRate(usd_zar=Decimal("17.0000"), as_of=date(2026, 8, 26), source="test"),
    )
    usage = by_purpose(
        roll_up([call("judge", at=NEXT_CAT_DAY, input_tokens=MILLION)], rates=rates)
    )
    row = usage["judge"]
    assert row.fx_version == "usd_zar-2026-08-26"
    # 01:00 CAT is off peak, so 0.088, at 17.0000.
    assert row.cost_zar == Decimal("0.088") * 17


def test_one_book_gives_one_price_version_however_many_windows_a_group_spans():
    """The hours move the rate. They never move the version, which is the book's."""
    usage = by_purpose(
        roll_up(
            [
                call("judge", at=PEAK, input_tokens=MILLION),
                call("judge", at=OFF_PEAK, output_tokens=MILLION),
            ]
        )
    )
    assert usage["judge"].price_version == "2026-08-23.1"


# ---------------------------------------------------------------------------
# The gaps, which the table shows rather than hides
# ---------------------------------------------------------------------------


def test_a_model_the_book_refuses_takes_the_whole_group_money_to_none():
    """A partial sum understates the day and still reads as a real figure."""
    usage = by_purpose(
        roll_up(
            [
                call("judge", input_tokens=MILLION),
                call("judge", served_model="deepseek-v4-pro", input_tokens=MILLION),
            ]
        )
    )
    row = usage["judge"]
    assert row.cost_usd is None
    assert row.cost_zar is None
    assert row.price_version is None
    assert row.fx_version is None


def test_the_unpriceable_group_keeps_its_tokens_and_its_count():
    """Tokens beside a NULL cost are what make the gap visible in the table."""
    usage = by_purpose(
        roll_up(
            [
                call("judge", input_tokens=MILLION),
                call("judge", served_model="deepseek-v4-pro", input_tokens=MILLION),
            ]
        )
    )
    row = usage["judge"]
    assert row.input_tokens == 2 * MILLION
    assert row.call_count == 2


def test_the_gap_names_the_provider_the_model_and_how_many_calls():
    """The task logs this, so an unpriced model is a name and a number, not a mystery."""
    usage = by_purpose(
        roll_up(
            [
                call("judge", served_model="deepseek-v4-pro"),
                call("judge", served_model="deepseek-v4-pro"),
            ]
        )
    )
    (gap,) = usage["judge"].price_gaps
    assert (gap.provider, gap.served_model, gap.call_count) == ("deepseek", "deepseek-v4-pro", 2)


def test_an_unreported_model_leaves_the_group_money_null_and_names_the_gap():
    """A response that named no model puts the requested alias in served_model.

    The book prices no `claude-haiku-4-5` from deepseek, so the group keeps its
    tokens and its count, its money goes to None, and the gap names the alias.
    """
    usage = by_purpose(
        roll_up(
            [
                call(
                    "judge",
                    at=PEAK,
                    served_model="claude-haiku-4-5",
                    model_source="unreported",
                    input_tokens=MILLION,
                )
            ]
        )
    )
    row = usage["judge"]
    assert row.cost_usd is None
    assert row.cost_zar is None
    assert row.input_tokens == MILLION
    assert row.call_count == 1
    (gap,) = row.price_gaps
    assert (gap.provider, gap.served_model, gap.call_count) == ("deepseek", "claude-haiku-4-5", 1)


def test_a_priced_group_reports_no_gap():
    usage = by_purpose(roll_up([call("judge", input_tokens=MILLION)]))
    assert usage["judge"].price_gaps == ()


def test_an_unpriceable_purpose_leaves_every_other_purpose_priced():
    """The whole reason UnknownPrice is caught per group instead of killing the run."""
    usage = by_purpose(
        roll_up(
            [
                call("judge", served_model="deepseek-v4-pro", input_tokens=MILLION),
                call("agent_turn", at=PEAK, input_tokens=MILLION),
            ]
        )
    )
    assert usage["judge"].cost_usd is None
    assert usage["agent_turn"].cost_usd == Decimal("0.44")


def test_a_call_older_than_every_fx_row_keeps_its_dollars():
    """The dollars are known. Only the rand is not, so only the rand goes to NULL."""
    older = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    usage = by_purpose(roll_up([call("judge", at=older, input_tokens=MILLION)]))
    row = usage["judge"]
    assert row.cost_usd == Decimal("0.44")
    assert row.price_version == "2026-08-23.1"
    assert row.cost_zar is None
    assert row.fx_version is None


def test_a_call_with_no_rate_is_counted_so_the_missing_rand_is_attributable():
    older = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    usage = by_purpose(roll_up([call("judge", at=older), call("judge", at=older)]))
    assert usage["judge"].unrated_call_count == 2


def test_a_rated_group_counts_no_unrated_calls():
    usage = by_purpose(roll_up([call("judge", at=PEAK, input_tokens=MILLION)]))
    assert usage["judge"].unrated_call_count == 0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run", [1, 2])
def test_the_same_calls_roll_up_to_the_same_rows_every_time(run):
    """The rollup is re-run against the same day whenever a book is corrected."""
    calls = [
        call("judge", at=PEAK, input_tokens=MILLION),
        call("agent_turn", at=OFF_PEAK, output_tokens=MILLION),
    ]
    assert roll_up(calls) == roll_up(calls)
