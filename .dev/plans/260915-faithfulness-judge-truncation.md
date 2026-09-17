# 279 · faithfulness-judge-truncation

**Goal:** the faithfulness Judge scores a long answer instead of storing unknown.

## Cause

Ragas 0.4.3 caps every judge call at 1024 completion tokens, and faithfulness' second call needs
more than that for a long answer.

The cap is `InstructorModelArgs.max_tokens = 1024`
(`.venv/Lib/site-packages/ragas/llms/base.py:726`). `build_judge_llm` passed only `temperature`, so
that default rode through `LunaInstructorLLM._map_provider_params`, reached the wire as
`max_completion_tokens: 1024`, and bound every Ragas judge call this platform makes.

Faithfulness makes two calls per row:

| Call | Returns | Size |
|---|---|---|
| `StatementGeneratorOutput` | the answer as atomic claims | about the length of the answer |
| `NLIStatementOutput` | each claim repeated word by word, plus a reason and a verdict | about twice the claims again |

The second is the one that overran. The provider stopped at the cap, returned
`finish_reason: "length"`, and instructor raised `IncompleteOutputException`
(`.venv/Lib/site-packages/instructor/v2/providers/openai/handlers.py:630`). Instructor re-raises
that one without a retry (`v2/core/retry.py:259`), `_score_samples` caught it, logged
`run_ragas_eval.metric_failed` and stored None.

**OBSERVED 2026-09-15 on eval run 735fb9fa:** 17 of 49 faithfulness rows stored no verdict, each
with `error_type=IncompleteOutputException 'The output is incomplete due to a max_tokens length
limit.'`. One more row went the same way on the 0a99f7ab rejudge.

## The cap, before and after

| | Value | Where it came from |
|---|---|---|
| Before | 1024 | ragas's `InstructorModelArgs` default, never overridden |
| After | 4096 | `judge_llm.JUDGE_MAX_COMPLETION_TOKENS` |

**Why 4096.** The longest assistant message this product stores is
`TURN_HISTORY_MAX_ROW_CHARS` = 4000 characters (`app/worker/tasks/runtime/agent.py:387`), about
1000 tokens of English prose. Verdicted, that is roughly 1250 tokens of restated claims, 900 of
reasons and 300 of JSON keys and punctuation, so about 2500 in all. Ragas names the same figure in
its own `_map_openai_params` docstring: "Default max_tokens=1024 may not be sufficient. Consider
increasing to 4096+".

**One number for every judge purpose.** Context recall decomposes the same way faithfulness does
(`ContextRecallOutput`, one statement plus one reason per classification). Context precision and
answer relevancy each return a single short object and have never been seen to cut. A ceiling costs
nothing until it is reached, so all five purposes carry the same figure rather than a second
routing table beside `PURPOSE_ROUTES`.

**The cap is not a silencer.** An output that still does not fit raises, `_score_samples` stores
None, and the row reads unknown, which is what it did before.
`test_a_truncated_verdict_stores_nothing_and_says_why` drives a 128-token cap and pins that.

**The kwarg is `max_tokens`, not `max_completion_tokens`.** Passing the renamed spelling would
leave ragas's own `max_tokens: 1024` in `model_args` beside it, and `_map_provider_params` pops that
one over the top, so the judge would go back to 1024 with the raised number nowhere on the wire.

## Files

- `apps/api/app/services/judge_llm.py`, the constant and one kwarg on `build_judge_llm`.
- `apps/api/tests/unit/test_faithfulness_judge_truncation.py`, new, 10 tests.
- `apps/api/tests/unit/test_judge_llm.py`, where the wire assertion reads the constant now.

## Tests

The fake provider **enforces the cap the judge itself asked for**, reading `max_completion_tokens`
off the request body it was sent and answering `finish_reason: "length"` with the arguments cut when
the reply does not fit. Nothing asserts a number the test also supplies, and lowering the constant
makes the fake start cutting exactly as the real provider did.

The fixture answer is 3437 characters, inside `TURN_HISTORY_MAX_ROW_CHARS`, so the bug is
reproduced at a size this product actually stores. Its statement call fits 1024 and its verdict call
does not, which is pinned by `test_only_the_verdict_call_exceeds_the_cap_that_shipped`.

## Mutation

`JUDGE_MAX_COMPLETION_TOKENS` set back to 1024, the value that shipped, then restored. The file's
sha256 is `0DD4FBAD3CC727D7CC7E81E1346C7F299536609017BB436F14B3653DC5E22998` before the mutation and
after the restore.

| Cap | `test_faithfulness_judge_truncation.py` |
|---|---|
| 1024 | 6 failed, 4 passed |
| 4096 | 10 passed |

```
E   AssertionError: the judge still lost the row: [{'metric': 'faithfulness',
    'error_type': 'IncompleteOutputException', 'error': 'The output is incomplete due to a
    max_tokens length limit.', 'event': 'run_ragas_eval.metric_failed', 'log_level': 'warning'}]
E   AssertionError: judge_faithfulness asked for max_completion_tokens=1024; #279 lost 17 of 49 rows at 1024
    assert 1024 >= 4096
E   AssertionError: judge_answer_relevancy asked for max_completion_tokens=1024; #279 lost 17 of 49 rows at 1024
E   AssertionError: judge_context_precision asked for max_completion_tokens=1024; #279 lost 17 of 49 rows at 1024
E   AssertionError: judge_context_recall asked for max_completion_tokens=1024; #279 lost 17 of 49 rows at 1024
E   AssertionError: judge_retrieval_faithfulness asked for max_completion_tokens=1024; #279 lost 17 of 49 rows at 1024
6 failed, 4 passed in 61.20s (0:01:01)
```

## Gates

```
217 passed in 100.57s (0:01:40)
```
(`test_faithfulness_judge_truncation`, `test_judge_llm`, `test_eval_service`,
`test_eval_scoring_concurrency`, `test_retrieval_faithfulness_task`, `test_judgement_temperature`)

```
5181 tests collected in 109.71s (0:01:49)
fast gates passed in 749.8s.
```

## What this does not fix

The 17 rows on run 735fb9fa are still unknown. Rejudging that run is what fills them.
