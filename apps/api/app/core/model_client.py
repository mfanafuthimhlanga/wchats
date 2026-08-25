"""The client every direct-API site builds, where it is routed, and the row it leaves.

WHY THE HOOK IS ON THE HTTP LAYER
    Ten call sites in `apps/api/app` build a client and read the text back.
    `make_instructor_client` wraps one in `instructor.from_openai(...)`, and Ragas
    wraps that again, so a recorder attached to a wrapper method stops firing the
    moment someone adds another wrapper. `usage` and `model` arrive as bytes on
    the wire, and the httpx response hook is the one place every wrapper still
    passes through. `attach_ledger_hook` therefore takes an httpx client it did
    not construct, which is what keeps the seam intact under instructor.

TWO PROVIDERS, TWO USAGE SHAPES, ONE ROW
    Decision #34 routes every direct-API purpose to OpenAI `gpt-5.6-luna`, and
    DeepSeek's Anthropic-format endpoint still serves the SDK Agent turn until the
    owned loop lands (#48). So the hook reads both shapes and writes one
    `ModelCall` either way. Anthropic reports fresh input, output and the two
    cache counts separately. OpenAI reports `prompt_tokens` with the cached ones
    INCLUDED, so fresh input is the difference and cache creation is zero. A body
    whose `usage` matches neither shape records nothing and logs
    `model_ledger.shape_skipped`, the same treatment a stream gets, because spend
    nobody can read is a hole and not a zero.

WHERE A PURPOSE GOES
    `PURPOSE_ROUTES` is the whole routing decision as data, one row per purpose,
    and `route_for` raises on anything else. A default provider here would send a
    mistyped purpose to a model nobody chose. The SDK Agent turn is deliberately
    absent from the table, because it does not come through this factory.

    A ROUTE IS NOT YET A REDIRECT FOR THE messages SITES. `make_instructor_client`
    reads the table and builds on the provider it names. `make_client` does not:
    it takes the provider as an argument and defaults to whatever the base url
    says, which is the Anthropic-format endpoint. Nine call sites migrated in
    #47 send `messages.create` and `messages.parse` bodies and read Anthropic
    content blocks back, and `openai.OpenAI` has no `.messages` at all (checked
    against the installed openai 2.45.0). Naming their purpose is what makes each
    one countable and separable today; moving them to the route's provider is a
    rewrite of the request and the response at every one of them, and it is the
    rest of this ticket rather than a construction change.

    `make_instructor_client` applies the route's model and reasoning effort as
    instructor defaults, which a call site can still override per call. In the
    installed instructor 1.15.4,
    `.venv/Lib/site-packages/instructor/v2/core/client.py` stores the extra kwargs
    on the `Instructor`, and `handle_kwargs` fills in each one the call did not
    name. So a purpose whose route names an effort belongs to that seam, not to
    `make_client`. The OpenAI SDK takes default headers and a default query but no
    default body parameter (`.venv/Lib/site-packages/openai/_client.py`, openai
    2.45.0), so a raw client carries no effort and every call site would repeat it.

WHY THE PROVIDER SDKS ARE IMPORTED HERE AND NOWHERE ELSE
    One home for `anthropic`, `openai` and `instructor` means one place to change
    a provider, and a grep that answers "who builds a client?" honestly. The cost
    is measured. `import openai` takes about 3.3s and `import anthropic` about
    1.9s on this machine, paid once per process that reaches this module.

WHAT ONE RESPONSE BECOMES
    One `ModelCall`, frozen, holding the four token counts the provider reported,
    the model it says ran, and the four ids the call site named. No money, because
    `app.domain.pricing` derives money at read time from a versioned book. No
    connection string, because the record has no field that could hold one
    (project rule 1). The dsn reaches `record_model_call` as its own argument at
    the moment of the write.

RECORDING FAILURE IS FAIL OPEN
    A ledger insert that fails logs one structured event naming the purpose and
    the tenant, and the model call itself succeeds anyway. A customer turn does
    not die because telemetry could not be written. That is a deliberate trade,
    not an oversight, and it means a quiet ledger is a real failure mode. The
    `model_ledger.record_failed` event is the only thing that says so, so anything
    reading these rows must treat a gap as unknown rather than as zero spend.

WHICH MODEL RAN
    `served_model_for` answers it. A body naming a model other than the requested
    alias is `reported`, because the provider said so. A body echoing the alias
    falls back to the provider's published mapping and is `mapped_by_docs`, which
    carries less confidence and is labelled so a report can separate the two. A
    body carrying no `model` field is `unreported`, and served_model holds the
    requested name. Reading the mapping there would attribute a served name to a
    provider that named nothing, and the row would read as documented fact.

    OpenAI has no entry in the mapping, and needs none. The alias a call site
    sends IS the model that runs, so an echoed `gpt-5.6-luna` is `reported`.

A STREAM LEAVES A GAP, AND THE GAP SAYS SO
    A streamed body belongs to the caller, so this hook never reads one and never
    records the tokens it spent. `model_ledger.stream_skipped` names the purpose
    and the requested model at every skip, which turns a silent hole in a tenant's
    day into a line somebody can count. Parsing the stream belongs to the
    owned-loop ticket. Until it lands, this event is what makes the hole countable.

Rung: `app.core` imports the standard library, third-party packages and
`app.domain`. It imports no sibling of its own rung.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse

import anthropic
import httpx
import instructor
import openai
import psycopg2
import structlog

from app.core.config import settings
from app.domain.model_call import ModelCall, ModelSource

log = structlog.get_logger(__name__)

Recorder = Callable[[ModelCall], None]
Clock = Callable[[], datetime]
#: What `make_client` hands back. Which one depends on the provider, and both
#: carry the same ledger hook on the httpx client underneath.
ProviderClient = anthropic.Anthropic | openai.OpenAI
#: What `make_instructor_client` hands back. `AsyncInstructor` is the one Ragas
#: accepts, because `InstructorLLM.agenerate` refuses a synchronous client.
InstructorClient = instructor.Instructor | instructor.AsyncInstructor


class LedgerCursor(Protocol):
    """One statement, inside a `with`. What psycopg2 hands back from `cursor()`."""

    def execute(self, sql: str, params: tuple) -> object: ...

    def __enter__(self) -> LedgerCursor: ...

    def __exit__(self, *exc: object) -> object: ...


class LedgerConnection(Protocol):
    """The whole of what the ledger asks of an already open connection.

    A psycopg2 connection satisfies it and so does a test double, which is the
    point of naming it rather than typing the parameter `object`. Commit and close
    are absent on purpose. The caller that opened the connection still owns both.
    """

    def cursor(self) -> LedgerCursor: ...


# The alias families DeepSeek's published mapping covers, longest prefix first so
# a future `claude-haiku-5` alias cannot match a shorter entry by accident.
#
# DECISION (ticket #46, issue #22): this mapping is data here rather than a lookup
# against the provider, because nothing in this repo has yet observed which case
# reality is. DeepSeek's Anthropic-format endpoint may echo the alias, in which
# case every row reads `mapped_by_docs`, or it may name its own model, in which
# case the mapping is never consulted and every row reads `reported`. The
# re-capture ticket settles it by looking at real bodies. Until then both paths
# exist and the row says which one produced the name.
PROVIDER_MODEL_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "deepseek": (
        ("claude-haiku", "deepseek-v4-flash"),
        ("claude-sonnet", "deepseek-v4-flash"),
        ("claude-opus", "deepseek-v4-pro"),
    ),
}

_STREAMING_CONTENT_TYPE = "text/event-stream"

_COLUMNS = (
    "purpose",
    "provider",
    "requested_model",
    "served_model",
    "model_source",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "at",
    "tenant_id",
    "agent_id",
    "job_id",
)

_INSERT = (
    "INSERT INTO model_calls (id, " + ", ".join(_COLUMNS) + ") "
    "VALUES (" + ", ".join(["%s"] * (len(_COLUMNS) + 1)) + ")"
)


@dataclass(frozen=True)
class CallContext:
    """Who one client's calls are for, and who is billed for them.

    This is the whole carrier. It is passed to the factory per client instance,
    so nothing reads ambient state and a test injects what it likes. It holds no
    connection string and has no field that could hold one (project rule 1).

    Args:
        purpose:   what the calls are for, the key a rollup groups by.
        tenant_id: UUID string of the tenant billed for them.
        agent_id:  UUID string of the agent, or None for a platform call.
        job_id:    UUID string of the job, or None.
    """

    purpose: str
    tenant_id: str
    agent_id: str | None = None
    job_id: str | None = None


@dataclass(frozen=True)
class LedgerContext:
    """The ids one call site carries, and where the rows it produces go.

    `CallContext` is what a built client holds and it names a single purpose.
    A call site names its purpose itself, at the call, and what it has to be
    handed is everything else: who is billed and where the row is written. This
    is that argument, so a migrated function grew one parameter rather than four.

    It holds no connection string and has no field that could hold one (project
    rule 1). `ledger_recorder` closes over the dsn at the moment the caller binds
    it, and the dsn reaches `record_model_call` as its own argument.

    Args:
        tenant_id: UUID string of the tenant billed for these calls.
        recorder:  where each finished row goes.
        agent_id:  UUID string of the agent, or None for a platform call.
        job_id:    UUID string of the job, or None.
    """

    tenant_id: str
    recorder: Recorder
    agent_id: str | None = None
    job_id: str | None = None

    def client(self, purpose: str, **kwargs) -> ProviderClient:
        """A factory client for one purpose, billed to these ids."""
        return make_client(
            purpose,
            tenant_id=self.tenant_id,
            recorder=self.recorder,
            agent_id=self.agent_id,
            job_id=self.job_id,
            **kwargs,
        )

    def instructor_client(self, purpose: str, **kwargs) -> InstructorClient:
        """An instructor client for one purpose, billed to these ids."""
        return make_instructor_client(
            purpose,
            tenant_id=self.tenant_id,
            recorder=self.recorder,
            agent_id=self.agent_id,
            job_id=self.job_id,
            **kwargs,
        )


@dataclass(frozen=True)
class Credentials:
    """What the SDK needs to reach a provider. Never logged, never stored on a row."""

    api_key: str
    base_url: str | None = None


@dataclass(frozen=True)
class ModelRoute:
    """Where one purpose sends its calls, and how hard the model thinks.

    Args:
        provider:         the price book's name for who serves the call.
        model:            the model id the call site asks for.
        reasoning_effort: the effort literal that goes on the wire, or None to
                          send no field and take the provider default. A string
                          rather than an enum, because it is what the wire
                          carries and what `JudgeIdentity` records.
    """

    provider: str
    model: str
    reasoning_effort: str | None = None


class UnknownPurpose(LookupError):
    """No route for this purpose. Raised rather than defaulted.

    A LookupError, because a missing key is what it is. A default provider here
    would send a mistyped purpose to a model nobody chose, and the rollup would
    group its spend under a name no report expects.
    """


class UnsupportedProvider(ValueError):
    """A route names a provider this factory has no client for."""


OPENAI_PROVIDER = "openai"
LUNA_MODEL = "gpt-5.6-luna"

#: The transient failures a call site retries on, from both SDKs.
#:
#: `metadata_service` retries a rate limit and a timeout and nothing else, which
#: needs the provider's exception classes at a site the import contract now keeps
#: the provider package out of. Both SDKs' pairs are listed rather than the
#: current provider's, so a site that moves provider keeps retrying the same two
#: failures instead of quietly retrying none. Every other error stays fatal: an
#: authentication error and a schema violation both hit the same wall on retry
#: and burn budget doing it.
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    anthropic.RateLimitError,
    anthropic.APITimeoutError,
    openai.RateLimitError,
    openai.APITimeoutError,
)

# Effort `none` is the figure decision #34 priced, $0.62 per thousand turns for a
# Judge against DeepSeek's $1.23. The floor holds ONLY at effort none, and the
# decision says any increase is re-measured from the `model_calls` ledger rather
# than assumed, so the effort travels with the route and reaches `JudgeIdentity`.
# `none` is one of the seven literals the installed SDK accepts, read off
# `.venv/Lib/site-packages/openai/types/shared/reasoning_effort.py` in openai
# 2.45.0, so it is a real effort and never a stand-in for a missing value.
_JUDGE = ModelRoute(OPENAI_PROVIDER, LUNA_MODEL, reasoning_effort="none")

# No effort field at all, so the provider default applies. Sending an explicit
# null is a different request from sending nothing, and nobody has measured what
# an effort buys on these purposes.
_LUNA = ModelRoute(OPENAI_PROVIDER, LUNA_MODEL)

#: Every purpose the direct-API half calls a model for, and where it goes.
#: Decision #34 routes all of them to one provider under one DPA. The SDK
#: Agent-turn path is not here. It stays on DeepSeek until the owned loop lands
#: (#48), and it never comes through this factory.
PURPOSE_ROUTES: Mapping[str, ModelRoute] = {
    # The Ragas metrics, one purpose each, so a rollup shows which dimension
    # spent the money and a calibration figure names the Judge it measured.
    "judge_faithfulness": _JUDGE,
    "judge_answer_relevancy": _JUDGE,
    "judge_context_precision": _JUDGE,
    "judge_context_recall": _JUDGE,
    "judge_retrieval_faithfulness": _JUDGE,
    # Everything else the direct API serves.
    "scenario_generation": _LUNA,
    "metadata_enrichment": _LUNA,
    "actor_gate": _LUNA,
    "red_team_prompt": _LUNA,
    # Added by ticket #47. `red_team_prompt` is the Attacker's own turn and it
    # runs on the SDK path, so neither of the two direct-API calls the red team
    # makes had a row. They are separate purposes because they are separate
    # spends: `red_team_probe` is the persona under attack answering a probe,
    # billed once per probe, and `red_team_severity` is the judge that rates
    # what came back, billed once per reported finding.
    "red_team_probe": _LUNA,
    "red_team_severity": _LUNA,
    "query_expansion": _LUNA,
    "retrieval_strategist": _LUNA,
    "strategist": _LUNA,
    "gatekeeper": _LUNA,
    "auditor": _LUNA,
}


def route_for(purpose: str, routes: Mapping[str, ModelRoute] = PURPOSE_ROUTES) -> ModelRoute:
    """Where this purpose sends its calls.

    Args:
        purpose: the key a rollup groups by, and the key of the table above.
        routes:  the table to read. Injected by a test the way credentials are.

    Raises:
        UnknownPurpose: the table has no such row. The message lists what it does
                        hold, because a typo is the likely cause and the reader
                        needs the correct spelling in front of them.
    """
    try:
        return routes[purpose]
    except KeyError:
        raise UnknownPurpose(
            f"No model route for purpose {purpose!r}. The table routes {sorted(routes)}."
        ) from None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def provider_for_base_url(base_url: str | None) -> str:
    """The price book's name for whoever serves this endpoint.

    An unrecognised host is returned as itself rather than guessed at, so the
    price book raises `UnknownPrice` on the read instead of pricing a call from
    an assumption.
    """
    if not base_url:
        return "anthropic"
    host = urlparse(base_url).hostname or base_url
    if "deepseek" in host:
        return "deepseek"
    if "openai" in host:
        return OPENAI_PROVIDER
    if "anthropic" in host:
        return "anthropic"
    return host


def resolve_credentials(provider: str | None = None) -> Credentials:
    """The api key from Settings and the base url from `os.environ`, per provider.

    The key comes from Settings because a Celery worker started without inheriting
    `.env` has it nowhere else, which is the reason `metadata_service` already
    passes it explicitly. The base url comes from the environment because that is
    where the SDK itself looks, so resolving it here changes no endpoint a worker
    already calls.

    Args:
        provider: the price book's name for who serves the call. `openai` reads
                  the OpenAI pair. Anything else, including None, reads the
                  Anthropic pair, which is what DeepSeek's Anthropic-format
                  endpoint is configured through today.
    """
    if provider == OPENAI_PROVIDER:
        return Credentials(
            api_key=settings.OPENAI_API_KEY,
            base_url=os.environ.get("OPENAI_BASE_URL"),
        )
    return Credentials(
        api_key=settings.ANTHROPIC_API_KEY,
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )


def served_model_for(
    provider: str, requested_model: str, reported_model: str | None
) -> tuple[str, ModelSource]:
    """The model that ran, and how that name was established.

    Args:
        provider:        the price book's name for who served the call.
        requested_model: the alias the call site asked for.
        reported_model:  the `model` field of the response body, if it had one.

    Returns:
        (served model name, ModelSource). A body that named no model yields the
        requested name and UNREPORTED, because the published mapping answers an
        echoed alias and an absent field echoes nothing.
    """
    if not reported_model:
        return requested_model, ModelSource.UNREPORTED
    if reported_model != requested_model:
        return reported_model, ModelSource.REPORTED
    for prefix, served in PROVIDER_MODEL_MAP.get(provider, ()):
        if requested_model.startswith(prefix):
            return served, ModelSource.MAPPED_BY_DOCS
    return reported_model, ModelSource.REPORTED


def _anthropic_counts(usage: dict) -> dict[str, int] | None:
    """The four ledger counts from an Anthropic `usage` block, or None if this is not one."""
    if "input_tokens" not in usage and "output_tokens" not in usage:
        return None
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "cache_creation_tokens": int(usage.get("cache_creation_input_tokens") or 0),
    }


def _openai_counts(usage: dict) -> dict[str, int] | None:
    """The four ledger counts from an OpenAI `usage` block, or None if this is not one.

    Field names read off the installed SDK, `openai 2.45.0`, in
    `.venv/Lib/site-packages/openai/types/completion_usage.py`. `CompletionUsage`
    declares `prompt_tokens`, `completion_tokens`, `total_tokens` and an optional
    `prompt_tokens_details`, whose `PromptTokensDetails` declares `cached_tokens`.
    """
    if "prompt_tokens" not in usage and "completion_tokens" not in usage:
        return None
    details = usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    return {
        # `prompt_tokens` COUNTS the cached ones, so fresh input is the difference.
        # Adding the two would bill every cached token twice. The floor at zero
        # holds a nonsense body to a count `ModelCall` accepts, since a negative
        # would be refused at construction and the row would vanish entirely.
        "input_tokens": max(prompt - cached, 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "cache_read_tokens": cached,
        # Automatic prompt caching bills nothing to write, so zero is the tariff
        # rather than a gap. `prompt_tokens_details.cache_write_tokens` exists in
        # this SDK version and is deliberately not read, because the price book
        # carries no write premium for Luna and would price real tokens at zero.
        "cache_creation_tokens": 0,
    }


def token_counts_from_usage(usage: dict) -> dict[str, int] | None:
    """The four ledger counts, from whichever provider wrote this `usage` block.

    Returns None for a block matching neither shape. The caller logs that, because
    a usage block nobody can read is a tenant's spend going unrecorded.
    """
    return _anthropic_counts(usage) or _openai_counts(usage)


def model_call_from_bodies(
    request_body: dict,
    response_body: dict,
    context: CallContext,
    provider: str,
    at: datetime,
) -> ModelCall | None:
    """One ledger row from one exchange, or None when no row can be built.

    Returns None for a body carrying no `usage`, which is what a token-count call
    and an error response both look like, and for a `usage` block in neither
    provider's shape.
    """
    usage = response_body.get("usage")
    if not isinstance(usage, dict):
        return None
    counts = token_counts_from_usage(usage)
    if counts is None:
        return None
    requested = str(request_body.get("model") or "")
    served, source = served_model_for(provider, requested, response_body.get("model"))
    return ModelCall(
        purpose=context.purpose,
        provider=provider,
        requested_model=requested,
        served_model=served,
        model_source=source,
        at=at,
        tenant_id=context.tenant_id,
        agent_id=context.agent_id,
        job_id=context.job_id,
        **counts,
    )


def _unreadable_usage(response_body: dict) -> bool:
    """True when the body reported spend in a shape this module cannot read."""
    usage = response_body.get("usage")
    return isinstance(usage, dict) and token_counts_from_usage(usage) is None


def _is_streamed(response: httpx.Response) -> bool:
    """A body the hook must leave alone, because reading it consumes the caller's stream."""
    return _STREAMING_CONTENT_TYPE in response.headers.get("content-type", "")


