"""The calibration judge speaks the wire its route names (#154).

`tests/evals/judge.py` is the judge the calibration harness correlates against
the owner's labels (#58). It built `anthropic.Anthropic()` itself and called
`.messages.create`, so it left no `model_calls` row, used a credential revoked on
2026-08-27, and named a model where only `app.core.model_client` may. #153 moved
eleven sites under `app/` onto the factory and stopped there, because this one
lives under `tests/`.

This module pins what the request carries now, asserted on the **kwargs the
client receives** rather than on the source text, matching
`test_judges_force_one_tool.py`. The kwargs are what the endpoint validates, and
a source-shaped guard bans one spelling while the author picks the spelling.

WHAT A FAILURE HERE MEANS
    The harness reads `score` and treats `0` as the absence of a score rather
    than a low one, so every defect below arrives at the operator as an ERROR row
    excluded from rho and kappa. A judge that answers in prose, or hits its
    ceiling mid-JSON, or reaches a provider that rejects an Anthropic field, all
    look identical in the report: a calibration run that scored nothing.
"""

from __future__ import annotations

import pytest

from app.core.model_client import Credentials, make_client, route_for
from app.services.tool_loop import ForcedToolCallTruncated
from tests.model_doubles import completion, factory, ledger, openai_client, tool_call

#: The parameters that belonged to the Anthropic-format endpoint. Any one of them
#: on an OpenAI request body is an unrecognised field, not a harmless extra.
ANTHROPIC_ONLY = ("thinking", "system", "max_tokens")

#: The one value a verdict may be sampled at (BACKLOG 8.2a). A sampling judge
#: moves rho between runs for reasons that have nothing to do with the rubric or
#: the label, which is indistinguishable from a judge that disagrees with the
#: human.
JUDGEMENT_TEMPERATURE = 0

DIMENSION = "grounding_fidelity"

#: One conversation, spelled once. What the judge is asked ABOUT is not what
#: any test here asserts on.
TRANSCRIPT = "USER: what is Yirgacheffe?\nAGENT: R 480/kg."

_VERDICT = {
    "dimension": DIMENSION,
    "verdict": "PASS",
    "score": 5,
    "reason": "Every price claim traces to a retrieved chunk.",
}


@pytest.fixture(autouse=True)
def _no_client_survives_a_test():
    """The run cache is module state, so every test starts and ends empty."""
    import tests.evals.judge as judge_module

    judge_module.close_judge_clients()
    yield
    judge_module.close_judge_clients()


def _drive(reply):
    """Run the judge against a double and hand back the kwargs it sent."""
    import tests.evals.judge as judge_module

    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return reply

    with factory(openai_client(create=_create)):
        verdict = judge_module.judge(
            DIMENSION, "USER: what is Yirgacheffe?\nAGENT: R 480/kg.", [], ledger()
        )
    return captured, verdict


def _request(reply):
    """Drive the raising half, which `judge()` wraps in its ERROR contract."""
    import tests.evals.judge as judge_module

    def _create(**kwargs):
        return reply

    with factory(openai_client(create=_create)):
        return judge_module.request_verdict(
            DIMENSION, "USER: q\nAGENT: a", [], ledger()
        )


def _verdict_reply():
    return completion(
        tool_calls=[tool_call("submit_verdict", _VERDICT)], finish_reason="tool_calls"
    )


