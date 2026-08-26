"""Unit tests for app.domain.pricing, money derived from tokens (ticket #46, issue #22).

Tokens are the fact a provider reports. Money is a reading of that fact against a
book, and the book changes: DeepSeek's peak windows are a published tariff, and
the rand rate moves every day. So nothing here stores money. `cost_usd` and
`cost_zar` are pure functions over a `ModelCall` and a versioned table, and the
version travels with the figure so a report can say which book produced it.

WHY EVERY EXPECTED FIGURE IS A LITERAL
    Each assertion below carries the arithmetic in a comment beside it, tokens
    times rate, worked out by hand. Building the expected value by calling
    `cost_usd` would compare the implementation against itself and stay green
    through a swapped window, an inverted peak rule or a dropped cache rate.

THE WINDOW RULE UNDER TEST
    Peak is 03:00 to 06:00 and 08:00 to 12:00 CAT, Monday to Friday. CAT is
    UTC+2 with no daylight saving, so the boundaries are testable to the minute
    from a UTC timestamp: 01:00 UTC is 03:00 CAT and peak, 00:59 UTC is 02:59
    CAT and is not. A window is half open, so 12:00 CAT is already off peak.

    The weekday rule is separate from the hour rule, which is why a Saturday
    sitting inside the peak hours prices off peak.

THE RE-PRICE TEST
    `test_a_new_book_re_prices_the_same_stored_call` is an acceptance criterion
    of #46, not a nicety. The same call object, priced against a second book,
    yields the second book's figure and the second book's version. If any money
    were stored on the row, that test could not pass.
"""

import base64
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

# Env setup before any `from app` import, matching tests/unit/test_chunk_type.py.
# app.domain.pricing imports the standard library and one domain sibling, so
# Settings never loads here, but the block keeps the file runnable in isolation.
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")

import pytest  # noqa: E402

from app.domain.model_call import ModelCall, ModelSource  # noqa: E402
from app.domain.pricing import (  # noqa: E402
    FX_RATES,
    PRICE_BOOK,
    FxRate,
    PriceBook,
    TokenKind,
    UnknownFxRate,
    UnknownPrice,
    Window,
    cost_usd,
    cost_zar,
    window_for,
)

TENANT = "11111111-1111-4111-8111-111111111111"
PROVIDER = "deepseek"
MODEL = "deepseek-v4-flash"

# CAT is UTC+2 with no daylight saving, so every timestamp below is written in
# UTC and the CAT hour is two ahead of it, all year.
#
#   2026-08-25 is a Tuesday   07:30 UTC = 09:30 CAT, inside 08:00 to 12:00
#   2026-08-22 is a Saturday  07:30 UTC = 09:30 CAT, inside the hours, wrong day
PEAK_TUESDAY = datetime(2026, 8, 25, 7, 30, tzinfo=timezone.utc)
OFF_PEAK_TUESDAY = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
PEAK_HOURS_SATURDAY = datetime(2026, 8, 22, 7, 30, tzinfo=timezone.utc)


def _call(at: datetime = PEAK_TUESDAY, **overrides) -> ModelCall:
    fields = {
        "purpose": "judge",
        "provider": PROVIDER,
        "requested_model": "claude-haiku-4-5",
        "served_model": MODEL,
        "model_source": ModelSource.REPORTED,
        "input_tokens": 500_000,
        "output_tokens": 200_000,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "at": at,
        "tenant_id": TENANT,
    }
    fields.update(overrides)
    return ModelCall(**fields)


