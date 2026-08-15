# PLAN — close the four adversary findings left open by `5.16`

`5.18`, `5.19`, `5.20`, `5.21`. Each was filed rather than fixed because it was wider than `5.16` or
touched a shared boundary. They are not one job: one has a root-cause fix, two are bounded changes,
and one turns out to be smaller than filed.

## `5.21` — retrieval order under parallel tool use. FIX THE CAUSE.

`_record_tool_result` walks `reversed(tool_calls_log)` for the most recent retrieve entry lacking a
result. With two `ToolUseBlock`s emitted before either result returns, the first result lands on the
second entry, so chunks are attributed to the wrong query.

**The block carries `tool_use_id` and the log entry does not.** Record it on the entry and match on
it. That removes the hazard rather than narrowing the claim, and it retires the `reversed()` walk
that `red_team_probe.py`'s `pending_skill` variable got wrong by construction (`5.9`).

- `tool_calls_log.append({... "tool_use_id": block.id})` at `agent.py:1183`.
- `_record_tool_result` matches by id; falls back to the old walk **only** when no entry carries the
  id, and logs when it does, since that path is the pre-`5.9` shape and should be observable.
- Test: two tool_use blocks, then results delivered **out of order**. Assert each result lands on its
  own query.

## `5.18` — two halves, both narrow

**(a) The judge sees content only.** `retrieve_tool` returns the repr of full chunk dicts, so the
agent saw `chunk_id`, `document_id`, `section` and `score`. An answer citing a document or section
name has no support in the judge's context.

`_retrieved_chunk_texts` is shared with `eval.py`, so it does not change. Add a second renderer that
produces one judge element per chunk carrying the metadata **and** the content, captured beside the
existing key. One parse, two derived captures, one reader each.

**(b) `AUDITOR_MAX_CITATION_SPANS = 8` was sized against a 3-element context.** Spans are one per
audited **claim**, and claims come from the response, so context size does not drive the count. The
real risk is the two constants drifting apart: `5.14` needed both together and nothing pins the
relationship. Add a test that computes the worst-case verdict size from the span cap and the stated
word limits and asserts `AUDITOR_MAX_TOKENS` covers it. Leave the number alone.

## `5.19` — the SEC-02 frame, restored on the judge path

`retrieve_tool` frames retrieved chunks with a header that says everything inside is data and never
instructions. `_retrieved_chunk_texts` strips it, so the Auditor now gets up to 80KB unframed.

Frame it in `call_auditor`, which is where the model reads it. Constants move to
`app/utils/context_frame.py` so `validation_service` does not import `agent_tools` (a heavy module on
a validator path) and so there is **one** copy of a security-relevant string. `agent_tools`
re-exports, keeping `agent_tools.RETRIEVED_CONTEXT_HEADER` valid for existing tests.

This also makes the judge's prompt structurally match what the agent was handed, which is the same
rule `5.16` implements.

## `5.20` — MEASURED, and smaller than filed. Close it with evidence.

| | Serialised chain message |
|---|---|
| Pre-`5.16` | 2,512 bytes |
| Typical turn, 5 chunks | 4,719 bytes (1.9x) |
| Worst case, 40 chunks at ceiling | 80,924 bytes |
| Occurrences of the payload | **1** |

The filed row said it rides in `run_gatekeeper`'s message "as well as" `run_auditor`'s. A chain is
one message carrying the head task plus the remaining signatures, so the payload exists once per hop,
not twice at once. The 44x figure is ceiling-against-old-ceiling; **typical growth is 1.9x**.

79 KB worst case in a Redis message does not justify restructuring a chain that carries OPS-07's
ordering guarantee. **Accept it, record the numbers, and add a guard** that the payload stays bounded
by the retrieval contract so an unbounded one cannot appear silently.

## Files

| File | Change |
|---|---|
| `app/utils/context_frame.py` | new; the two frame constants and the framer |
| `app/services/agent_tools.py` | import and re-export them |
| `app/services/validation_service.py` | frame the Auditor's context block |
| `app/worker/tasks/runtime/agent.py` | `tool_use_id` matching; the judge chunk renderer |
| `tests/unit/test_judge_sees_agent_context.py` | metadata, out-of-order results, the bound |
| `tests/unit/test_auditor_truncation.py` | the span-cap / token-ceiling relationship |

## Risks

- **The judge's elements change shape**, so any stored comparison against the old shape is not
  comparable. This is a third fence on historical verdicts and must be recorded as one.
- **Framing costs tokens.** The header is about 60 words, once per call, against a context of up to
  20,000 tokens. Negligible, and stated rather than assumed.
- **`tool_use_id` matching changes attribution** on turns that were previously mis-attributed. That
  is the fix, but it means retrieval-order claims about past runs do not hold retroactively.

## Tests, and what each catches

1. Out-of-order results land on their own query. (Catches the `reversed()` walk.)
2. A chunk's `document_id` and `section` reach the judge. (Catches `5.18a`.)
3. The Auditor prompt contains the frame around the context. (Catches `5.19`.)
4. `AUDITOR_MAX_TOKENS` covers `AUDITOR_MAX_CITATION_SPANS` at the stated word caps. (`5.18b`.)
5. The dispatched payload is bounded by `MAX_CHUNKS x CHUNK_CONTENT_CHAR_LIMIT x CALLS_PER_TURN`
   plus metadata. (`5.20`.)

**Mutation proof required for each**, and per the round-2 lesson, the proof must assert on the value
the consumer receives, not on the shape of the line that produces it.