def _requested_model(request: httpx.Request) -> str:
    """The alias a request asked for, read off the bytes the SDK sent."""
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        return ""
    return str(body.get("model") or "") if isinstance(body, dict) else ""


def _log_skip(event: str, context: CallContext, request: httpx.Request) -> None:
    """Name a call the ledger will not hold, in the one shape every gap is reported in.

    Every skip carries the purpose, the requested model and the three ids, so a
    hole in a tenant's day is a line somebody can count rather than a silent zero.
    """
    log.warning(
        event,
        purpose=context.purpose,
        requested_model=_requested_model(request),
        tenant_id=context.tenant_id,
        agent_id=context.agent_id,
        job_id=context.job_id,
    )


def _body_is_unreadable(response: httpx.Response, context: CallContext) -> bool:
    """True when the hook must leave this response alone, having said why.

    Shared by the sync and the async hook so both skip on the same two grounds
    and log the same event. An error response spent no tokens worth billing and
    says nothing. A streamed body belongs to the caller, and reading it here
    consumes the caller's stream.
    """
    if response.status_code >= 400:
        return True
    if _is_streamed(response):
        _log_skip("model_ledger.stream_skipped", context, response.request)
        return True
    return False


def _record_exchange(
    response_text: str,
    request: httpx.Request,
    context: CallContext,
    provider: str,
    recorder: Recorder,
    clock: Clock,
) -> None:
    """Turn one finished exchange into a row, or name the gap it leaves."""
    response_body = json.loads(response_text)
    call = model_call_from_bodies(
        json.loads(request.content or b"{}"),
        response_body,
        context,
        provider,
        clock(),
    )
    if call is not None:
        recorder(call)
    elif _unreadable_usage(response_body):
        _log_skip("model_ledger.shape_skipped", context, request)


