# TRACE — the four findings `5.16` left open

**2026-08-15. Closes `5.18`, `5.19`, `5.20`, `5.21`.**
Plan: `.dev/plans/260815-adversary-findings-5-18-to-5-21.md`.

They were not one job. One had a root-cause fix, two were bounded changes, and one turned out to be
much smaller than filed once measured.

## What changed

| File | Change |
|---|---|
| `app/utils/context_frame.py` | new. The SEC-02 frame, one definition |
| `app/services/agent_tools.py` | imports and re-exports it |
| `app/services/validation_service.py` | frames the Auditor's context block |
| `app/worker/tasks/runtime/agent.py` | `tool_use_id` matching; `_judge_chunk_record`; one parser |
| `tests/unit/test_judge_sees_agent_context.py` | provenance, out-of-order results, the bound |
| `tests/unit/test_auditor_truncation.py` | the frame, and the two output constants |

## `5.21` — fixed at the cause

`_record_tool_result` matched "the most recent retrieve entry without a result", walking in reverse.
The block carries `tool_use_id`; the log entry did not. Now it does, and results attach by id. The
positional walk survives as a fallback for hand-built logs and **logs a warning when it fires**,
because it is the shape that produced the mis-attribution.

`_attach_retrieve_capture` exists so the id path and the fallback cannot drift apart. They were two
copies of the same block for about ten minutes, which is how `5.1` started.

### The test for this was a tautology on the first attempt, and the reason is worth keeping

The first version delivered results in **reverse** order, reasoning that "out of order" was the
hazard. Removing id-matching left it **25/25 green**: a reverse-order arrival pairs up correctly by
accident under `reversed(tool_calls_log)`.

**The case that actually breaks positional attachment is arrival in ISSUE order.** Both calls
outstanding, A's result arrives first, the reverse walk finds B as the most recent entry without a
result, and A's chunks are filed under B's query. Corrected, and the mutation now fails 2 tests.

The general form: **a mutation proof tests the test as much as the code**, and "I picked the obvious
adversarial ordering" is not the same as "I picked the ordering the rule gets wrong".

## `5.18` — two halves

**(a) Provenance.** `retrieve_tool` hands the agent the repr of full chunk dicts, so the agent sees
`document_id`, `section`, `chunk_id` and `score`. The judge saw content alone, so a claim naming a
document had nothing to be grounded against. `_judge_chunk_record` renders one element per chunk as a
labelled header line plus the content: the same information the agent had, without the dict syntax
that `RETRIEVE_CHUNKS_KEY`'s docstring already records as dominating the token budget.

`_retrieved_chunk_texts` is untouched, because `eval.py` imports it and Ragas scores text against
text. What changed is that there is now **one parser** (`_retrieved_chunk_records`) with two
renderings hanging off it, rather than two parses that could disagree about what a turn retrieved.

**The fixture is the part to check.** The first version of the test module built chunks as
`{"content": t, "score": 0.9}` — no `document_id`, no `section` — and was therefore structurally
incapable of seeing this defect. Fixtures now carry the provenance the real tool emits.

**(b) The span cap.** `AUDITOR_MAX_CITATION_SPANS = 8` was sized by `5.14` against a 3-element
context. Spans are one per audited **claim** and claims come from the response, so context size does
not drive the count and the number is left alone. What was missing is that `5.14` needed **both**
constants together and nothing pinned the relationship. `test_the_span_cap_and_the_token_ceiling_are_solvent_together`
computes the worst-case verdict from the span cap and the prompt's stated word limits and asserts
`AUDITOR_MAX_TOKENS` covers it. Raising the cap alone now fails.

**A second prompt test was written and then deleted rather than shipped.** It asserted
`str(AUDITOR_MAX_CITATION_SPANS) in system` — the exact vacuity `1.33` B5 removed, where the "2" in
"under 25 words" satisfied the check. `test_the_span_cap_reaches_the_model` already pins the whole
phrase. Two guards on one claim, one weaker, is how the weaker one becomes the one someone edits.

## `5.19` — the frame, restored where the model reads it

The retrieval layer wraps chunks in a header saying everything inside is data and never instructions.
`_retrieved_chunk_texts` strips it, so the Auditor received it bare, and since `5.16` it receives up
to 80,000 chars rather than 1,800.

Now framed in `call_auditor`. The constants moved to `app/utils/context_frame.py` so
`validation_service` does not import `agent_tools` (the whole retrieval stack, on a validator path)
and so a security-relevant string has **one** definition. `agent_tools` re-exports, keeping
`agent_tools.RETRIEVED_CONTEXT_HEADER` valid for its existing tests, which passed unchanged.

The guard asserts the frame encloses the context, not merely that it is present: a boundary the
evidence sits outside of bounds nothing.

