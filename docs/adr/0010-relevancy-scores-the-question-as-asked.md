# 0010: Relevancy is scored on the question as it was asked, rewritten to stand alone

Status: accepted. Built in #231 (tenant migration 0028), #234 and #239 for issue #227,
2026-09-09 to 2026-09-12.

## What was decided

An eval scenario is a conversation whose last customer message is the question.
`eval_scenarios.turns` holds the prior turns, oldest first, and the eval turn replays them
as the agent's history through the same `run_agent_loop` path a live conversation uses.
History belongs to the scenario: `_run_one_eval_turn` builds it from the row's own turns
on every call and nothing carries one row's history into the next.

Answer relevancy compares a response with the question. For a scenario carrying turns
the raw last message is not the question, so before scoring a resolver rewrites it into a
standalone question and relevancy scores against that string. The other three metrics
keep the raw question. The rule is `RESOLVED_INPUT_METRICS` in
`app/services/eval_service.py`, and the override is applied to the metric's keyword
arguments, never to the sample row, because a returned judge row is matched back to its
scenario on `(user_input, reference)`.

## The resolver

`resolve_question` in `app/services/question_resolution.py` is one forced tool call,
temperature 0, 200 output tokens, billed to its own purpose `eval_question_resolution` so
a cost rollup never reports the Judge as costing what the rewrite cost.

It takes the question and its turns. It never sees the reference or the response. A
resolver holding the reference could write the question the reference answers, and
relevancy would then score the Judge's paraphrase of the label. The parameter list is the
guard, not a prompt instruction.

Every failure returns `None`, and `None` scores the raw question, which is what the eval
did before #227. The row records the fallback: `eval_samples.resolved_question` is `NULL`
beside a non-empty `turns`. A fallback is counted (ADR 0011), never scored as a fail,
because a resolver outage is a fact about the measurement and not about the agent. Two
exceptions do propagate: `UnknownPurpose`, so a typo in the route table stops the run
rather than scoring it raw and saying so only in a log, and `SoftTimeLimitExceeded`.

Turn contents are JSON-encoded per line and the message under rewrite is its own message,
so a customer-authored turn cannot forge a role line or imitate the final marker.

## Who writes turns

- The miner keeps every message before the flagged one. The question comes off the
  flagged event, not the conversation opener.
- The drafter adds a lead-in only when the corpus holds more than one document, and drops
  a lead-in that names nothing or whose question repeats the subject.
- The golden bench and `register_golden_scenarios` take `turns` on each pair. A golden
  pair's identity is the pair of question and turns, so one question may appear under two
  conversations.

## Consequences a reader will meet

- `#58`'s first calibration was labelled on raw questions. The Judge is unchanged, so the
  artifact stands, and the calibration sheet now shows the conversation and the rewrite
  and tells the labeller to judge against `resolved_question` where one exists.
- A scenario's turns are customer-authored text reaching two model calls, the agent turn
  and the resolver. Both bound the history at the read and again at the seam.
- Every read and write of the 0028 columns degrades to the pre-0028 projection so a tenant
  behind the migration still runs a single-turn eval. `register_golden_scenarios` is the
  exception and returns a 500 on such a tenant.
