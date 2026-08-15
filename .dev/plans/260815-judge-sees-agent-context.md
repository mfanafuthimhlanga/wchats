# PLAN — the grounding judge sees exactly what the agent saw (`5.16`)

**Goal.** `run_auditor` is handed the retrieved context the agent actually answered from, not a
truncated repr of part of it. Blocks E2E-6: calibrating a judge that is shown half its evidence
measures the cap, not the judge.

## The defect, located

`agent.py:1515`

```python
retrieve_results = [tc.get("result") for tc in tool_calls_log
                    if tc.get("tool_name") == "retrieve" and tc.get("result")]
retrieved_context_json = json.dumps([str(r)[:600] for r in retrieve_results][:3])
```

Three separate losses, in one line:

| | What it cuts | Against what the agent saw |
|---|---|---|
| `[:600]` | 600 chars per retrieve call | `MAX_CHUNKS`(5) × `CHUNK_CONTENT_CHAR_LIMIT`(2000) = 10,000 |
| `[:3]` | calls 4+ dropped | `max_turns=6` allows more |
| `tc["result"]` | a **repr** of the SDK block, already cut at `RETRIEVE_RESULT_CAPTURE_CHARS`(1800) | the framed chunk text |

`result` is the **audit** capture, bounded because it reaches a jsonb column. `eval.py:483` already
records that it is "below one full retrieval" and refuses to score it.

## Approach

**The rule: the judge sees exactly what the agent saw.** No new cap, because the bound already
exists upstream and is the retrieval layer's own — `MAX_CHUNKS × CHUNK_CONTENT_CHAR_LIMIT` per call,
`max_turns=6` calls per turn. A number chosen here would drift away from it (`2.13`'s history).

Mirror `eval.py:488-499`, which is the same decision already reviewed and shipped on the eval path:
read `RETRIEVE_CHUNKS_KEY` (one untruncated string per chunk), all calls, in order.

- Extract the construction to a module-level helper so it is reachable by a test without driving the
  whole 400-line task body.
- Unparsed calls (`RETRIEVE_CHUNKS_SOURCE_KEY == unparsed`) fall back to `result` and are **counted
  and logged**, never silently dropped — a degraded judge context must be an observation. Dropping
  them would reintroduce `5.11` (empty context) in a new spelling.
- Emit `run_agent_turn.judge_context` with chunk count / unparsed count / chars, so E2E-6 can read
  what the judge was actually shown instead of inferring it.

## Files

| File | Change |
|---|---|
| `apps/api/app/worker/tasks/runtime/agent.py` | new `_judge_retrieved_context()`; dispatch site calls it |
| `apps/api/tests/unit/test_judge_sees_agent_context.py` | new — pins judge-context == agent-context |

## Cost, stated rather than discovered later

Auditor input grows from ≤1800 chars to ≤10,000 per retrieve call. Haiku input $1/M:
~$0.0005 → ~$0.0025 per typical turn; worst case (6 calls at cap) ~$0.015. `AUDITOR_MAX_TOKENS=2048`
and `AUDITOR_MAX_CITATION_SPANS=8` bound the **output** and are unchanged — `5.14` sized them for a
real multi-claim verdict already.

## Risks

- **A verdict's meaning changes.** Every stored `auditor.complete` was produced under the old cap and
  is not comparable to a new one. Does not silently invalidate them — `5.11` already fences the
  pre-`dc67d37` set; this adds a second boundary that must be recorded in the backlog row, not just
  in code.
- **Unbounded growth if `max_turns` is ever raised.** Accepted: the helper is expressed in the
  retrieval layer's units, so the ceiling moves with the contract rather than against it.

## Tests, and what each would catch

1. Untruncated: a chunk longer than 600 chars reaches the judge whole. (Catches `[:600]`.)
2. All calls: a 4th retrieve call reaches the judge. (Catches `[:3]`.)
3. Chunk-per-element: the judge gets N elements for N chunks, not one repr blob. (Catches `result`.)
4. Equality: what the judge is handed **is** what `_record_tool_result` captured for the agent —
   driven through the real capture path, not a hand-built log.
5. Unparsed: falls back to `result` and is counted; the judge is never handed `[]` for a turn that
   retrieved something.

**Mutation proof required** (a negative test never observed to fail is a tautology): restore the
`[:600]` / `[:3]` line, observe red, `git checkout HEAD --`, observe green. Record the output.

## Not in scope

- `5.12` (the 200-char `agent.tool_result` summary the faithfulness sampler reads) — a different
  channel with a different reader. Named here so it is not assumed closed.
- `5.13` / `5.15` — needs `RETRIEVAL_FAITHFULNESS_SAMPLE_RATE=1.0` and a live turn.
- Re-running E2E-3 to observe a verdict under the new context. That is the next step, not this one.
