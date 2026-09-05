"""An orchestrator failure must name its cause.

BACKLOG `1.30`. The first deployment checklist ever executed (E2E-4,
2026-08-13) produced exactly this, and nothing else::

    run_deployment_checklist.orchestrator_failed  error=
    run_deployment_checklist.no_report
    run_deployment_checklist.failed  error='Orchestrator did not produce a report'

Three lines naming no cause between them. The handler logged ``error=str(exc)``
and the exception was ``asyncio.TimeoutError``, whose ``str()`` is the **empty
string** — so the one fact that mattered (it hit the 120s ceiling) was erased
at the moment it was recorded.

This is the `getattr(x, "name", "unknown")` family that `retro.md` Family I
already names: a default that is silently plausible for the wrong input
converts a diagnosis into a value. ``str(exc)`` is that default for any
exception carrying its meaning in its *type* rather than its message.
"""

from __future__ import annotations

import asyncio

import pytest

from app.worker.tasks.runtime import deployment
from app.worker.tasks.runtime.deployment import ORCHESTRATOR_TIMEOUT_S


def test_timeout_error_stringifies_empty():
    """The premise, pinned — if this ever changes, the guard below is moot.

    Not a test of the standard library for its own sake: the whole defect is
    that `str()` on this exception yields nothing, and a reader who does not
    believe that will not believe the fix either.
    """
    assert str(asyncio.TimeoutError()) == "", (
        "asyncio.TimeoutError now has a non-empty str(); re-read BACKLOG 1.30 "
        "before simplifying the handler back to error=str(exc)"
    )


def test_the_timeout_is_a_named_constant_not_an_inline_literal():
    """A ceiling that cannot be logged is a ceiling nobody can see."""
    assert isinstance(ORCHESTRATOR_TIMEOUT_S, float)
    assert ORCHESTRATOR_TIMEOUT_S >= 300.0, (
        f"ORCHESTRATOR_TIMEOUT_S is {ORCHESTRATOR_TIMEOUT_S}. 120.0 was observed "
        "to be exceeded by a real orchestrator turn in E2E-4."
    )


def test_the_handler_logs_the_exception_TYPE_not_only_its_message():
    """The regression this module exists for.

    Read from the source rather than by driving the task: the failure path is
    ~200 lines into a function that needs a control DB, a tenant DB, Redis and
    an SDK client. A source assertion is a weaker test than an executed one and
    is stated as such — but it pins the exact line that erased the diagnosis,
    and it fails loudly if someone simplifies it back.
    """
    import inspect

    source = inspect.getsource(deployment)
    handler_start = source.index("run_deployment_checklist.orchestrator_failed")
    # From just before the event name, so the call that carries it is in view.
    handler = source[max(0, handler_start - 80) : handler_start + 400]

    # Since #166 every failure log line goes through log_failure, which derives
    # the type name and a first-line message with a fallback itself. The
    # guarantee this test pins therefore has two halves: the handler hands the
    # exception to that helper, and the helper still does what the inline
    # spelling used to do. Either half regressing is the blank line coming back.
    assert "log_failure(" in handler and ", exc," in handler, (
        "the orchestrator failure handler does not hand the exception to "
        "log_failure. asyncio.TimeoutError stringifies to '', so a bare "
        "error=str(exc) records a blank where the cause should be."
        f"handler:\n{handler}"
    )
    from app.core import log_bounds

    helper = inspect.getsource(log_bounds.log_failure) + inspect.getsource(
        log_bounds.bounded_error_detail
    )
    assert "type(exc).__name__" in helper, (
        "log_failure no longer records the exception's type name"
    )
    assert "str(exc) or repr(exc)" in helper, (
        "the bounded message has no fallback; an exception whose message is "
        "empty logs nothing at all"
    )
    assert "timeout_s=" in handler, (
        "the ceiling that fired is not in the log line, so the reader cannot "
        "tell 300s-was-not-enough from something-else-broke"
    )


@pytest.mark.parametrize(
    "exc",
    [asyncio.TimeoutError(), TimeoutError(), RuntimeError("")],
    ids=["asyncio_timeout", "builtin_timeout", "empty_runtime_error"],
)
def test_the_fallback_produces_a_non_empty_string_for_message_less_exceptions(exc):
    """`str(exc) or repr(exc)` — the expression, exercised.

    Covers the general class, not just the one exception observed: any
    exception carrying its meaning in its type has this problem.
    """
    rendered = str(exc) or repr(exc)
    assert rendered, f"{type(exc).__name__} still renders as empty"
    assert type(exc).__name__ in rendered or rendered == str(exc)
