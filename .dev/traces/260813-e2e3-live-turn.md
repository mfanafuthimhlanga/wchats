# TRACE — E2E-3 · one live customer turn (2026-08-13). `5.10` ANSWERED

**4/5 assertions. The one that failed was my assertion's fault, not the product's.**
**The headline: `5.10` is closed — the `dc67d37` fix is confirmed against the real stdout stream.**

Owner authorised the spend. Turn: `POST /agents/{id}/chat`, job `0996d8d9-88e3-4541-bfb2-a171555e558b`,
~56 seconds wall clock.

## The question this run existed to answer

`5.10`: *every* test of the turn loop constructs SDK dataclasses directly, so **nothing had ever
observed the stdout stream-json the SDK actually parses.** `dc67d37` moved `ToolResultBlock` handling
from `AssistantMessage` to `UserMessage` on the strength of 42,334 session-transcript entries — strong
evidence about the *session JSONL* protocol, but not about the stdout one. If the two differed, the
fix was verified against the wrong wire.

**They do not differ.**

```
events: agent.thinking → agent.tool_call → agent.tool_result
                       → agent.tool_call → agent.tool_result → agent.response

[PASS] agent.tool_result events exist          2 found, on the real stream
[PASS] tool_name is resolved, not 'unknown'    'ToolSearch'
[PASS] tool_name is resolved, not 'unknown'    'retrieve'
```

Both halves of `dc67d37` are confirmed: the message-type fix (results arrive at all) **and** the
`tool_use_id` join that replaced `getattr(block, "name", "unknown")`. Neither name is `"unknown"`,
which is the only value the old code could produce and the reason `retrieval_eval.py:194`'s
`tool_name == "retrieve"` join could never match.

The agent answered **from the corpus**: *"Our wholesale prices are per kilogram, excluding VAT …
Yirgacheffe: R 480/kg (MOQ: 5 kg)"* — matching the chunk E2E-2 ingested. Real retrieval, real
grounding, first time.

Tenant side: `conversations 1 · messages 2 · retrieval_metrics 1 · tool_calls 2 · turn_metrics 1`.

## `5.14` — and it is the day's second Family J

**The Auditor failed on all three attempts.**

```
run_auditor.failed error="2 validation errors for AuditorVerdict
  citation_spans  Field required  [input_value={'verdict': 'partial', 'confidence': 0.65}]
  reason          Field required"
```

`max_tokens=512`, and the Auditor's verdict is the one judge output that must **echo evidence** — one
`{claim, source_chunk, supported}` per claim. An empty `citation_spans` costs nothing, which is
exactly why 512 sufficed for the three months `5.11` describes, when the judge was handed `"[]"` on
every turn. A real multi-claim answer over 962 tokens of context truncates the tool JSON mid-object.

**Fixing the dead branch is what gave the judge its evidence, and the evidence is what broke the
judge's budget.** The second layer was only visible from on top of the first.

Fixed in `5bd88cf`: `AUDITOR_MAX_TOKENS=2048` **and** `AUDITOR_MAX_CITATION_SPANS=8` in the system
prompt — neither alone suffices, since a verdict that scales with the answer re-breaches any fixed
ceiling. The more important half is `AuditorVerdictTruncated`, raised **before** validation: a
truncated call arrives as a partial dict, so pydantic said "Field required", which reads as a model
ignoring its schema and points at the prompt instead of the budget. **A truncated verdict is not an
`ungrounded` verdict.** 7 tests, 3 mutation proofs.

## The failed assertion was mine

`citation_coverage` was `NULL`. Cause: `run_retrieval_faithfulness` logged **`skipped_not_sampled`** —
`RETRIEVAL_FAITHFULNESS_SAMPLE_RATE = 0.1` and this turn lost the dice roll. **The sampler worked; my
assertion was wrong to expect it unconditionally.** Filed as `5.15`.

Consequence worth stating: **`5.13` is not closed by this run.** But `retrieved_tokens=962` *was*
written, which is the first evidence that contexts now reach the metrics path at all.

## Also observed

- **`tool_name='ToolSearch'`.** The customer agent spent a tool call *searching for* its own
  `mcp__customer-tools__retrieve` before calling it — so the SDK's deferred-tool mechanism is active
  on the customer turn path, costing an extra round trip per turn. Not investigated; not filed as a
  defect because it is not established that it is one.
- **The runtime worker drained a stale `run_eval_suite`** for an unrelated agent
  (`dfc18d4a-…`, `agent_not_found_or_unconfigured`) the moment it started — a task parked on the
  Redis `runtime` queue by an earlier session. Exactly the hazard `1.13` names: *"a real
  `run_agent_turn` parked on the queue for whatever worker drained it next — that bill lands detached
  from the test run."* It failed harmlessly here. The queue should be drained deliberately before any
  costed run.

## RE-RUN after the `5.14` fix — the verdict is live, and it exposed `5.16`

Second turn, job `ca20dc54-3a8d-4540-b83c-361788c1572a`. The whole validation chain now completes:

```
agent.thinking → agent.tool_call(ToolSearch) → agent.tool_result
               → agent.tool_call(retrieve)   → agent.tool_result
               → agent.response
               → gatekeeper.complete → auditor.complete → strategist.complete

run_auditor.complete  citation_spans=7  confidence=0.65  verdict=partial   (3.83s)
```

**The first valid grounding verdict in the platform's history.** Before the fix: three attempts, zero
verdicts. `citation_spans=7` sits just under the cap of 8, so the cap is binding-ish and worth
watching rather than obviously generous.

**And reading the verdict's own reason found `5.16`.** It says *"Retrieved context only confirms VAT
exclusion and South African Rand currency; specific product [prices]…"* — but the agent quoted the
prices correctly from the corpus. `agent.py:1515` builds the judge's context as
`[str(r)[:600] for r in retrieve_results][:3]` — **≤1800 chars, against `retrieved_tokens=962`
(~3,800 chars).** The judge marked price claims unsupported because it was never shown the price
rows. **So `partial` is an artefact of the cap, not a judgement about the response**, and every
stored verdict is biased the same way. Filed as `5.16`, deliberately not fixed in place: the right
rule is "the judge sees what the agent saw", and that is a measurement-layer decision, not a bigger
arbitrary number.

## Not established

- **`citation_coverage` has still never been non-NULL** — both turns drew `skipped_not_sampled`.
- **The `5.14` fix is confirmed for one answer shape.** A longer answer could still breach 2048; the
  span cap is what is supposed to prevent that, and it has not been observed to bind.
- One turn is not a measurement. Nothing here says anything about answer quality in general, and
  `5.16` means the `partial` verdict says less than it appears to.