def _log_record_failure(context: CallContext, exc: Exception) -> None:
    """Fail open. The caller's turn already succeeded and telemetry may not undo it."""
    log.error(
        "model_ledger.record_failed",
        purpose=context.purpose,
        tenant_id=context.tenant_id,
        agent_id=context.agent_id,
        job_id=context.job_id,
        error=str(exc),
    )


def _append_response_hook(http_client, hook) -> None:
    """Add one response hook, keeping whatever hooks the client already carried."""
    hooks = dict(http_client.event_hooks)
    hooks["response"] = [*hooks.get("response", []), hook]
    http_client.event_hooks = hooks


def attach_ledger_hook(
    http_client: httpx.Client,
    context: CallContext,
    *,
    provider: str,
    recorder: Recorder,
    clock: Clock = _utc_now,
) -> None:
    """Bolt the ledger onto an httpx client, whoever built it.

    Args:
        http_client: the client to hook. `make_client` builds one, #47 hands one in.
        context:     the ids and the purpose every row from this client carries.
        provider:    the price book's name for who serves these calls.
        recorder:    where a finished row goes. `ledger_recorder` binds one to a dsn.
        clock:       reads the instant a row is stamped with. Injected so a test
                     can place a call in a known price window.
    """

    def on_response(response: httpx.Response) -> None:
        try:
            if _body_is_unreadable(response, context):
                return
            response.read()
            _record_exchange(
                response.text, response.request, context, provider, recorder, clock
            )
        except Exception as exc:
            _log_record_failure(context, exc)

    _append_response_hook(http_client, on_response)


