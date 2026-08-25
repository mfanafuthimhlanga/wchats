"""The anthropic client every direct-API site builds, and the ledger row it leaves.

WHY THE HOOK IS ON THE HTTP LAYER
    Ten call sites in `apps/api/app` build `anthropic.Anthropic()` and read the
    text back. Ticket #47 wraps this client in `instructor.from_anthropic(...)`,
    and Ragas wraps that again, so a recorder attached to a wrapper method stops
    firing the moment someone adds another wrapper. `usage` and `model` arrive as
    bytes on the wire, and the httpx response hook is the one place every wrapper
    still passes through. `attach_ledger_hook` therefore takes an httpx client it
    did not construct, which is what lets #47 keep this seam intact.

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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse

import anthropic
import httpx
import psycopg2
import structlog

from app.core.config import settings
from app.domain.model_call import ModelCall, ModelSource

log = structlog.get_logger(__name__)

Recorder = Callable[[ModelCall], None]
Clock = Callable[[], datetime]


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
class Credentials:
    """What the SDK needs to reach a provider. Never logged, never stored on a row."""

    api_key: str
    base_url: str | None = None


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
    if "anthropic" in host:
        return "anthropic"
    return host


def resolve_credentials() -> Credentials:
    """The api key from Settings and the base url from `os.environ`.

    The key comes from Settings because a Celery worker started without inheriting
    `.env` has it nowhere else, which is the reason `metadata_service` already
    passes it explicitly. The base url comes from the environment because that is
    where the SDK itself looks, so resolving it here changes no endpoint a worker
    already calls.
    """
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


def model_call_from_bodies(
    request_body: dict,
    response_body: dict,
    context: CallContext,
    provider: str,
    at: datetime,
) -> ModelCall | None:
    """One ledger row from one exchange, or None when the exchange spent nothing.

    Returns None for a body carrying no `usage`, which is what a token-count call
    and an error response both look like.
    """
    usage = response_body.get("usage")
    if not isinstance(usage, dict):
        return None
    requested = str(request_body.get("model") or "")
    served, source = served_model_for(provider, requested, response_body.get("model"))
    return ModelCall(
        purpose=context.purpose,
        provider=provider,
        requested_model=requested,
        served_model=served,
        model_source=source,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        at=at,
        tenant_id=context.tenant_id,
        agent_id=context.agent_id,
        job_id=context.job_id,
    )


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
            # An error response spent no tokens worth billing, and says nothing.
            if response.status_code >= 400:
                return
            if _is_streamed(response):
                log.warning(
                    "model_ledger.stream_skipped",
                    purpose=context.purpose,
                    requested_model=_requested_model(response.request),
                    tenant_id=context.tenant_id,
                    agent_id=context.agent_id,
                    job_id=context.job_id,
                )
                return
            response.read()
            call = model_call_from_bodies(
                json.loads(response.request.content or b"{}"),
                json.loads(response.text),
                context,
                provider,
                clock(),
            )
            if call is not None:
                recorder(call)
        except Exception as exc:
            # Fail open. The customer's turn already succeeded and must not be
            # undone by a ledger that could not be written.
            log.error(
                "model_ledger.record_failed",
                purpose=context.purpose,
                tenant_id=context.tenant_id,
                agent_id=context.agent_id,
                job_id=context.job_id,
                error=str(exc),
            )

    hooks = dict(http_client.event_hooks)
    hooks["response"] = [*hooks.get("response", []), on_response]
    http_client.event_hooks = hooks


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
) -> anthropic.Anthropic:
    """An anthropic client whose every response leaves one ledger row.

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
        credentials: api key and base url. Resolved from Settings when absent.
        http_client: an httpx client to hook instead of a fresh one. #47 passes the
                     client instructor is about to wrap.
        clock:       reads the instant each row is stamped with.
    """
    credentials = credentials or resolve_credentials()
    provider = provider or provider_for_base_url(credentials.base_url)
    http_client = http_client or httpx.Client()
    attach_ledger_hook(
        http_client,
        CallContext(purpose=purpose, tenant_id=tenant_id, agent_id=agent_id, job_id=job_id),
        provider=provider,
        recorder=recorder,
        clock=clock,
    )
    return anthropic.Anthropic(
        api_key=credentials.api_key,
        base_url=credentials.base_url,
        http_client=http_client,
    )


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
