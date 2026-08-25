"""Money read off a ModelCall, never stored on one (ticket #46, issue #22).

Tokens are the fact. Dollars and rand are readings of that fact against tables
that change, so both are computed at read time by the pure functions here and
neither is a column anywhere. `cost_usd` and `cost_zar` return the figure and the
version of the table that produced it, so a report can always say which book it
was priced against, and a corrected book re-prices every historical call for free.

THE PRICE BOOK KEY
    (provider, served_model, token_kind, window) -> USD per million tokens.

    The four token counts a provider reports already name both the direction and
    the cache state of every token it charged for: fresh input, output, cache
    read, cache creation. So the cache state that #22 named as part of the key is
    the token kind, one lookup per count, and pricing is a sum over four rows with
    no branching. The window is the fifth part of the key, derived from the call's
    hour rather than stored, because a call knows when it happened and the tariff
    knows which hours are dear.

    A provider that charges one figure all day sits in a second table keyed
    (provider, served_model, token_kind), and a model appears in one table or the
    other. Writing a flat tariff as two identical windowed rows would let the two
    drift apart under a later edit, and a reader could not say which of the two is
    the published number. `cost_usd` reads the same `window_for` answer either way
    and a flat model simply never consults it.

CAT, AND WHY THE OFFSET IS IN THE DATA
    DeepSeek's peak windows are 03:00 to 06:00 and 08:00 to 12:00 CAT, Monday to
    Friday. CAT is UTC+2 and observes no daylight saving, ever, so the conversion
    is a fixed two hours all year and a window boundary is exact to the minute.
    The book carries `utc_offset_hours` rather than trusting a reader to remember
    it, and every rollup and report shows CAT for the same reason: a South African
    business morning is what the second window is for, and a report in UTC moves
    it. A window is half open, so 12:00 CAT is already off peak.

    The weekday rule is separate from the hour rule. A Saturday at 09:30 CAT sits
    inside the peak hours and prices off peak.

WHAT THE SEEDED BOOK KNOWS AND WHAT IT REFUSES
    Two models. DeepSeek V4 Flash by window, at the figures fetched 2026-08-23,
    and OpenAI `gpt-5.6-luna` flat, at the figures decision #34 verified the same
    day. A call naming any other model raises `UnknownPrice`. It never returns
    zero, because a silent zero is a free model call in every report that reads
    it, and the Harness run that started this ticket was unpriceable in exactly
    that way.

    `price_version` stays `2026-08-23.1` across the Luna addition. Both tariffs
    were verified on that date, and every call the book already priced prices
    identically, so the version still names one set of figures. A version bump is
    for a figure that changed, which re-prices history.

    `deepseek-v4-pro` is the documented mapping for `claude-opus` and is
    deliberately absent. Its tariff has not been fetched, so a call that reaches it
    fails loudly rather than being priced from a guess.

    DeepSeek cache creation takes the fresh input rate for its window. The fetched
    tariff names input, output, the off-peak fifth and the cache-read fifth, and
    states no write premium, so the row records that reading rather than inventing
    one. Luna's two cache rows carry their own reasoning beside them, and one of
    them overcounts on purpose.

THE RAND
    Providers bill in USD, so the book stays USD and rand is a second derived
    figure (owner, 2026-08-23). `FX_RATES` is a dated table of (usd_zar, as_of,
    source) rows, updated by a task that records where the number came from.
    Nothing in this module fetches anything. `cost_zar` applies the latest rate
    whose `as_of` is on or before the call's CAT date, and a call older than every
    row raises rather than borrowing a rate that did not exist yet.

    `fx_version` is derived from the row's date, so a corrected rate for a day
    replaces the row rather than being appended beside it.

NO ROUNDING
    The functions return exact `Decimal`s. A single judge call costs a small
    fraction of a cent, and rounding each row to cents before summing a day would
    report a busy tenant as free. The report layer rounds what it prints.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. `app.domain.model_call` is a sibling.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum

from app.domain.model_call import ModelCall

CAT_UTC_OFFSET_HOURS = 2
CAT = timezone(timedelta(hours=CAT_UTC_OFFSET_HOURS), "CAT")

PER_MILLION = Decimal(1_000_000)


class Window(StrEnum):
    """Which half of the tariff an hour falls in."""

    PEAK = "peak"
    OFF_PEAK = "off_peak"


class TokenKind(StrEnum):
    """The four counts a provider reports, each priced by its own row.

    INPUT and OUTPUT are the direction. CACHE_READ and CACHE_CREATION are the
    cache state of input tokens, which providers bill apart from fresh input.
    """

    INPUT = "input"
    OUTPUT = "output"
    CACHE_READ = "cache_read"
    CACHE_CREATION = "cache_creation"


# Each ModelCall count, and the row it is priced by.
_KIND_FIELDS = (
    (TokenKind.INPUT, "input_tokens"),
    (TokenKind.OUTPUT, "output_tokens"),
    (TokenKind.CACHE_READ, "cache_read_tokens"),
    (TokenKind.CACHE_CREATION, "cache_creation_tokens"),
)

PriceKey = tuple[str, str, TokenKind, Window]
# A provider that publishes one figure per kind and no time-of-day tariff is keyed
# without a window. Spelling such a model out as two identical windowed rows lets
# the two drift apart under a later edit, and leaves a reader unable to say which
# of the two is the published tariff.
FlatPriceKey = tuple[str, str, TokenKind]


class UnknownPrice(LookupError):
    """The book prices no such provider, model or token kind.

    A LookupError, because a missing key is what it is. It is raised rather than
    answered with zero. A zero would report a model call as free, which is the
    failure this ticket exists to end.
    """


class UnknownFxRate(LookupError):
    """No usd_zar rate on or before the call's CAT date."""