def attach_async_ledger_hook(
    http_client: httpx.AsyncClient,
    context: CallContext,
    *,
    provider: str,
    recorder: Recorder,
    clock: Clock = _utc_now,
) -> None:
    """The same hook on an async client, because httpx demands an async callable.

    `httpx.AsyncClient` awaits every response hook, so the sync one attached to it
    is coroutine-less and raises at the first response. Reading the body is
    `aread()` there and `read()` here; everything either hook then does with the
    bytes is the shared code above, so the two providers' usage shapes are parsed
    in exactly one place.

    Ragas is why this exists. Its collections metrics await `llm.agenerate(...)`
    and `InstructorLLM` refuses that on a sync client, so the judge seam in #47
    needs `openai.AsyncOpenAI` and this hook underneath it.
    """

    async def on_response(response: httpx.Response) -> None:
        try:
            if _body_is_unreadable(response, context):
                return
            await response.aread()
            _record_exchange(
                response.text, response.request, context, provider, recorder, clock
            )
        except Exception as exc:
            _log_record_failure(context, exc)

    _append_response_hook(http_client, on_response)


def _sdk_client(
    provider: str, credentials: Credentials, http_client: httpx.Client
) -> ProviderClient:
    """The provider's own SDK, over an httpx client that already carries the hook."""
    if provider == OPENAI_PROVIDER:
        return openai.OpenAI(
            api_key=credentials.api_key,
            base_url=credentials.base_url,
            http_client=http_client,
        )
    return anthropic.Anthropic(
        api_key=credentials.api_key,
        base_url=credentials.base_url,
        http_client=http_client,
    )


