# 0006: An eval turn sends what a live turn sends, to where a live turn sends it

Status: accepted. Decided with the owner 2026-08-23 on issue #16, built in #50.
Supersedes nothing; corrects the premise the question was asked under.

## What was decided

Two things, and they answer different halves of one question.

**The PII firewall moves inside the turn seam**, so no caller OF THE LOOP can skip it. A
response the firewall would deflect is now scored as the deflection on all three of those
paths. The scope is exact and it is narrower than it sounds, so it is spelled out under
"What this does not cover" below.

**Corpus content reaching a third-party judge is accepted egress**, named here rather than
firewalled. Ragas receives the retrieved corpus chunks as `retrieved_contexts` and cannot
score Faithfulness or ContextPrecision without them. Corpus, not "published": `retrieve`
searches ingested documents, and `pii_firewall.py:31-32` already records that a third
party's address arriving inside an ingested document is repeatable, accepted, with the
control at ingest (BACKLOG 7.30).

## The premise the question was asked under, and why it was wrong

Issue #16 asked whether the firewall belongs on the eval path or whether the egress is
accepted, on the strength of `.dev/BACKLOG.md` row `0.4`:

> Up to 60 nightly eval turns run against the tenant's production `conn_str`;
> `lookup_structured` returns `SELECT *` customer rows into the transcript; that
> transcript goes to the judge API with `pii_firewall_applied=False`.

The last clause is the one this ADR acts on, and deleting that field is half of what #50
does.

`lookup_structured` reads corpus tables only. `agent_tools.py:102`:

```python
ALLOWED_LOOKUP_TABLES: frozenset[str] = frozenset({"chunks", "documents", "chunk_metadata"})
```

The table is checked against that set before any SQL is assembled, and a non-allowlisted
table returns `is_error` with nothing executed. There are no customer tables behind that
tool.

The transcript is not sent either. `run_ragas_eval` builds a four-key sample
(`eval_service.py:1311-1314`): `user_input`, `response`, `retrieved_contexts`, `reference`.
`retrieved_contexts` is built by the loop at `eval.py:500-511`, and the line that makes it
`retrieve` captures alone is the skip at `:502`. No `lookup_structured` output reaches the
judge in any form.

## The gap that was real

The agent's own **response** could carry a customer's personal data, and did so unfiltered.

A customer types an email address or a card number into the chat, the model repeats it in
its reply, and on the live path `scan_response` replaces the entire response with
`PII_DEFLECTION`. The eval path never called that function. It never imported the module.
The unfiltered reply went into `agent_response` and out to the judge API.

`app/domain/pii_firewall.py:17-21` claims the scan is "called unconditionally,
synchronously, in-line, with no flag and no config read that could switch it off". That was
false by construction rather than by a flag, because the call sat in one caller's task body
and there are three callers.

## What this does not cover

**Paths to the agent that do not run the loop.** The guarantee is about the three callers of
`run_agent_loop`. `red_team.py:73` says outright that the loop "is intentionally NOT used
here", and the bare `probe_fn` it builds for the conversational, retrieval and data-leakage
vectors calls the model directly with no tools and no scan. Those probes cannot pull corpus
rows, but a model echoing what an attacker typed is the same leak shape. Tracked as #105.

**Model-authored text that leaves before the turn ends.** `agent.tool_call` and
`agent.tool_result` (`agent_loop.py:609`, `:614`) and `agent.escalated` (`agent.py`) all
carry model-authored strings to a public SSE endpoint, and the escalation text also goes to
the tenant by email. All three sit above the seam. Tracked as #104.

**The customer's own question.** `eval_service.py:1311` sends `user_input` to the judge
verbatim, and `bench.promote_trace_to_scenario` writes real customer turns into
`eval_scenarios` with `source='production'`. That route is closed two layers before the
judge and it is worth knowing why, because the code reads as though it were open:
`promote_trace_to_scenario` writes `reference_answer=NO_GROUND_TRUTH`, which is `''`
(`bench.py:94`), and `reference_answer != ''` survives in all three selector queries
(`eval.py:816-819`). An unlabelled row is inert to the selector by construction, pinned by
`test_the_scenario_is_inert_to_the_eval_selector_by_construction`. Labelling one would open
the route, and the label is an owner action.

## What changed

`scan_response` moved into `_turn_result` in `app/services/agent_loop.py`, which is the one
place every caller's text is finalised. `run_agent_loop` now returns the served text, and
the pre-scan text is not returned in any form, because a caller that can read it can serve
it. `published_context`, the BACKLOG 7.29 allowlist that stops the firewall deflecting the
tenant's own published contact details, moved with it.

**Three callers run this loop**, and the third is easy to miss:

| Caller | Before | After |
|---|---|---|
| `run_agent_turn` | scanned, in the task body | scanned, in the seam |
| `eval._run_one_eval_turn` | not scanned | scanned |
| `red_team_probe._build_transactional_probe_fn` | not scanned | scanned |

`pii_firewall_applied` is deleted. It was written to
`eval_runs.config -> 'agent_invocation'`, and its only reader in the repo was a test
asserting it was `False`.

## What this costs, stated rather than discovered later

**The eval scores deflections as answers.** The deleted field's own comment argued against
this: scoring a deflection measures the firewall's hit rate as if it were the agent's
grounding. That is true and it is now the accepted behaviour, because a Judge scoring text
no customer would ever receive measures nothing about the deployed Agent. Nothing counts
deflections on a run, so a run whose Faithfulness fell because three answers were deflected
looks the same as one where the model was wrong. Tracked as the eval half of #103.

**The red team can no longer tell a refusal from a caught leak.** An attack that talks the
agent into emitting an address, card or SA ID is scored on the substituted deflection. No
exemption was carved out for the probe, because an exemption is the defect this ADR closes,
rebuilt one caller along. Tracked as #103.

**Both deflect silently.** The two log lines stayed in `agent.py`, because they name
`agent_id` and `conversation_id` that the seam's dataclass does not carry. The eval and
red-team paths therefore substitute with no log line. The seam returns four `pii_` keys as
telemetry, and a caller that ignores all four still serves the deflection.

## Consequences a reader will meet

`run_agent_loop`'s return value is the served text, not the model's text. Anything wanting
the model's own words for diagnosis will not find them, and that is deliberate.

`published_context` lives in `app/services/agent_loop.py`. Older notes under `.planning/`
point at `app/utils/pii_firewall.py` and at a call site in
`app.worker.tasks.runtime.agent`; both moved.

The ordering inside `pii_firewall.py` is load-bearing and unchanged. The email branch falls
through rather than returning, so a published address cannot shadow a card number later in
the same reply, and `card` and `sa_id` have no exemption path at all.