def _rates(peak_input: str, peak_output: str, off_input: str, off_output: str, cache_read: str):
    """One provider and one model, priced per million tokens in both windows.

    Cache creation takes the fresh input rate for its window, which is what the
    seeded book does and what the fetched tariff leaves unstated.
    """
    return {
        (PROVIDER, MODEL, TokenKind.INPUT, Window.PEAK): Decimal(peak_input),
        (PROVIDER, MODEL, TokenKind.OUTPUT, Window.PEAK): Decimal(peak_output),
        (PROVIDER, MODEL, TokenKind.CACHE_CREATION, Window.PEAK): Decimal(peak_input),
        (PROVIDER, MODEL, TokenKind.CACHE_READ, Window.PEAK): Decimal(cache_read),
        (PROVIDER, MODEL, TokenKind.INPUT, Window.OFF_PEAK): Decimal(off_input),
        (PROVIDER, MODEL, TokenKind.OUTPUT, Window.OFF_PEAK): Decimal(off_output),
        (PROVIDER, MODEL, TokenKind.CACHE_CREATION, Window.OFF_PEAK): Decimal(off_input),
        (PROVIDER, MODEL, TokenKind.CACHE_READ, Window.OFF_PEAK): Decimal(cache_read),
    }


def _book(version: str, rates) -> PriceBook:
    return PriceBook(
        price_version=version,
        utc_offset_hours=2,
        peak_windows_cat=((3, 6), (8, 12)),
        peak_weekdays=(0, 1, 2, 3, 4),
        rates_per_million=rates,
    )


# The fetched 2026-08-23 DeepSeek V4 Flash tariff, spelled out so the tests below
# do not depend on the seeded book holding it.
FETCHED = _book("test-2026-08-23", _rates("0.44", "1.32", "0.088", "0.264", "0.088"))


# ---------------------------------------------------------------------------
# The window: the CAT hour and the weekday, read off a UTC instant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utc_hour,utc_minute,expected",
    [
        # 01:00 UTC is 03:00 CAT, the first minute of the first peak window.
        (1, 0, Window.PEAK),
        # 00:59 UTC is 02:59 CAT, one minute before it opens.
        (0, 59, Window.OFF_PEAK),
        # 03:59 UTC is 05:59 CAT, the last minute of the first window.
        (3, 59, Window.PEAK),
        # 04:00 UTC is 06:00 CAT. The window is half open, so this is off peak.
        (4, 0, Window.OFF_PEAK),
        # 06:00 UTC is 08:00 CAT, the South African business morning opening.
        (6, 0, Window.PEAK),
        # 05:59 UTC is 07:59 CAT, one minute before it.
        (5, 59, Window.OFF_PEAK),
        # 09:59 UTC is 11:59 CAT, the last minute of the second window.
        (9, 59, Window.PEAK),
        # 10:00 UTC is 12:00 CAT, and 12:00 closes the window.
        (10, 0, Window.OFF_PEAK),
        # 22:00 UTC is midnight CAT on the 26th, still a weekday, still off peak.
        (22, 0, Window.OFF_PEAK),
    ],
)
def test_the_cat_boundaries_are_exact_on_a_weekday(utc_hour, utc_minute, expected):
    at = datetime(2026, 8, 25, utc_hour, utc_minute, tzinfo=timezone.utc)
    assert window_for(at, FETCHED) is expected


@pytest.mark.parametrize("day", [22, 23])
def test_a_weekend_inside_the_peak_hours_is_off_peak(day):
    """2026-08-22 is a Saturday and 2026-08-23 a Sunday. 09:30 CAT on either is off peak."""
    at = datetime(2026, 8, day, 7, 30, tzinfo=timezone.utc)
    assert window_for(at, FETCHED) is Window.OFF_PEAK


def test_the_same_clock_time_on_the_monday_after_is_peak():
    """The weekday rule is the only difference between this and the Saturday above."""
    assert window_for(datetime(2026, 8, 24, 7, 30, tzinfo=timezone.utc), FETCHED) is Window.PEAK


def test_the_window_is_read_from_the_instant_not_the_written_zone():
    """23:30 on 2026-08-24 in UTC is 01:30 CAT on the 25th, which no window covers."""
    sast = timezone(timedelta(hours=2))
    written_in_sast = datetime(2026, 8, 25, 1, 30, tzinfo=sast)
    assert window_for(written_in_sast, FETCHED) is Window.OFF_PEAK


# ---------------------------------------------------------------------------
# cost_usd: tokens times rate, per kind, per window
# ---------------------------------------------------------------------------