def make_client(
    purpose: str,
    *,
    tenant_id: str,
    recorder: Recorder,
    agent_id: str | None = None,
    job_id: str | None = None,
    provider: str | None = None,
    credentials: Credentials | None = None,
    http_client: httpx.Client | None = None,
    clock: Clock = _utc_now,
) -> ProviderClient:
    """A provider client whose every response leaves one ledger row.

    Provider and credentials are inputs, not lookups buried at a call site. Both
    default to what Settings and the environment already say, so an ordinary call
    site names a purpose and its ids and nothing else.

    Args:
        purpose:     what these calls are for, the key a rollup groups by.
        tenant_id:   UUID string of the tenant billed for them.
        recorder:    where each row goes. Required, because a client that silently
                     records nothing is the failure this ticket exists to end.
        agent_id:    UUID string of the agent, or None for a platform call.
        job_id:      UUID string of the job, or None.
        provider:    the price book's name for who serves the calls. Derived from
                     the base url when absent.
        credentials: api key and base url. Resolved from Settings when absent, for
                     the provider named above.
        http_client: an httpx client to hook instead of a fresh one.
                     `make_instructor_client` passes the client instructor wraps.
        clock:       reads the instant each row is stamped with.

    Returns:
        An `openai.OpenAI` for the `openai` provider, an `anthropic.Anthropic`
        for everyone else. Both carry the same hook on the same httpx client, and
        neither carries a reasoning effort. See WHERE A PURPOSE GOES above.
    """
    credentials = credentials or resolve_credentials(provider)
    provider = provider or provider_for_base_url(credentials.base_url)
    http_client = http_client or httpx.Client()
    attach_ledger_hook(
        http_client,
        CallContext(purpose=purpose, tenant_id=tenant_id, agent_id=agent_id, job_id=job_id),
        provider=provider,
        recorder=recorder,
        clock=clock,
    )
    return _sdk_client(provider, credentials, http_client)