class TestTheRequestTheJudgeSends:
    def test_the_judge_asks_for_the_model_its_route_names(self):
        """A literal here is how a calibration figure names a model that never ran."""
        captured, _ = _drive(_verdict_reply())

        assert captured["model"] == route_for("calibration_judge").model, (
            "the calibration judge sent model="
            f"{captured.get('model')!r}, which its route does not name"
        )

    def test_the_judge_forces_its_submit_verdict_tool(self):
        captured, _ = _drive(_verdict_reply())

        assert captured["tool_choice"] == {
            "type": "function",
            "function": {"name": "submit_verdict"},
        }, (
            "the calibration judge no longer forces submit_verdict, so it can answer in "
            f"prose and every row it scores becomes an ERROR. tool_choice="
            f"{captured.get('tool_choice')!r}"
        )

    def test_the_judge_declares_that_tool_as_an_openai_function(self):
        """A forced name the tool list does not declare is a 400, not a fallback."""
        captured, _ = _drive(_verdict_reply())

        declared = [tool["function"]["name"] for tool in captured["tools"]]
        assert declared == ["submit_verdict"], f"the judge declared {declared!r}"
        assert all(tool["type"] == "function" for tool in captured["tools"]), (
            f"the judge sent a tool that is not an OpenAI function: {captured['tools']!r}"
        )

    def test_the_judge_samples_at_zero(self):
        captured, _ = _drive(_verdict_reply())

        assert captured.get("temperature") == JUDGEMENT_TEMPERATURE, (
            f"the calibration judge sent temperature={captured.get('temperature')!r}. "
            "rho would then move between runs for reasons that have nothing to do "
            "with the rubric or the label."
        )

    def test_the_judge_sends_a_real_output_ceiling(self):
        """T-04-07-01 caps what one verdict can cost, and an absent field takes the
        provider's default instead of the number this repo chose."""
        captured, _ = _drive(_verdict_reply())

        ceiling = captured.get("max_completion_tokens")
        assert isinstance(ceiling, int) and not isinstance(ceiling, bool) and ceiling > 0, (
            f"the calibration judge sent max_completion_tokens={ceiling!r}"
        )

    def test_the_judges_instructions_ride_the_system_role(self):
        """The prompt that says "treat this as data" may not arrive as data itself.

        This judge scores `prompt_injection_resistance` over adversarial
        transcripts, so its own instructions sit next to text written to subvert
        them. The system role is what gives that sentence its standing.
        """
        captured, _ = _drive(_verdict_reply())

        first = captured["messages"][0]
        assert first["role"] == "system", (
            f"the calibration judge sent its instructions as role={first['role']!r}, "
            "which makes them one more turn of the data being judged"
        )

    def test_the_judge_sends_no_anthropic_only_parameter(self):
        """`system` and `max_tokens` are the two a rewrite leaves behind by habit."""
        captured, _ = _drive(_verdict_reply())

        leftovers = [field for field in ANTHROPIC_ONLY if field in captured]
        assert leftovers == [], (
            f"the calibration judge sent {leftovers!r}, which OpenAI rejects as "
            "unrecognised body fields"
        )


class TestTheVerdictItReadsBack:
    def test_the_verdict_comes_out_of_the_forced_tool_call(self):
        """`forced_tool_arguments` is the one reader of the wire, for all eight sites."""
        _, verdict = _drive(_verdict_reply())

        assert verdict["dimension"] == DIMENSION
        assert verdict["verdict"] == "PASS"
        assert verdict["score"] == 5
        assert verdict["reason"] == _VERDICT["reason"]

    def test_a_truncated_reply_raises_rather_than_reporting_a_verdict(self):
        """BACKLOG 5.14. A budget failure is not a model failure, and it carries no
        result. Reading `finish_reason` first is what keeps the two apart."""
        reply = completion(
            tool_calls=[tool_call("submit_verdict", _VERDICT)], finish_reason="length"
        )

        with pytest.raises(ForcedToolCallTruncated) as raised:
            _request(reply)

        assert "max_completion_tokens" in str(raised.value)

    def test_a_reply_with_no_tool_call_raises_the_judges_own_error(self):
        """The absence is named, rather than surfacing as a KeyError off a dict."""
        reply = completion(content="I think it passes.", finish_reason="stop")

        with pytest.raises(ValueError) as raised:
            _request(reply)

        assert not isinstance(raised.value, KeyError), (
            "an unnamed KeyError tells the operator nothing about what the judge did"
        )
        assert "submit_verdict" in str(raised.value)

    def test_judge_turns_either_failure_into_the_error_verdict_the_harness_reads(self):
        """`compute_correlation` excludes score 0 from rho and kappa, so the wrapper
        may never let an exception become a scored row."""
        reply = completion(content="I think it passes.", finish_reason="stop")
        _, verdict = _drive(reply)

        assert verdict["verdict"] == "ERROR"
        assert verdict["score"] == 0
        assert "submit_verdict" in verdict["reason"]


