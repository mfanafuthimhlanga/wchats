"""The InstructorLLM every Ragas judge scores through, built in one place.

`eval_service` and the retrieval faithfulness task each built their own, with the
same client, the same route and the same temperature, and each carried the same
defect: ragas 0.4.3 puts `max_tokens: 1024` on every judge call and renames it to
`max_completion_tokens` only for a model its detector reads as a reasoning model.
The detector parses the version out of `gpt-<version>-...` with `int()`, and
`int("5.6")` raises, so `gpt-5.6-luna` falls through and `max_tokens` goes on the
wire.

OBSERVED 2026-09-05 on staging, eval run d7bf12d9 of agent ee8087ed: every one of
the 80 metric calls came back
`400 Unsupported parameter: 'max_tokens' is not supported with this model. Use
'max_completion_tokens' instead.` The agent had answered all 20 scenarios and the
Judge scored none of them (#198). Reproduced 2026-09-06 from this box: the same
request with `max_completion_tokens` in place of `max_tokens`, beside the forced
tool, `reasoning_effort: none`, `temperature: 0` and `top_p: 0.1`, is accepted.

Only the rename is applied. Ragas's own reasoning-model branch would also force
`temperature` to 1.0 and drop `top_p`; the provider accepts both as sent, and the
temperature a judge runs at is a measured decision (BACKLOG 8.2a, 8.10), not a
side effect of a parameter rename.
"""

from __future__ import annotations

from typing import Any

from ragas.llms import InstructorLLM

from app.core.model_client import OPENAI_PROVIDER, LedgerContext, route_for

#: The completion cap every Ragas judge call goes out with, in place of ragas's
#: own 1024 (`InstructorModelArgs.max_tokens`, `ragas/llms/base.py:726`).
#:
#: OBSERVED 2026-09-15 on eval run 735fb9fa: 17 of 49 faithfulness rows stored no
#: verdict, each logging `run_ragas_eval.metric_failed
#: error_type=IncompleteOutputException 'The output is incomplete due to a
#: max_tokens length limit.'`. One more row went the same way on the 0a99f7ab
#: rejudge (#279). A third of that run's faithfulness measurement was lost to a
#: number, not to a judge that could not judge.
#:
#: WHICH CALL BLOWS IT. Faithfulness makes two. `StatementGeneratorOutput`
#: returns the answer decomposed into atomic claims, which is about as long as
#: the answer. `NLIStatementOutput` then repeats every claim WORD BY WORD and
#: adds a reason and a verdict to each, so it runs to roughly twice the claims
#: again. Context recall decomposes the same way (`ContextRecallOutput`, one
#: statement plus one reason per classification). Context precision and answer
#: relevancy each return a single short object and have never been seen to cut.
#:
#: WHY 4096. The longest assistant message this product stores is
#: `TURN_HISTORY_MAX_ROW_CHARS` = 4000 characters, about 1000 tokens of English
#: prose. Verdicted, that is about 1250 tokens of restated claims, about 900 of
#: reasons and about 300 of JSON keys and punctuation, so about 2500 in all, and
#: 4096 clears it with headroom. Ragas names the same figure in
#: `_map_openai_params`: "Default max_tokens=1024 may not be sufficient.
#: Consider increasing to 4096+".
#:
#: ONE NUMBER FOR EVERY JUDGE PURPOSE. A ceiling costs nothing until it is
#: reached, so the two metrics that cannot truncate carry the same figure rather
#: than a second routing table beside `PURPOSE_ROUTES`.
#:
#: THIS IS A CAP THAT FITS AND NEVER A SILENCER. An output that still does not
#: fit raises `IncompleteOutputException`, `_score_samples` stores None and logs
#: `run_ragas_eval.metric_failed`, and the row reads unknown. Pinned by
#: `tests/unit/test_faithfulness_judge_truncation.py`.
JUDGE_MAX_COMPLETION_TOKENS = 4096


class LunaInstructorLLM(InstructorLLM):
    """Ragas's InstructorLLM with `max_tokens` sent as `max_completion_tokens`.

    `_map_provider_params` is what `generate` and `agenerate` splat into
    `client.chat.completions.create` (`ragas/llms/base.py:1050`, `:1098`), so the
    rename here reaches every call the metric makes.
    """

    def _map_provider_params(self) -> dict[str, Any]:
        mapped = super()._map_provider_params()
        if "max_tokens" in mapped:
            mapped["max_completion_tokens"] = mapped.pop("max_tokens")
        return mapped


def build_judge_llm(purpose: str, ledger: LedgerContext) -> InstructorLLM:
    """The judge one Ragas metric scores through, billed under `purpose`.

    The client is async. Collections metrics await `llm.agenerate(...)`
    exclusively, and `InstructorLLM.agenerate` raises
    `TypeError("Cannot use agenerate() with a synchronous client")` for any
    client whose `chat.completions.create` is not a coroutine function
    (`ragas/llms/base.py`, `_check_client_async`). `LedgerContext.instructor_client`
    with `is_async=True` is the factory's answer, and it carries the same ledger
    hook as every other client, so a Ragas run lands `model_calls` rows without
    Ragas knowing this module exists. The route's reasoning effort rides as an
    instructor default, which is where `make_instructor_client` puts it.

    `temperature=0` reaches the wire as written (BACKLOG 8.2a): ragas merges every
    extra kwarg into `model_args`, and its reasoning-model branch, which would
    override it, never fires for this model. `top_p: 0.1` rides along from
    ragas's own defaults; changing it changes eval behaviour and wants its own
    measurement (BACKLOG 8.10).

    THE CAP GOES OUT AS `max_tokens` AND ARRIVES AS `max_completion_tokens`.
    Passing `max_completion_tokens` here instead would leave ragas's own
    `max_tokens: 1024` default sitting in `model_args` beside it, and
    `_map_provider_params` pops that one over the top, so the judge would go back
    to 1024 with the raised number nowhere on the wire.

    Args:
        purpose: the routing-table key this metric's calls bill under.
        ledger:  the ids each row carries and where it is written.
    """
    return LunaInstructorLLM(
        client=ledger.instructor_client(purpose, is_async=True),
        model=route_for(purpose).model,
        provider=OPENAI_PROVIDER,
        temperature=0,
        max_tokens=JUDGE_MAX_COMPLETION_TOKENS,
    )
