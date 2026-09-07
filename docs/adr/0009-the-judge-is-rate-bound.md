# 0009: The Judge is rate-bound, and what to do about it

Status: proposed. Drafted 2026-09-07 from issue #213 and the measurement in
`.dev/reference/260907-eval-scoring-is-rate-bound.md`. The first half of #213, a checklist
wait that scales with the scenario count, is already in; this ADR is the second half and
needs the owner's decision.

Option A was built (#218) and measured the same day on checklist run `ec85275a`: four keys
from four OpenAI projects, `judge_keys=4` confirmed on the worker, 2715 s to score 31
scenarios against 2661 s on one key. The projects share the wall, so A as routed buys
nothing until a project's usage tier is raised. The per-purpose key stays in the code. The
decision between B and C is still open, and B is now the only option left that shortens
the wall without the provider's help.

## The measurement

Scoring 31 scenarios against the four Ragas metrics on the production judge route took
2693 s at four samples in flight and 2676 s at eight. The worker logged an SDK retry to the
completions endpoint every two to four minutes at both settings. Doubling the calls in
flight changed the total by under one percent, so the calls share a rate: the provider's
limit on the judge key. About 87 s per scenario at this corpus size, whatever the worker
does.

The four metrics do not make one call each. Read from Ragas 0.4's collections source:
faithfulness decomposes the answer into statements in one call and judges them all in a
second; answer relevancy generates three questions in three calls and embeds them in four
Voyage calls; context precision judges each of the five retrieved chunks in its own call;
context recall is one call. Eleven judge calls and four embeddings per scenario at most.
The count a run actually made is in the tenant's `model_calls` rows for run `2dab3550`,
by purpose, and is the first thing to read before choosing below. An eval run's cost
measures the same rows and was unreadable until #208, which is why nobody had this number.

## Options

**A. Pay for rate.** Raise the provider's limit on the judge project, or route the four
judge purposes across separate OpenAI projects so each has its own rate. OpenAI meters
rate per project, not per key: four keys from one project share one budget. One Settings
field per judge purpose and a purpose-aware credential lookup; nothing else.
Keeps the Ragas Judge and its calibration work under #58 as it stands. Cost scales with
scenarios exactly as now; only the wall clock shrinks. The rate is the provider's to grant,
and a tenant with 300 scenarios meets the same wall again.

**B. One call per metric.** Replace the four Ragas metrics with four owned prompts, each
one forced tool call that returns the score and its reasons, the way every other Judge in
this codebase already works (a Judge is one typed tool call: the architecture line in
CLAUDE.md, and ADR 0008). Eleven calls become four, under a threefold cut, so 31
scenarios score in about a third of the time if the rate is per call and less if it is
per token. It is a different Judge:
#58's calibration against the owner's labels starts again, and Ragas's published
behaviour, which is the reason it was chosen, goes with it. Faithfulness in one call is
the hard case; the statement decomposition is what makes Ragas's number defensible.

**C. Both, in order.** Take A now to unblock the staging agent, read the per-purpose call
counts, and decide B on the number rather than on the reading above. Context precision
is five of the eleven calls, and its one-per-chunk loop can become one call over all
chunks without touching the other three or their calibration; that alone is the biggest
single cut available and the least to recalibrate.

## Recommendation

C. A is a support ticket and a route change. B is a Judge change and belongs behind #58's
first calibration figure, which today's run made possible for the first time: golden
relevancy 0.58 to 0.89 against a 0.9 rule. Deciding B before that figure exists would
change the Judge and the threshold in the same move, and neither would be measurable
against the other.

## Consequences a reader will meet

The checklist waits longer for a bigger golden set, by `CHECKLIST_WAIT_PER_SCENARIO_S` per
scenario over the floor, and an eval that outruns even that still reports as unfinished
and keeps spending (#207). `EVAL_SCORING_CONCURRENCY` above 4 buys retries and nothing
else until A lands. A run's per-purpose call count is the number to quote in any further
eval-throughput issue; wall clock alone says nothing about which metric is the cost.