def make_async_client(
    purpose: str,
    *,
    tenant_id: str,
    recorder: Recorder,
    agent_id: str | None = None,
    job_id: str | None = None,
    credentials: Credentials | None = None,
    http_client: httpx.AsyncClient | None = None,
    clock: Clock = _utc_now,
) -> openai.AsyncOpenAI:
    """The async half of `make_client`, for the one caller that needs one.

    Ragas is that caller. Its collections metrics await `llm.agenerate(...)`, and
    `InstructorLLM` raises `TypeError("Cannot use agenerate() with a synchronous
    client")` for anything whose underlying `chat.completions.create` is not a
    coroutine function (`ragas/llms/base.py`, `_check_client_async`, ragas 0.4.3).

    OpenAI is the only provider here, because decision #34 routes every judge to
    `gpt-5.6-luna` and no other async site exists. A second provider gets added
    when a second provider has an async caller, not before.

    Args:
        purpose:     what these calls are for, the key a rollup groups by.
        tenant_id:   UUID string of the tenant billed for them.
        recorder:    where each row goes.
        agent_id:    UUID string of the agent, or None for a platform call.
        job_id:      UUID string of the job, or None.
        credentials: api key and base url. Resolved from Settings when absent.
        http_client: an async httpx client to hook instead of a fresh one.
        clock:       reads the instant each row is stamped with.
    """
    credentials = credentials or resolve_credentials(OPENAI_PROVIDER)
    http_client = http_client or httpx.AsyncClient()
    attach_async_ledger_hook(
        http_client,
        CallContext(purpose=purpose, tenant_id=tenant_id, agent_id=agent_id, job_id=job_id),
        provider=OPENAI_PROVIDER,
        recorder=recorder,
        clock=clock,
    )
    return openai.AsyncOpenAI(
        api_key=credentials.api_key,
        base_url=credentials.base_url,
        http_client=http_client,
    )