def test_a_peak_weekday_call_prices_at_the_peak_rate():
    # input   500_000 / 1_000_000 * 0.44 = 0.22
    # output  200_000 / 1_000_000 * 1.32 = 0.264
    # total                                0.484
    usd, version = cost_usd(_call(PEAK_TUESDAY), FETCHED)
    assert usd == Decimal("0.484")
    assert version == "test-2026-08-23"


def test_the_same_tokens_off_peak_price_at_one_fifth():
    # input   500_000 / 1_000_000 * 0.088 = 0.044
    # output  200_000 / 1_000_000 * 0.264 = 0.0528
    # total                                  0.0968, which is 0.484 / 5
    usd, _ = cost_usd(_call(OFF_PEAK_TUESDAY), FETCHED)
    assert usd == Decimal("0.0968")


def test_a_saturday_inside_the_peak_hours_prices_off_peak():
    """Same clock time as the peak call, same tokens, one fifth of the money."""
    # 0.044 + 0.0528 = 0.0968
    usd, _ = cost_usd(_call(PEAK_HOURS_SATURDAY), FETCHED)
    assert usd == Decimal("0.0968")


def test_the_boundary_minute_is_priced_as_peak():
    # 01:00 UTC is 03:00 CAT. 0.22 + 0.264 = 0.484
    at = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)
    usd, _ = cost_usd(_call(at), FETCHED)
    assert usd == Decimal("0.484")


def test_the_minute_before_the_boundary_is_priced_off_peak():
    # 00:59 UTC is 02:59 CAT. 0.044 + 0.0528 = 0.0968
    at = datetime(2026, 8, 25, 0, 59, tzinfo=timezone.utc)
    usd, _ = cost_usd(_call(at), FETCHED)
    assert usd == Decimal("0.0968")


def test_cache_reads_price_at_the_cache_rate_while_fresh_input_takes_the_window():
    # fresh input   500_000 / 1_000_000 * 0.44  = 0.22
    # cache read  1_000_000 / 1_000_000 * 0.088 = 0.088
    # output      none
    # total                                       0.308
    call = _call(PEAK_TUESDAY, output_tokens=0, cache_read_tokens=1_000_000)
    usd, _ = cost_usd(call, FETCHED)
    assert usd == Decimal("0.308")


def test_the_cache_rate_does_not_move_between_windows_while_fresh_input_does():
    # fresh input   500_000 / 1_000_000 * 0.088 = 0.044   (a fifth of 0.22)
    # cache read  1_000_000 / 1_000_000 * 0.088 = 0.088   (unchanged)
    # total                                       0.132
    call = _call(OFF_PEAK_TUESDAY, output_tokens=0, cache_read_tokens=1_000_000)
    usd, _ = cost_usd(call, FETCHED)
    assert usd == Decimal("0.132")


def test_cache_creation_takes_the_fresh_input_rate_for_its_window():
    # cache creation 400_000 / 1_000_000 * 0.44 = 0.176
    call = _call(PEAK_TUESDAY, input_tokens=0, output_tokens=0, cache_creation_tokens=400_000)
    usd, _ = cost_usd(call, FETCHED)
    assert usd == Decimal("0.176")


def test_a_call_that_spent_nothing_costs_nothing():
    """Four zero counts is a real reading, and zero is its honest price."""
    call = _call(PEAK_TUESDAY, input_tokens=0, output_tokens=0)
    usd, _ = cost_usd(call, FETCHED)
    assert usd == Decimal("0")


# ---------------------------------------------------------------------------
# The acceptance criterion: a book change re-prices history at read time
# ---------------------------------------------------------------------------


def test_a_new_book_re_prices_the_same_stored_call():
    """#46 AC. One call object, two books, two figures, and no stored money anywhere."""
    call = _call(PEAK_TUESDAY)

    # old book: 0.22 + 0.264 = 0.484
    old_usd, old_version = cost_usd(call, FETCHED)
    # new book: 500_000 / 1_000_000 * 0.50 = 0.25
    #           200_000 / 1_000_000 * 1.50 = 0.30
    #           total                        0.55
    raised = _book("test-2026-09-01", _rates("0.50", "1.50", "0.10", "0.30", "0.10"))
    new_usd, new_version = cost_usd(call, raised)

    assert (old_usd, old_version) == (Decimal("0.484"), "test-2026-08-23")
    assert (new_usd, new_version) == (Decimal("0.55"), "test-2026-09-01")


