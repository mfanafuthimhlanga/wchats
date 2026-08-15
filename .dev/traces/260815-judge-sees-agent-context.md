# TRACE — the grounding judge sees what the agent saw (`5.16`)

**2026-08-15. Closes `5.16`. Blocks nothing further before E2E-6 except the E2E-3 re-run.**

Plan: `.dev/plans/260815-judge-sees-agent-context.md`.
Proofs: `.dev/reference/260815-judge-context-mutation-proofs.md`.

## What changed

| File | Change |
|---|---|
| `app/worker/tasks/runtime/agent.py` | new `_judge_retrieved_context()`; the dispatch site calls it and logs `run_agent_turn.judge_context` |
| `tests/unit/test_judge_sees_agent_context.py` | new, 10 tests |

The line that held the defect:

```python
retrieved_context_json = json.dumps([str(r)[:600] for r in retrieve_results][:3])
```

Three cuts in one expression, each independently enough to bias a verdict:

| | Cut to | The agent saw |
|---|---|---|
| `[:600]` | 600 chars per retrieve call | `MAX_CHUNKS`(5) × `CHUNK_CONTENT_CHAR_LIMIT`(2000) = 10,000 |
| `[:3]` | first three calls | up to `max_turns`(6) |
| `tc["result"]` | a repr of the SDK block, already cut at `RETRIEVE_RESULT_CAPTURE_CHARS`(1800) | the framed chunk text |

## The decision, and why it is not a bigger number

The row deferred this deliberately: raising the cap changes judge cost and the meaning of every
future verdict, and `2.13`'s history is that these numbers get changed on a reading and stay wrong.

**The rule implemented is "the judge sees exactly what the agent saw", and no ceiling is applied at
the call site.** The bound already exists and belongs to the retrieval layer: `retrieve_tool` returns
at most `MAX_CHUNKS` chunks, each already truncated to `CHUNK_CONTENT_CHAR_LIMIT`, and the SDK turn
allows at most `max_turns` calls. A number chosen at the judge boundary would drift away from that
contract the first time either constant moved. `test_the_helper_applies_no_cap_of_its_own` is what
makes reintroducing one a decision rather than an accident.

The source is `RETRIEVE_CHUNKS_KEY`, one untruncated string per chunk. This mirrors `eval.py:488-499`
line for line in shape, because that is the same decision already made and reviewed on the eval path,
and `5.1`'s lesson is that two readers of one rule is how two answers drift apart.

**Cost, stated up front rather than discovered in a bill:** Auditor input goes from ≤1800 chars to
≤10,000 per retrieve call, roughly $0.0005 to $0.0025 a turn on Haiku, worst case about $0.015.
`AUDITOR_MAX_TOKENS`(2048) and `AUDITOR_MAX_CITATION_SPANS`(8) bound the **output** and are
unchanged: `5.14` already sized them for a real multi-claim verdict.

## What this does to egress, checked rather than assumed

More tenant content now leaves the process for the Anthropic judge API per turn: up to 10,000 chars
per retrieve call instead of 1800. **It is not a new class of egress.** The helper filters on
`tool_name == "retrieve"`, exactly as the line it replaced did, so what travels is the tenant's own
ingested corpus. `lookup_structured` results, which are the `SELECT *` customer rows `0.4` is about,
were never in this channel and still are not.

## Degradation is counted, not silent

A retrieve call whose framed payload cannot be decoded falls back to the audit capture and
increments `unparsed_calls`. Contributing nothing would hand the judge `[]` for a turn that did
retrieve, which is `5.11` (the empty-context era) with a different cause and the same signature.

`run_agent_turn.judge_context` logs `chunks` / `unparsed_calls` / `chars`. E2E-6 calibrates this
judge, and a calibration run has to read what the judge was shown rather than reconstruct it from
whatever the code said at the time.

## What the mutation proofs established beyond the fix

**M1 is the finding.** Restoring the original line at the dispatch site left **8 of 10 tests green**.
The helper was correct; nothing called it. Every behavioural test drove the helper directly and saw
nothing wrong. Only the two AST pins failed.

That is `1.32` again in a new spelling, and it is now three occurrences in a week: a schema defined
and never registered, a guard whose check its own docstring satisfied (`1.33` B4), and a helper that
would have been correct and unwired. **The behavioural half of a test module cannot see a wiring
defect, and wiring is where this codebase's defects live.**

Two mutation attempts were invalid: the anchor did not match, the file was never mutated, and the
run reported `10 passed` on pristine code. Recorded in the proofs note rather than quietly redone,
with the general form: a mutation proof needs its own evidence that the mutation landed.

## Two facts this does not establish

- **No live turn has produced a verdict under the full context.** The fix is proven against the real
  capture path with the real framer and the real SDK dataclass, not against the API. The E2E-3 re-run
  is what closes that, and it is the first step of Phase B.
- **The record is not repaired.** Every stored `auditor.complete` predates this, so there are now two
  fences on the historical set, not one: `5.11` (pre-`dc67d37`, empty context) and this
  (pre-2026-08-15, capped context). `0.6`'s `count(*)` counts artefacts unless it fences on time.

## Deviation from the plan

None in approach. One observation the plan did not anticipate: running the new module alongside
`test_agent_task.py` fails collection with
`ImportError: cannot import name 'UserMessage' from 'claude_agent_sdk'`. That is `2.24` — the fake
SDK installed into `sys.modules` and never removed. **Confirmed pre-existing** by reproducing it with
`test_agent_task.py` and `test_agent_tool_result_stream.py` alone, no new file involved. It bites an
ad-hoc module selection only; in full-suite ordering `agent` is already imported before the fake
lands. Worth knowing before diagnosing it as a new break.

## Gate

Backend unit suite, run detached, observed:

```
2276 passed, 13 skipped, 28 warnings in 460.47s (0:07:40)     exit 0
grep -cE "^(FAILED|ERROR)"  ->  0        stderr empty
```

**Arithmetic exact.** Baseline at `7f2c3cf` was `2266 passed, 13 skipped, 0 failed` (a prior
session's detached run: 2259 plus the 7 tests `1.33` added). 2266 + the 10 tests added here = 2276,
so no pre-existing test changed status. Skips unchanged at 13.

## The declared gate had never run once

Found by the stop hook while this work was in flight, and it is not this session's change.
`.dev/gates.json` declared `cd apps/api && .venv/Scripts/python.exe -m pytest tests/unit -q`.
`cmd.exe` parses a leading `.venv/Scripts/...` as the command `.venv` with a `/Scripts` switch, so it
exited in **0s** with `'.venv' is not recognized as an internal or external command` and collected
nothing. Committed in `b2ec6d3` yesterday; every hook run since has reported a failure that was the
path, not the tests.

Two changes, both in `.dev/gates.json`:

- Backslashes in the interpreter path.
- **`fast` is now a collection-only smoke gate and says so in the file.** The harness clamps
  `timeoutSec` to 170s; the unit suite needs 460 to 560s here. Declaring the suite as the hook gate
  gets the hook killed, which removes the gate and reports nothing. A smaller gate that runs beats a
  larger one that cannot.

Filed as `1.35`.