def make_instructor_client(
    purpose: str,
    *,
    tenant_id: str,
    recorder: Recorder,
    agent_id: str | None = None,
    job_id: str | None = None,
    route: ModelRoute | None = None,
    credentials: Credentials | None = None,
    http_client: httpx.Client | httpx.AsyncClient | None = None,
    clock: Clock = _utc_now,
    is_async: bool = False,
) -> InstructorClient:
    """An instructor client over a factory-built one, so structured calls are counted.

    Ragas asks its metrics through instructor, instructor asks through the
    provider SDK, and the SDK asks through httpx. The hook sits on the httpx
    client, the one layer all three still pass through, so a Ragas run lands
    ledger rows without Ragas knowing this module exists. The route supplies the
    model and the reasoning effort as instructor defaults, so no call site
    repeats either. See WHERE A PURPOSE GOES above.

    Args:
        purpose:     the key the routing table and the rollup both use.
        tenant_id:   UUID string of the tenant billed for these calls.
        recorder:    where each finished row goes.
        agent_id:    UUID string of the agent, or None for a platform call.
        job_id:      UUID string of the job, or None.
        route:       overrides the table. Injected by a test the way clock is.
        credentials: api key and base url. Resolved from Settings when absent.
        http_client: an httpx client to hook instead of a fresh one. Async when
                     `is_async` is set, since that is the client it wraps.
        clock:       reads the instant each row is stamped with.
        is_async:    build on `openai.AsyncOpenAI`, which is what Ragas needs and
                     what `make_async_client` exists for.

    Returns:
        An `AsyncInstructor` when `is_async` is set, an `Instructor` otherwise.

    Raises:
        UnknownPurpose:      the table routes no such purpose.
        UnsupportedProvider: the route names a provider with no client here.
    """
    route = route or route_for(purpose)
    if route.provider != OPENAI_PROVIDER:
        raise UnsupportedProvider(
            f"Purpose {purpose!r} routes to {route.provider!r}, and this factory wraps "
            f"only {OPENAI_PROVIDER!r} clients in instructor."
        )
    build = make_async_client if is_async else make_client
    client = build(
        purpose, tenant_id=tenant_id, recorder=recorder, agent_id=agent_id,
        job_id=job_id, credentials=credentials, http_client=http_client, clock=clock,
        **({} if is_async else {"provider": route.provider}),
    )
    defaults: dict[str, str] = {"model": route.model}
    if route.reasoning_effort is not None:
        defaults["reasoning_effort"] = route.reasoning_effort
    return instructor.from_openai(client, **defaults)


