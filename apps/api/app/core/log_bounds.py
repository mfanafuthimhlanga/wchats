"""What a log line is allowed to say about an exception, for the whole tree.

`str(exc)` on a log line publishes whatever the exception carries, and in this
codebase an exception routinely carries text the agent produced or a credential
the caller passed:

  - psycopg2 renders a server error as the primary message, then `LINE n:` with
    a fragment of the failing statement, then DETAIL and CONTEXT. Postgres keeps
    values out of the primary message by design, and puts them in those trailing
    lines. Every statement this tree fails on binds model output as a parameter.
  - pydantic heads a ValidationError with the error count and the model name,
    then one indented block per field carrying `input_value=`. A response the
    model garbled arrives in that block.
  - psycopg2 and boto3 both render a connection failure with the DSN or the
    endpoint in it, so an `error=str(exc)` on a connect path writes the tenant's
    connection string into the worker's log.

#164 bounded the failure lines in `app/worker/tasks/runtime/red_team.py` with a
module-private pair of helpers. #166 counted 120 more sites in 46 other modules
with the same shape, so the pair moved here and the red-team module imports it.
One helper in one place is what makes the gate in `scripts/gates.py` able to say
the count of unbounded sites is zero.

`bounded_error_detail` keeps the first line only. Both libraries above put the
description there and the data underneath. The cap is for everything else: an
exception whose whole message is one line of interpolated model text is still
cut to `LOG_ERROR_CHAR_CAP`, which bounds the leak rather than closing it. The
close is to stop building messages that way, which is why the gate also reports
`raise` statements that interpolate.

`log_failure` takes the logger rather than binding its own, so every line keeps
the `logger` field of the module that emitted it. Deriving both `error_type` and
`error` from the exception here is the point: a handler that spells out only one
of the two is a handler that says what leaked without saying what raised, or the
reverse, and 120 handlers spelled them out by hand.
"""

from typing import Any, Protocol

#: How much of an exception message any log line in this tree may carry.
LOG_ERROR_CHAR_CAP = 200


class _Logger(Protocol):
    """The structlog surface `log_failure` uses: one method per level, kwargs only."""

    def warning(self, event: str, **fields: Any) -> Any: ...

    def error(self, event: str, **fields: Any) -> Any: ...


def scrub_for_a_text_sink(value: str) -> str:
    """One string made storable by a text sink, code point by code point.

    A log sink is a text field, and two code points break one. A NUL is stored
    by neither `text` nor `jsonb` and is rejected by a JSON parser as the
    `\\u0000` escape. An unpaired surrogate raises UnicodeEncodeError before a
    socket is touched, because UTF-8 cannot encode it.

    The UTF-16 round trip repairs exactly those two. It re-reads the string as
    the code units it is made of, so a high surrogate followed by a low one
    becomes the one astral character the two of them name, and anything left
    unpaired becomes U+FFFD. Encoding straight to UTF-8 with "replace" would
    lose the paired case, which is a real character the agent typed.
    """
    return (
        value.replace("\x00", "")
        .encode("utf-16-le", "surrogatepass")
        .decode("utf-16-le", "replace")
    )


def bounded_error_detail(exc: BaseException) -> str:
    """The one line an exception is allowed to contribute to a log record.

    `repr(exc)` covers the exception whose message is empty, where `str(exc)`
    returns "" and the line would otherwise say nothing at all. The repr names
    the class and its args, so it is bounded by the same cut and the same cap.
    """
    text = str(exc) or repr(exc)
    return scrub_for_a_text_sink(text.strip().partition("\n")[0])[:LOG_ERROR_CHAR_CAP]


def log_failure(
    log: _Logger,
    event: str,
    exc: BaseException,
    *,
    level: str = "warning",
    **fields: object,
) -> None:
    """Log one failure with its exception bounded, from any module in the tree.

    `fields` renders before the pair, which keeps each line in the order it
    already had, ids first and the failure last. `level` names the structlog
    method; the default is the level most of these handlers already used, and a
    handler that ends a run passes "error".
    """
    getattr(log, level)(
        event,
        **fields,
        error_type=type(exc).__name__,
        error=bounded_error_detail(exc),
    )
