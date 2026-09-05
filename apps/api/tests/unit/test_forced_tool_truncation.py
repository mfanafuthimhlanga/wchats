"""Every forced-tool site names the token ceiling when the ceiling is what cut it.

BACKLOG `5.14` fixed this for ONE of seven sites. `call_auditor` read
`finish_reason == "length"` itself and raised its own error; the other six read
their arguments through `tool_loop.forced_tool_arguments` and had no such check.

On the OpenAI wire a tool call cut off at `max_completion_tokens` arrives with
its `function.arguments` string ending mid-JSON. `tool_arguments` cannot parse
it, `forced_tool_arguments` returned `None`, and `None` is the same value the
model produces by answering in prose. So the six sites said:

    ValueError: The judge returned no submit_verdict tool call

which points at the prompt while the fault is the budget. That is the exact
misdiagnosis `5.14` was opened for, surviving at six of the seven places it can
happen. The check now lives in `forced_tool_arguments`, so all seven diagnose it
the same way and the Auditor's own copy is gone.

The double here is the wire shape, not a convenience: `finish_reason="length"`
AND a `function.arguments` string that stops mid-object. A double that carried
whole arguments would pass with or without the fix.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.ingestion_job import IngestionJob
from app.services import strategy_service
from app.services.tool_loop import ForcedToolCallTruncated
from tests.model_doubles import AGENT_ID, JOB_ID, TENANT_ID, factory, ledger, openai_client

#: A tool call the provider cut off at the ceiling. `arguments` stops mid-object,
#: which is what the SDK hands back and what `json.loads` refuses.
_CUT_MID_JSON = '{"verdict": "partial", "confidence": 0.6'


def _truncated(name: str):
    """One forced call to `name`, cut off at `max_completion_tokens`."""
    call = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name=name, arguments=_CUT_MID_JSON),
    )
    message = SimpleNamespace(
        role="assistant", content=None, tool_calls=[call], parsed=None, refusal=None
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(index=0, message=message, finish_reason="length")]
    )


def _create_for(name: str):
    return lambda **kwargs: _truncated(name)


_JOB = IngestionJob(
    tenant_id=TENANT_ID, agent_id=AGENT_ID, job_id=JOB_ID, document_ids=[]
)


# ---------------------------------------------------------------------------
# One driver per site. Each runs the REAL site against the truncating double and
# hands back (exception type name, diagnosis text) however that site reports it.
# ---------------------------------------------------------------------------


def _raised(create, invoke) -> tuple[str, str]:
    """For the five sites that let the failure out."""
    with factory(openai_client(create=create)):
        try:
            invoke()
        except Exception as exc:
            return type(exc).__name__, str(exc)
    return "", "the site returned normally on a truncated reply"


def _gatekeeper() -> tuple[str, str]:
    from app.services.validation_service import call_gatekeeper

    return _raised(
        _create_for("submit_verdict"), lambda: call_gatekeeper("q", "r", ledger())
    )


def _strategist_judge() -> tuple[str, str]:
    from app.services.validation_service import call_strategist

    return _raised(
        _create_for("submit_verdict"),
        lambda: call_strategist("q", "r", "role", "voice", ["do"], ["don't"], ledger()),
    )


def _actor_gate() -> tuple[str, str]:
    import asyncio

    from app.services.actor_seam import call_actor_gate

    with patch("app.services.actor_seam._fetch_history", AsyncMock(return_value=[])), \
         patch("app.services.actor_seam._langfuse", None):
        return _raised(
            _create_for("submit_verdict"),
            lambda: asyncio.run(
                call_actor_gate(
                    "place_order", {}, {}, "", AGENT_ID, "", ledger=ledger()
                )
            ),
        )


def _severity() -> tuple[str, str]:
    from app.services.red_team_service import classify_severity

    return _raised(
        _create_for("submit_severity"),
        lambda: classify_severity(
            attack_vector="prompt_injection",
            probe_message="probe",
            agent_response="response",
            ledger=ledger(),
        ),
    )


def _scenarios() -> tuple[str, str]:
    from app.services.scenario_service import generate_scenarios_from_chunks

    return _raised(
        _create_for("submit_scenarios"),
        lambda: generate_scenarios_from_chunks([{"content": "content"}], ledger(), n=1),
    )


def _retrieval_strategist() -> tuple[str, str]:
    """The one site that swallows, so its diagnosis is read off its log.

    `run_strategist` logs and returns so the Celery task falls back to
    `RetrievalStrategy()` defaults. That is deliberate and stays. It makes
    `run_strategist.failed` the only place the reason appears, which is why the
    log line carries the exception type as well as its text.
    """
    with factory(openai_client(create=_create_for("generate_strategy"))), \
         patch.object(strategy_service, "log") as spy:
        strategy_service.run_strategist("{}", {}, _JOB, "postgresql://tenant-probe")

    if not spy.warning.call_args_list:
        return "", "run_strategist logged nothing at all on a truncated reply"
    fields = spy.warning.call_args.kwargs
    return fields.get("error_type", ""), fields.get("error", "")


_SITES = [
    ("validation_service.call_gatekeeper", _gatekeeper),
    ("validation_service.call_strategist", _strategist_judge),
    ("actor_seam.call_actor_gate", _actor_gate),
    ("red_team_service.classify_severity", _severity),
    ("scenario_service.generate_scenarios_from_chunks", _scenarios),
    ("strategy_service.run_strategist", _retrieval_strategist),
]


@pytest.mark.parametrize(("site", "drive"), _SITES, ids=[s[0] for s in _SITES])
def test_a_truncated_forced_call_is_reported_as_truncation(site, drive):
    """THE regression. Six sites called the ceiling a missing tool call."""
    error_type, message = drive()

    assert error_type == ForcedToolCallTruncated.__name__, (
        f"{site} reported {error_type or 'nothing'!r} for a tool call the provider "
        f"cut off at max_completion_tokens. The reader is then told to fix the "
        f"prompt while the fault is the budget. message={message!r}"
    )
    assert "max_completion_tokens" in message, (
        f"{site} raised the right type with a message that never names the "
        f"ceiling, which is the one thing its reader has to change. message={message!r}"
    )


def test_the_auditor_still_says_a_truncated_verdict_is_not_an_ungrounded_one():
    """The shared path may not cost the Auditor its measurement-honesty sentence.

    `call_auditor` had its own check and its own message until this change. The
    sentence that mattered in it is not the budget fact, which every site shares,
    but the refusal to let a truncated verdict be recorded as `ungrounded`.
    """
    from app.services.validation_service import call_auditor

    error_type, message = _raised(
        _create_for("submit_verdict"),
        lambda: call_auditor("q", "r", "ctx", ledger()),
    )

    assert error_type == ForcedToolCallTruncated.__name__
    assert "ungrounded" in message.lower(), message
