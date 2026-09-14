"""One embedding call as a `model_calls` row (issue #265).

WHY THIS IS NOT THE HOOK IN model_client
    `attach_ledger_hook` sits on the httpx client every chat call passes through,
    and it reads `usage` off the wire. Neither embedding provider goes near it.
    The Voyage SDK owns its own transport, and Bedrock arrives through boto3. So
    the two embedding services call this module where the response is in hand,
    which is the only place the token count exists.

TWO PURPOSES, NOT ONE
    `embed_query` is what a customer turn spends on retrieval. `embed_document`
    is what an ingest spends on a corpus. One `embedding` purpose would add them
    together in `tenant_usage_daily` and in `GET /agents/{id}/usage`, and the two
    questions the ledger is read for are exactly "what does a turn cost" and
    "what did ingesting this corpus cost". They are derived from the `input_type`
    the services already pass, so the distinction is never spelled twice.

WHY THESE PURPOSES ARE NOT IN PURPOSE_ROUTES
    A `ModelRoute` is a chat completion's provider, model and reasoning effort,
    and `route_for` feeds `make_client`, which builds an OpenAI or Anthropic chat
    SDK. An embedding reaches neither. Its provider comes from
    `settings.EMBEDDING_PROVIDER` and its model from `EMBEDDING_MODEL` or
    `BEDROCK_EMBED_MODEL_ID`. A row in that table would name `gpt-5.6-luna` for a
    call that never asks it anything. `EMBEDDING_PURPOSES` is the separate table,
    and `tests/unit/test_embedding_ledger.py` pins the two as disjoint.

WHY served_model IS `unreported`
    Neither response names a model. `.venv/Lib/site-packages/voyageai/object/
    embeddings.py` builds `EmbeddingsObject` out of two attributes, `embeddings`
    and `total_tokens`, and keeps nothing else the body carried. The Titan
    `invoke_model` body carries `embedding` and `inputTextTokenCount`. So the row
    holds the model the call site asked for, and `unreported` says no provider
    stated it, which is what `served_model_for` already labels a silent body.

WHY total_tokens IS ONE REQUEST'S FIGURE AND NOT A RUNNING TOTAL
    `EmbeddingsObject.update` does `self.total_tokens += response.usage.total_tokens`,
    which reads like an accumulator across calls. It is not.
    `.venv/Lib/site-packages/voyageai/client.py:91` constructs a fresh
    `EmbeddingsObject(response)` for every `embed` call, so the object a caller
    holds has accumulated exactly one response. One Voyage request is therefore
    one row carrying that request's whole token count, and nothing has to
    subtract a previous call's figure.

WHY A MISSING TOKEN COUNT WRITES NO ROW
    `input_tokens=None` means the provider reported no count. A row of zero would
    read as a free call in every rollup, and the whole point of `UnknownPrice` and
    of `usage_service`'s null costs is that missing data is never passing data.
    The gap is logged by name instead, the treatment `model_client` gives a usage
    block it cannot parse.

Rung: `app.services` imports `app.core`, `app.domain`, `app.models` and its
siblings. This module imports `app.core` and `app.domain` only.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from app.core.log_bounds import log_failure
from app.core.model_client import LedgerContext
from app.domain.model_call import ModelCall, ModelSource

log = structlog.get_logger(__name__)

#: The two purposes an embedding row is filed under.
EMBED_QUERY = "embed_query"
EMBED_DOCUMENT = "embed_document"

#: Every purpose this module writes, spelled out so a rollup reader and
#: `PURPOSE_ROUTES` can be checked against each other.
EMBEDDING_PURPOSES = frozenset({EMBED_QUERY, EMBED_DOCUMENT})

#: The price book's names for who serves an embedding call.
VOYAGE_PROVIDER = "voyage"
BEDROCK_PROVIDER = "bedrock"

#: `input_type` as the services already spell it, and the purpose it files under.
_PURPOSE_BY_INPUT_TYPE = {"query": EMBED_QUERY, "document": EMBED_DOCUMENT}


def purpose_for(input_type: str) -> str:
    """The ledger purpose for one `input_type`.

    Args:
        input_type: "query" or "document", the two values `embed_texts` takes.

    Raises:
        ValueError: any other value. Defaulting would file a retrieval call as an
                    ingest one, and the rollup that separates them is the reason
                    there are two purposes.
    """
    try:
        return _PURPOSE_BY_INPUT_TYPE[input_type]
    except KeyError:
        raise ValueError(
            f"No embedding purpose for input_type {input_type!r}. "
            f"The two are {sorted(_PURPOSE_BY_INPUT_TYPE)}."
        ) from None


def _embedding_row(
    ledger: LedgerContext,
    purpose: str,
    provider: str,
    model: str,
    input_tokens: int,
    at: datetime | None,
) -> ModelCall:
    """One embedding call as a ledger row. Zero for every count but input."""
    return ModelCall(
        purpose=purpose,
        provider=provider,
        requested_model=model,
        served_model=model,
        model_source=ModelSource.UNREPORTED,
        input_tokens=input_tokens,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        at=at or datetime.now(timezone.utc),
        tenant_id=ledger.tenant_id,
        agent_id=ledger.agent_id,
        job_id=ledger.job_id,
    )


def _log_tokens_unreported(
    ledger: LedgerContext, purpose: str, provider: str, model: str
) -> None:
    """Name a call the ledger will not hold, the way every other gap is named."""
    log.warning(
        "embedding_ledger.tokens_unreported",
        purpose=purpose,
        provider=provider,
        requested_model=model,
        tenant_id=ledger.tenant_id,
        agent_id=ledger.agent_id,
        job_id=ledger.job_id,
    )


def record_embedding(
    ledger: LedgerContext,
    *,
    purpose: str,
    provider: str,
    model: str,
    input_tokens: int | None,
    at: datetime | None = None,
) -> None:
    """Write one embedding call to the tenant ledger. Never raises.

    Fail open, for the reason the chat hook is. A customer's retrieval and a
    tenant's ingest do not die because telemetry could not be written. That makes
    a quiet ledger a real failure mode, and `embedding_ledger.record_failed` is
    the only line that says so.

    Args:
        purpose:      `EMBED_QUERY` or `EMBED_DOCUMENT`.
        provider:     `VOYAGE_PROVIDER` or `BEDROCK_PROVIDER`.
        model:        the model id the call site asked for, which is also what the
                      row carries as served. See WHY served_model IS `unreported`.
        input_tokens: what the provider reported, or None when it reported none.
                      None writes no row and logs the gap.
        at:           when the call happened. Defaults to now, injected by a test
                      that needs a known price window.
    """
    if input_tokens is None:
        _log_tokens_unreported(ledger, purpose, provider, model)
        return
    try:
        ledger.recorder(_embedding_row(ledger, purpose, provider, model, input_tokens, at))
    except Exception as exc:
        log_failure(
            log, "embedding_ledger.record_failed", exc, level="error",
            purpose=purpose,
            provider=provider,
            tenant_id=ledger.tenant_id,
            agent_id=ledger.agent_id,
            job_id=ledger.job_id,
        )