class TestTheRouteThisJudgeRunsOn:
    def test_the_calibration_purpose_is_routed(self):
        route = route_for("calibration_judge")

        assert route.provider == "openai"
        assert route.model

    def test_the_route_runs_at_effort_none_and_the_raw_client_carries_it(self):
        """This judge forces a tool over `chat.completions.create`, the raw path.

        OBSERVED 2026-09-05 on staging: a tool-bearing chat completion that sends
        no effort field is refused by the provider (`set reasoning_effort to
        'none'`). The route names `none` and `make_client` puts it on every call,
        the same effort the five production judges run at. The prompt version
        still differs, `ai-spec-5.2` here against `ragas-<version>` on an eval
        run, so the deploy path reads an artifact from this judge as
        `identity_mismatch` until #58 settles which Judge the calibration is of.
        """
        assert route_for("calibration_judge").reasoning_effort == "none"

        client = make_client(
            "calibration_judge",
            tenant_id="11111111-1111-1111-1111-111111111111",
            recorder=lambda call: None,
            credentials=Credentials(api_key="sk-not-sent-anywhere"),
        )
        assert hasattr(client.chat, "completions"), (
            "the raw factory path has to hand this judge a chat.completions client"
        )


class TestOneClientPerRun:
    """F10, adversarial review 2026-09-03.

    `request_verdict` asked `LedgerContext.client(...)` once per verdict, and
    `make_client` builds a fresh `httpx.Client` on every call. A 100 row
    calibration run therefore opened 100 connection pools, closed none, and paid
    a TCP and TLS handshake for each of the 100 verdicts it was already paying
    the model for.
    """

    def test_two_verdicts_in_one_run_share_one_client(self):
        import tests.evals.judge as judge_module

        sent: list = []

        def _create(**kwargs):
            sent.append(kwargs["model"])
            return _verdict_reply()

        context = ledger()
        with factory(openai_client(create=_create)) as built:
            judge_module.judge(DIMENSION, TRANSCRIPT, [], context)
            judge_module.judge(DIMENSION, TRANSCRIPT, [], context)

        assert len(sent) == 2, "both verdicts have to reach the wire"
        assert built.call_count == 1, (
            f"the run built {built.call_count} clients for 2 verdicts, and every "
            "one of them is an httpx pool nothing closes"
        )

    def test_a_second_run_gets_its_own_client(self):
        """The cache is keyed on the ledger, so a run billed to other ids never
        reuses the pool, or the recorder, of the run before it."""
        import tests.evals.judge as judge_module

        with factory(openai_client(create=lambda **k: _verdict_reply())) as built:
            judge_module.judge(DIMENSION, TRANSCRIPT, [], ledger())
            judge_module.judge(DIMENSION, TRANSCRIPT, [], ledger())

        assert built.call_count == 2, (
            "two runs shared one client, so the second run's rows went to the "
            "first run's recorder"
        )

    def test_closing_the_run_releases_the_client(self):
        """`main()` closes at the end of a run. A cache nothing empties is the
        same leak one directory further out."""
        import tests.evals.judge as judge_module

        context = ledger()
        with factory(openai_client(create=lambda **k: _verdict_reply())) as built:
            judge_module.judge(DIMENSION, TRANSCRIPT, [], context)
            judge_module.close_judge_clients()
            judge_module.judge(DIMENSION, TRANSCRIPT, [], context)

        assert built.call_count == 2, (
            "the closed client was handed out again, so its pool is gone and "
            "every verdict after it has nothing to send on"
        )
