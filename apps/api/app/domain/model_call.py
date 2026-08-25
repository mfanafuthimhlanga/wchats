"""ModelCall, what one call to a model spent (ticket #46, issue #22).

WHY THIS RECORD EXISTS
    Twelve call sites in `apps/api/app` build a client and read the text back.
    None of them reads `usage` or `model`, so the first Harness run could not be
    priced. `config.model_id` held the Anthropic alias, and `turn_metrics.cost_usd`
    carried the CLI's Anthropic-book figure for calls that DeepSeek served.

    One call yields one of these. The tokens are the fact the provider reported.
    Money is not a field here at all, because money is derived from these numbers
    at read time by `app.domain.pricing`, against a versioned book. A stored cost
    freezes yesterday's price into a row that a corrected book can never reach.

WHY CONSTRUCTION IS LOUD
    Every field feeds a rollup that a tenant sees. Three shapes would poison one
    quietly, so each is refused here rather than at whichever reader notices first.

        a naive `at`     names no instant. Pricing reads the window off a
                         conversion to CAT, and a conversion that starts from an
                         assumed offset moves a call into the wrong window
        a negative count subtracts spend from a tenant's day
        an empty id      is a row nobody is billed for

    `at` is normalised to UTC, so every row in the ledger is comparable and the
    CAT conversion always starts from a known offset. The column is `timestamptz`.

WHY model_source IS AN ENUM AND NOT A FLAG
    Three states, each a different confidence in the served name.

        reported        the response named the model that ran
        mapped_by_docs  the response echoed the requested alias, and the provider's
                        published mapping supplied the served name (DeepSeek maps
                        `claude-haiku` and `claude-sonnet` to `deepseek-v4-flash`,
                        `claude-opus` to `deepseek-v4-pro`)
        unreported      the response carried no `model` field at all, so
                        served_model holds the requested name and no provider ever
                        stated it

    A report that mixes the three without saying which is which cannot be audited.
    `unreported` exists because both other labels would credit a provenance the
    silent body never gave.

WHAT IS NOT HERE
    No connection string, and no field that could hold one (project rule 1). No
    money, no price version, no fx version. Those belong to the read.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

_REQUIRED_TEXT = ("purpose", "provider", "requested_model", "served_model", "tenant_id")
_TOKEN_COUNTS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
_OPTIONAL_IDS = ("agent_id", "job_id")


class ModelSource(StrEnum):
    """How the served model was established for one call.

    REPORTED:       the provider's response named it.
    MAPPED_BY_DOCS: the response echoed the requested alias, and the provider's
                    published mapping named it.
    UNREPORTED:     the response named no model, so served_model carries the
                    requested name and no provider stated what ran.
    """

    REPORTED = "reported"
    MAPPED_BY_DOCS = "mapped_by_docs"
    UNREPORTED = "unreported"


class InvalidModelCall(ValueError):
    """A ledger row that would price wrong, refused at construction.

    A ValueError, so callers that already catch ValueError keep catching it, the
    same choice `InvalidJobDict` made.
    """


def _require_text(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidModelCall(f"ModelCall needs a {name}, got {value!r}")


def _require_optional_id(name: str, value: Any) -> None:
    """None says there is no such id. An empty string says there is one and hides it."""
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise InvalidModelCall(f"ModelCall needs {name} as a non-empty string or None, got {value!r}")


def _require_token_count(name: str, value: Any) -> None:
    # bool first: True is an int in Python and would price as one token.
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidModelCall(f"ModelCall needs {name} as an int, got {type(value).__name__}")
    if value < 0:
        raise InvalidModelCall(f"ModelCall needs {name} at zero or above, got {value}")


def _as_model_source(value: Any) -> ModelSource:
    if isinstance(value, ModelSource):
        return value
    try:
        return ModelSource(value)
    except ValueError:
        raise InvalidModelCall(
            f"ModelCall needs model_source as one of {[m.value for m in ModelSource]}, got {value!r}"
        ) from None


def _as_utc(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidModelCall(f"ModelCall needs `at` as a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidModelCall(
            f"ModelCall needs `at` aware. A naive {value.isoformat()} names no instant, "
            "so it cannot be placed in a price window."
        )
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ModelCall:
    """One call to a model, as the response hook and the SDK path both record it.

    Args:
        purpose:               what the call was for, the key the rollup groups by
                               (`judge`, `agent_turn`, `attacker`, `scenario_gen`).
        provider:              who served it, as the price book names them.
        requested_model:       the model id the call site asked for.
        served_model:          the model that ran, as the price book names it.
        model_source:          how served_model was established. A ModelSource, or
                               its string value, which is how a stored row reads back.
        input_tokens:          fresh input tokens, the ones no cache covered.
        output_tokens:         tokens the model produced.
        cache_read_tokens:     input tokens served from cache, priced at their own rate.
        cache_creation_tokens: input tokens written into cache.
        at:                    when the call happened. Any aware datetime, held as UTC.
        tenant_id:             UUID string of the tenant billed for this call.
        agent_id:              UUID string of the agent, or None for a platform call.
        job_id:                UUID string of the job, or None. A rollup has no job.

    Raises:
        InvalidModelCall: any required field is empty, a token count is negative or
                          is not an int, model_source is unknown, or `at` is naive.
    """

    purpose: str
    provider: str
    requested_model: str
    served_model: str
    # The init input, not what the record holds. __post_init__ coerces a string
    # to the enum and a datetime in any zone to UTC.
    model_source: ModelSource | str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    at: datetime
    tenant_id: str
    agent_id: str | None = None
    job_id: str | None = None

    def __post_init__(self) -> None:
        for name in _REQUIRED_TEXT:
            _require_text(name, getattr(self, name))
        for name in _TOKEN_COUNTS:
            _require_token_count(name, getattr(self, name))
        for name in _OPTIONAL_IDS:
            _require_optional_id(name, getattr(self, name))
        # object.__setattr__ is how a frozen dataclass normalises a field.
        object.__setattr__(self, "model_source", _as_model_source(self.model_source))
        object.__setattr__(self, "at", _as_utc(self.at))
