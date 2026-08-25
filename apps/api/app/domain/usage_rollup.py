"""One day of ledger rows to one row per purpose, priced (ticket #46, issue #22).

WHAT THIS IS FOR
    `model_calls` holds one row per call and no money. `tenant_usage_daily` holds
    one row per (tenant, purpose, day) and money that names the book it came from.
    This module is the arithmetic between them, and it owns no connection, so every
    rule below is proved against calls built in memory.

MONEY IS SUMMED PER CALL, NEVER DERIVED FROM SUMMED TOKENS
    The price window is read off each call's own hour in CAT. A day holds calls in
    both windows, and the peak rate is five times the off-peak one, so pricing a
    day's summed tokens at any single rate is wrong by up to five times in either
    direction. Each call is priced by `app.domain.pricing` and the dollars are what
    get summed.

A GAP IS WRITTEN DOWN, NOT RAISED
    A model the book refuses raises `UnknownPrice` per call. Letting that reach the
    task would lose the whole rollup, including every tenant whose day priced
    cleanly. Instead the group keeps its tokens and its call count, its money goes
    to None, and `price_gaps` names the provider, the model and how many calls,
    which is what the task logs. The row that lands then shows tokens spent for no
    recorded cost, so the gap is visible in the table.

    The group loses its money ENTIRELY rather than summing the calls that did
    price. A partial sum understates the day while still reading as a real figure,
    and no column would say how much was missing.

THE DOLLARS AND THE RAND FAIL SEPARATELY
    A call whose CAT date predates every fx row raises `UnknownFxRate`, and the
    dollars for that call are still known. So the rand and `fx_version` go to None
    while `cost_usd` and `price_version` stand, and `unrated_call_count` says how
    many calls the rand is missing. One gap never hides the other.

WHY A VERSION COLUMN CAN NAME TWO BOOKS
    A UTC day runs from 02:00 CAT to 02:00 CAT the next day, so it can straddle two
    published rates. The version fields hold every version that priced the group,
    comma separated and sorted, which is one value in the ordinary case. Naming
    only one of two would attribute half the row to a book that never saw it.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. `app.domain.model_call` and `app.domain.pricing` are siblings.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain.model_call import ModelCall
from app.domain.pricing import (
    FX_RATES,
    PRICE_BOOK,
    FxRate,
    PriceBook,
    UnknownFxRate,
    UnknownPrice,
    cost_usd,
    cost_zar,
)

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)


@dataclass(frozen=True)
class PriceGap:
    """A provider and model the book refuses, and how many calls named it.

    Carried out of the rollup so the task can log a name and a number. A gap
    reported as "something did not price" is a gap nobody can go and fix.
    """

    provider: str
    served_model: str
    call_count: int


@dataclass(frozen=True)
class PurposeUsage:
    """One day of one purpose, as `tenant_usage_daily` stores it.

    The four token counts and `call_count` are summed facts. The four derived
    fields are readings of those facts, each None when the reading could not be
    taken. `price_gaps` and `unrated_call_count` say why a None is None.
    """

    purpose: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    call_count: int
    cost_usd: Decimal | None
    cost_zar: Decimal | None
    price_version: str | None
    fx_version: str | None
    price_gaps: tuple[PriceGap, ...]
    unrated_call_count: int


def _versions(names: set[str]) -> str | None:
    """Every version that priced a group, sorted, or None when none did."""
    return ",".join(sorted(names)) if names else None


def _gaps(counted: Counter) -> tuple[PriceGap, ...]:
    """The refused (provider, model) pairs as records, in a stable order."""
    return tuple(
        PriceGap(provider=provider, served_model=model, call_count=count)
        for (provider, model), count in sorted(counted.items())
    )


def _money(calls: Sequence[ModelCall], book: PriceBook, rates: Sequence[FxRate]):
    """Price every call in one group and add the figures up.

    Returns:
        (usd, zar, price versions, fx versions, refused pairs, unrated call count).
        The caller decides which of those figures a row is allowed to carry.
    """
    usd = Decimal(0)
    zar = Decimal(0)
    price_versions: set[str] = set()
    fx_versions: set[str] = set()
    refused: Counter = Counter()
    unrated = 0
    for call in calls:
        try:
            call_usd, price_version = cost_usd(call, book)
        except UnknownPrice:
            refused[(call.provider, call.served_model)] += 1
            continue
        usd += call_usd
        price_versions.add(price_version)
        try:
            call_zar, fx_version = cost_zar(call, book, rates)
        except UnknownFxRate:
            unrated += 1
            continue
        zar += call_zar
        fx_versions.add(fx_version)
    return usd, zar, price_versions, fx_versions, refused, unrated


def _usage_for(
    purpose: str,
    calls: Sequence[ModelCall],
    book: PriceBook,
    rates: Sequence[FxRate],
) -> PurposeUsage:
    """One purpose's row: the counts always, the money only when it is whole."""
    usd, zar, price_versions, fx_versions, refused, unrated = _money(calls, book, rates)
    priced = not refused
    rated = priced and not unrated
    tokens = {field: sum(getattr(call, field) for call in calls) for field in _TOKEN_FIELDS}
    return PurposeUsage(
        purpose=purpose,
        call_count=len(calls),
        cost_usd=usd if priced else None,
        cost_zar=zar if rated else None,
        price_version=_versions(price_versions) if priced else None,
        fx_version=_versions(fx_versions) if rated else None,
        price_gaps=_gaps(refused),
        unrated_call_count=unrated,
        **tokens,
    )


def roll_up(
    calls: Iterable[ModelCall],
    book: PriceBook = PRICE_BOOK,
    rates: Sequence[FxRate] = FX_RATES,
) -> tuple[PurposeUsage, ...]:
    """A day of one tenant's calls, grouped by purpose and priced.

    Args:
        calls: the day's ledger rows, in any order. The caller decides which day.
        book:  the tariff to price against. Passing a corrected book re-derives the
               day, which is what re-pricing at read time means for a rollup.
        rates: the dated usd_zar table.

    Returns:
        One PurposeUsage per purpose that appears, sorted by purpose so a re-run
        writes the same rows in the same order.
    """
    grouped: dict[str, list[ModelCall]] = {}
    for call in calls:
        grouped.setdefault(call.purpose, []).append(call)
    return tuple(_usage_for(purpose, grouped[purpose], book, rates) for purpose in sorted(grouped))