def test_no_money_field_reaches_the_call_after_pricing():
    """Pricing reads the record. It never writes one, so history stays re-priceable."""
    call = _call(PEAK_TUESDAY)
    cost_usd(call, FETCHED)
    assert not [name for name in vars(call) if "cost" in name or "usd" in name or "zar" in name]


# ---------------------------------------------------------------------------
# Unknown provider or model raises. A silent zero is a free model call.
# ---------------------------------------------------------------------------


def test_an_unknown_provider_raises():
    with pytest.raises(UnknownPrice, match="openai"):
        cost_usd(_call(PEAK_TUESDAY, provider="openai"), FETCHED)


def test_an_unknown_model_raises():
    with pytest.raises(UnknownPrice, match="deepseek-v9-nova"):
        cost_usd(_call(PEAK_TUESDAY, served_model="deepseek-v9-nova"), FETCHED)


def test_an_unknown_model_raises_even_when_the_call_spent_no_tokens():
    """Otherwise the one call that skips every lookup is the one that reports zero."""
    call = _call(PEAK_TUESDAY, served_model="deepseek-v9-nova", input_tokens=0, output_tokens=0)
    with pytest.raises(UnknownPrice):
        cost_usd(call, FETCHED)


def test_a_missing_rate_for_a_kind_that_was_spent_raises():
    """A book with no cache row cannot price a call that read cache."""
    thin = dict(FETCHED.rates_per_million)
    del thin[(PROVIDER, MODEL, TokenKind.CACHE_READ, Window.PEAK)]
    call = _call(PEAK_TUESDAY, cache_read_tokens=1_000)
    with pytest.raises(UnknownPrice, match="cache_read"):
        cost_usd(call, _book("test-thin", thin))


def test_a_missing_rate_for_a_kind_that_was_not_spent_is_not_reached():
    """Zero cache tokens cost zero under any book, so the absent row is no obstacle."""
    thin = dict(FETCHED.rates_per_million)
    del thin[(PROVIDER, MODEL, TokenKind.CACHE_READ, Window.PEAK)]
    usd, _ = cost_usd(_call(PEAK_TUESDAY), _book("test-thin", thin))
    assert usd == Decimal("0.484")


def test_unknown_price_is_a_lookup_error():
    assert issubclass(UnknownPrice, LookupError)


# ---------------------------------------------------------------------------
# cost_zar: the dated rate on or before the call, applied at read time
# ---------------------------------------------------------------------------

EARLY_RATE = FxRate(usd_zar=Decimal("16.0237"), as_of=date(2026, 8, 24), source="test rate")
LATE_RATE = FxRate(usd_zar=Decimal("17.5000"), as_of=date(2026, 8, 30), source="test rate")
TWO_RATES = (EARLY_RATE, LATE_RATE)


def test_a_rate_dated_after_the_call_is_not_used():
    """The call is on the 25th, so the rate published on the 30th does not exist yet."""
    # usd 0.484 * 16.0237 = 7.7554708
    zar, fx_version = cost_zar(_call(PEAK_TUESDAY), book=FETCHED, rates=TWO_RATES)
    assert zar == Decimal("7.7554708")
    assert fx_version == "usd_zar-2026-08-24"


def test_the_latest_rate_on_or_before_the_call_is_the_one_applied():
    """2026-08-31 is a Monday, so the tokens still price at peak, against the newer rate."""
    # usd 0.484 * 17.5 = 8.47
    at = datetime(2026, 8, 31, 7, 30, tzinfo=timezone.utc)
    zar, fx_version = cost_zar(_call(at), book=FETCHED, rates=TWO_RATES)
    assert zar == Decimal("8.47")
    assert fx_version == "usd_zar-2026-08-30"