@dataclass(frozen=True)
class PriceBook:
    """One published tariff, versioned, with the windows it declares.

    Args:
        price_version:     dated identifier, carried out with every figure so a
                           report says which book priced it. A same-day correction
                           takes the next suffix.
        utc_offset_hours:  the offset the windows below are declared in. CAT is 2,
                           with no daylight saving.
        peak_windows_cat:  half-open (opens, closes) hour pairs in that zone.
        peak_weekdays:     weekday numbers the windows apply on, Monday is 0.
        rates_per_million: (provider, served_model, kind, window) -> USD per
                           million tokens, for a provider that charges by the hour.
        flat_rates_per_million: (provider, served_model, kind) -> USD per million
                           tokens, for a provider that charges one figure all day.
                           A model appears in one table or the other, never both.
    """

    price_version: str
    utc_offset_hours: int
    peak_windows_cat: tuple[tuple[int, int], ...]
    peak_weekdays: tuple[int, ...]
    rates_per_million: Mapping[PriceKey, Decimal]
    flat_rates_per_million: Mapping[FlatPriceKey, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class FxRate:
    """One day's rand rate, and where the number came from.

    Args:
        usd_zar: rand per US dollar.
        as_of:   the CAT date the rate is published for.
        source:  who published it. A rate nobody can trace is a rate nobody can
                 check against an invoice.
    """

    usd_zar: Decimal
    as_of: date
    source: str

    @property
    def fx_version(self) -> str:
        """The identifier a rollup stores beside the rand figure."""
        return f"usd_zar-{self.as_of.isoformat()}"


# DeepSeek V4 Flash, fetched 2026-08-23. Peak is $0.44 per million in and $1.32
# per million out. Off peak and cache reads are a fifth of those.
_DEEPSEEK = "deepseek"
_V4_FLASH = "deepseek-v4-flash"

# OpenAI gpt-5.6-luna, verified 2026-08-23 with decision #34. $0.20 per million in
# and $1.20 per million out, published as one figure with no time-of-day tariff.
_OPENAI = "openai"
_LUNA = "gpt-5.6-luna"

PRICE_BOOK = PriceBook(
    price_version="2026-08-23.1",
    utc_offset_hours=CAT_UTC_OFFSET_HOURS,
    peak_windows_cat=((3, 6), (8, 12)),
    peak_weekdays=(0, 1, 2, 3, 4),
    rates_per_million={
        (_DEEPSEEK, _V4_FLASH, TokenKind.INPUT, Window.PEAK): Decimal("0.44"),
        (_DEEPSEEK, _V4_FLASH, TokenKind.OUTPUT, Window.PEAK): Decimal("1.32"),
        # No published write premium, so a written token costs what a fresh one costs.
        (_DEEPSEEK, _V4_FLASH, TokenKind.CACHE_CREATION, Window.PEAK): Decimal("0.44"),
        (_DEEPSEEK, _V4_FLASH, TokenKind.CACHE_READ, Window.PEAK): Decimal("0.088"),
        (_DEEPSEEK, _V4_FLASH, TokenKind.INPUT, Window.OFF_PEAK): Decimal("0.088"),
        (_DEEPSEEK, _V4_FLASH, TokenKind.OUTPUT, Window.OFF_PEAK): Decimal("0.264"),
        (_DEEPSEEK, _V4_FLASH, TokenKind.CACHE_CREATION, Window.OFF_PEAK): Decimal("0.088"),
        # A cache read is already at the floor, so the window does not move it.
        (_DEEPSEEK, _V4_FLASH, TokenKind.CACHE_READ, Window.OFF_PEAK): Decimal("0.088"),
    },
    flat_rates_per_million={
        (_OPENAI, _LUNA, TokenKind.INPUT): Decimal("0.20"),
        (_OPENAI, _LUNA, TokenKind.OUTPUT): Decimal("1.20"),
        # DELIBERATE OVERCOUNT. OpenAI serves cached input at a discount and no
        # figure for Luna has been verified, so a cache read is charged the full
        # input rate. The error runs one way only. A report reads high, never low,
        # and the $20 cap trips early rather than late. Replace this row with the
        # verified tariff and every historical call re-prices for free.
        (_OPENAI, _LUNA, TokenKind.CACHE_READ): Decimal("0.20"),
        # Automatic prompt caching bills nothing to write, so zero is the published
        # tariff rather than a missing row. The hook records zero cache-creation
        # tokens for this provider as well, so this row can only ever yield zero.
        (_OPENAI, _LUNA, TokenKind.CACHE_CREATION): Decimal("0"),
    },
)

# Repo data, updated by a task that records its source. Nothing here fetches.
FX_RATES: tuple[FxRate, ...] = (
    FxRate(
        usd_zar=Decimal("16.0237"),
        as_of=date(2026, 8, 24),
        # The SARB site refused the connection on 2026-08-25, so this is the
        # market close for that day rather than the SARB published daily rate.
        # The first rollup replaces it with a rate read from resbank.co.za.
        source="tradingeconomics.com USD/ZAR close, read 2026-08-25",
    ),
)


def window_for(at: datetime, book: PriceBook = PRICE_BOOK) -> Window:
    """Which window a call falls in, read from its hour and weekday in the book's zone.

    Args:
        at:   an aware datetime. ModelCall guarantees one.
        book: the tariff whose windows and weekdays apply.
    """
    local = at.astimezone(timezone(timedelta(hours=book.utc_offset_hours)))
    if local.weekday() not in book.peak_weekdays:
        return Window.OFF_PEAK
    for opens, closes in book.peak_windows_cat:
        if opens <= local.hour < closes:
            return Window.PEAK
    return Window.OFF_PEAK


def _names_model(rows: Mapping, provider: str, served_model: str) -> bool:
    """True when a rate table holds any row for this provider and model."""
    return any(key[0] == provider and key[1] == served_model for key in rows)


def _require_priced_model(book: PriceBook, provider: str, served_model: str) -> None:
    """Fail on a model the book does not price, before any count is read.

    Checked up front so that a call which spent nothing is refused too. Skipping
    the lookup for zero counts would make the one call with no tokens the one call
    that reports a clean zero for an unpriceable model.
    """
    if _names_model(book.flat_rates_per_million, provider, served_model):
        return
    if _names_model(book.rates_per_million, provider, served_model):
        return
    raise UnknownPrice(
        f"Price book {book.price_version} prices no {served_model!r} from {provider!r}. "
        "A model call is never free, so this raises instead of reporting zero."
    )


def _rate(book: PriceBook, call: ModelCall, kind: TokenKind, window: Window) -> Decimal:
    """One kind's rate. A flat model is stated once and the window never reaches it."""
    flat_key = (call.provider, call.served_model, kind)
    if _names_model(book.flat_rates_per_million, call.provider, call.served_model):
        try:
            return book.flat_rates_per_million[flat_key]
        except KeyError:
            raise UnknownPrice(
                f"Price book {book.price_version} prices no {kind} tokens for "
                f"{call.served_model!r} from {call.provider!r}. That model is flat, "
                "so no window supplies a second row to fall back on."
            ) from None
    try:
        return book.rates_per_million[(*flat_key, window)]
    except KeyError:
        raise UnknownPrice(
            f"Price book {book.price_version} prices no {kind} tokens for "
            f"{call.served_model!r} from {call.provider!r} in the {window} window."
        ) from None


def cost_usd(call: ModelCall, book: PriceBook = PRICE_BOOK) -> tuple[Decimal, str]:
    """What one call cost in USD, and the version of the book that says so.

    Args:
        call: the ledger row. Its `at` decides the window.
        book: the tariff to price against. Passing a different one re-prices a
              historical call, which is the whole point of storing no money.

    Returns:
        (exact Decimal dollars, price_version).

    Raises:
        UnknownPrice: the book prices no such provider or model, or no row exists
                      for a kind this call actually spent tokens on.
    """
    _require_priced_model(book, call.provider, call.served_model)
    window = window_for(call.at, book)
    total = Decimal(0)
    for kind, count_field in _KIND_FIELDS:
        tokens = getattr(call, count_field)
        if tokens == 0:
            continue
        total += _rate(book, call, kind, window) * tokens / PER_MILLION
    return total, book.price_version


def rate_for(at: datetime, rates: Sequence[FxRate] = FX_RATES) -> FxRate:
    """The latest rate published on or before the call's CAT date.

    The CAT date, not the UTC one, because every report and rollup shows CAT and a
    call just after midnight CAT belongs to the South African day it happened on.

    Raises:
        UnknownFxRate: the call predates every row. Applying a later rate would be
                       reporting a rand figure that did not exist at the time.
    """
    on = at.astimezone(CAT).date()
    published = [rate for rate in rates if rate.as_of <= on]
    if not published:
        raise UnknownFxRate(
            f"No usd_zar rate published on or before {on.isoformat()} in CAT. "
            "The fx table starts later than this call."
        )
    return max(published, key=lambda rate: rate.as_of)


def cost_zar(
    call: ModelCall,
    book: PriceBook = PRICE_BOOK,
    rates: Sequence[FxRate] = FX_RATES,
) -> tuple[Decimal, str]:
    """What one call cost in rand, and the version of the rate that says so.

    Returns:
        (exact Decimal rand, fx_version).

    Raises:
        UnknownPrice:  as cost_usd.
        UnknownFxRate: no rate on or before the call's CAT date.
    """
    usd, _ = cost_usd(call, book)
    rate = rate_for(call.at, rates)
    return usd * rate.usd_zar, rate.fx_version