def record_model_call(call: ModelCall, target: str | LedgerConnection) -> None:
    """Write one row to the tenant's `model_calls` table (tenant migration 0019).

    Args:
        call:   the finished row.
        target: a tenant dsn string, which this opens and commits and closes, or an
                already open psycopg2 connection, which the caller still owns and
                still commits. The dsn arrives here and nowhere else, so no carrier
                and no ledger row ever holds one (project rule 1).
    """
    params = (str(uuid.uuid4()), *(_value(call, name) for name in _COLUMNS))
    if not isinstance(target, str):
        with target.cursor() as cur:
            cur.execute(_INSERT, params)
        return
    conn = psycopg2.connect(target, connect_timeout=settings.TENANT_DB_CONNECT_TIMEOUT_S)
    try:
        with conn.cursor() as cur:
            cur.execute(_INSERT, params)
        conn.commit()
    finally:
        conn.close()


def _value(call: ModelCall, name: str) -> object:
    """One column's value. StrEnum goes to the driver as its string."""
    value = getattr(call, name)
    return value.value if isinstance(value, ModelSource) else value


def ledger_recorder(target: str | LedgerConnection) -> Recorder:
    """A recorder bound to one tenant database, ready to hand to `make_client`."""

    def record(call: ModelCall) -> None:
        record_model_call(call, target)

    return record