def test_a_rate_change_re_prices_the_same_stored_call():
    """The fx analogue of the book AC. One call, two tables, two figures."""
    call = _call(PEAK_TUESDAY)
    # 0.484 * 16.0237 = 7.7554708
    old_zar, old_version = cost_zar(call, book=FETCHED, rates=(EARLY_RATE,))
    # 0.484 * 19.0    = 9.196
    corrected = FxRate(usd_zar=Decimal("19.0000"), as_of=date(2026, 8, 24), source="test correction")
    new_zar, new_version = cost_zar(call, book=FETCHED, rates=(corrected,))

    assert old_zar == Decimal("7.7554708")
    assert new_zar == Decimal("9.196")
    assert old_version == new_version == "usd_zar-2026-08-24"


def test_the_rate_is_chosen_by_the_cat_date_because_every_report_shows_cat():
    """23:00 UTC on the 24th is 01:00 CAT on the 25th, so the 25th rate applies."""
    # 01:00 CAT is before the 03:00 window, so off peak: 0.044 + 0.0528 = 0.0968
    # 0.0968 * 17.5 = 1.694
    at = datetime(2026, 8, 24, 23, 0, tzinfo=timezone.utc)
    on_the_25th = FxRate(usd_zar=Decimal("17.5000"), as_of=date(2026, 8, 25), source="test rate")
    zar, fx_version = cost_zar(_call(at), book=FETCHED, rates=(EARLY_RATE, on_the_25th))
    assert zar == Decimal("1.694")
    assert fx_version == "usd_zar-2026-08-25"


def test_a_call_older_than_every_rate_raises():
    """Reporting a rand figure against a rate that did not exist yet is a made up number."""
    at = datetime(2026, 8, 20, 7, 30, tzinfo=timezone.utc)
    with pytest.raises(UnknownFxRate, match="2026-08-20"):
        cost_zar(_call(at), book=FETCHED, rates=TWO_RATES)


def test_an_empty_rate_table_raises():
    with pytest.raises(UnknownFxRate):
        cost_zar(_call(PEAK_TUESDAY), book=FETCHED, rates=())


def test_unknown_fx_rate_is_a_lookup_error():
    assert issubclass(UnknownFxRate, LookupError)


# ---------------------------------------------------------------------------
# The seeded tables: what the repo ships today
# ---------------------------------------------------------------------------


def test_the_seeded_book_carries_the_fetched_deepseek_tariff():
    """0.44 in and 1.32 out per million at peak, the 2026-08-23 figures."""
    # 500_000 / 1_000_000 * 0.44 = 0.22
    # 200_000 / 1_000_000 * 1.32 = 0.264
    usd, version = cost_usd(_call(PEAK_TUESDAY))
    assert usd == Decimal("0.484")
    assert version == PRICE_BOOK.price_version


def test_the_seeded_book_prices_the_same_call_at_a_fifth_off_peak():
    # 0.044 + 0.0528 = 0.0968
    usd, _ = cost_usd(_call(OFF_PEAK_TUESDAY))
    assert usd == Decimal("0.0968")


def test_the_seeded_book_declares_cat_and_its_windows():
    assert PRICE_BOOK.utc_offset_hours == 2
    assert PRICE_BOOK.peak_windows_cat == ((3, 6), (8, 12))
    assert PRICE_BOOK.peak_weekdays == (0, 1, 2, 3, 4)


def test_the_seeded_book_version_is_dated():
    assert PRICE_BOOK.price_version.startswith("2026-")


def test_the_seeded_fx_table_has_a_rate_a_date_and_a_named_source():
    assert FX_RATES
    for rate in FX_RATES:
        assert rate.usd_zar > 0
        assert isinstance(rate.as_of, date)
        assert rate.source.strip()


def test_the_seeded_fx_table_prices_a_recent_call_in_rand():
    """The same call the two tests above price at $0.484, in rand, by hand.

    0.484 x 16.0237 = 7.7554708. Deriving the expected value from cost_usd would
    compare the implementation against itself and stay green through a dropped
    multiplication.
    """
    zar, fx_version = cost_zar(_call(PEAK_TUESDAY))
    assert zar == Decimal("7.7554708")
    assert fx_version == "usd_zar-2026-08-24"