## `5.20` — measured, and smaller than filed. Accepted with evidence.

| | Serialised chain message |
|---|---|
| Pre-`5.16` | 2,512 bytes |
| Typical turn, 5 chunks | 4,719 bytes |
| Worst case, 40 chunks at the ceiling | 80,924 bytes |
| Occurrences of the payload in the message | **1** |

**Two corrections to the row as filed.** It said the payload rides in `run_gatekeeper`'s message "as
well as" `run_auditor`'s; a chain is one message carrying the head task plus the remaining
signatures, so it exists once per hop, not twice at once. And the 44x figure is
ceiling-against-old-ceiling: **typical growth is 1.9x**, from 2.5 KB to 4.7 KB.

79 KB worst case in a Redis message does not justify restructuring a chain that carries OPS-07's
ordering guarantee. Accepted, with `test_the_worst_case_is_bounded_by_the_retrieval_contract` pinning
that the content total is exactly the retrieval ceiling and the provenance overhead stays a header
per chunk, so an unbounded payload cannot appear silently.

## Mutation proofs

Six, each printing `APPLIED` before its run so an unapplied mutation cannot pass for a proof.

| Mutation | Observed |
|---|---|
| attach positionally, ignore `tool_use_id` | 2 failed, 22 passed |
| judge reads the content-only key | 2 failed, 23 passed |
| unframe the judge's context | 1 failed, 24 passed |
| raise the span cap alone | 2 failed, 23 passed |
| stop telling the judge the cap | 2 failed, 23 passed |
| render the raw dict repr | 2 failed, 23 passed |

### A mutation was left applied, and the restore check was too narrow to see it

The first full-suite run after these proofs came back **2 failed**, both saying provenance was absent
from the judge's context. Cause: the proof script was interrupted by piping its output through
`Select-Object -First 6`, which closes the pipe and kills the process. It died **between restoring N1
and restoring N2**, so `_judge_retrieved_context` was left reading the content-only key. The gate ran
against mutated product code.

**The restore check is what failed, not the restore.** The adversary's rule is "restore, then verify
`git status --porcelain` is empty" — unusable here, because legitimate edits also show as modified.
The substitute was a grep for one mutation's anchor, and it was too narrow by exactly one mutation.

The check that works, and now runs after every proof round, asserts **every** anchor at once:

```
N1 id-match : True      N4a cap=8   : True
N2 judge key: True      N4b phrase  : True
N3 frame    : True      N5 renderer : True
```

Two rules out of it. **Never truncate a mutation script's output** — `-First N` kills the process
between apply and restore, and the `finally` block never runs. And **verify the anchors you did not
look at**, because the one you check is the one you were already thinking about.

The gate caught it, which is the system working. It would not have caught a mutation whose effect no
test observed, which is the whole reason these proofs exist.

### A fixture that had stopped describing production

Found while re-checking: the test harness appended `tool_calls_log` entries without `tool_use_id`, so
almost every test exercised the positional **fallback** rather than the id path a real turn now takes.
That is `1.26`'s shape — a fixture quietly describing a contract the product no longer uses. The
harness now sets the id, and `test_a_log_entry_without_an_id_still_gets_its_result` covers the
fallback deliberately rather than by accident.

Restored from byte copies taken before the first mutation, with all six anchors confirmed together
and the modules green.

## Gate

```
2284 passed, 13 skipped, 28 warnings in 481.62s (0:08:01)     exit 0
grep -cE "^(FAILED|ERROR)"  ->  0        stderr empty
```

**Arithmetic exact.** 2278 before this round, plus 6: the provenance test, three in
`TestResultsAttachToTheCallThatProducedThem`, the frame guard and the span-cap solvency guard. No
pre-existing test changed status. Skips unchanged at 13.

The run before this one was **2 failed**, and the cause was the left-applied mutation above, not the
product.

## What this does NOT establish

- **No live turn has run under any of this.** Everything is proven against the real capture path, the
  real framer and the real SDK dataclass, not against the API. E2E-3b is still the next step and is
  now larger: it should read `run_agent_turn.judge_context` and check the verdict's reason for
  provenance it can only have got from `_judge_chunk_record`.
- **No injection exploit was demonstrated**, before or after `5.19`. The frame is a layer that was
  missing and now is not.
- **The parallel-retrieve fix is proven against a constructed message order**, not an observed one.
  No real turn in this repo's history has been shown to issue two retrieves before either returns.
- **Token and cost figures remain a 4-chars-per-token heuristic.** Character counts are measured;
  `count_tokens` is a live billed call and was not made.
- **A third fence now sits on stored verdicts.** `5.11` (empty context), `5.16` (capped context) and
  this (content without provenance, unframed). A verdict from any earlier era is not comparable to
  one produced now.
