# 0009: The Judge is rate-bound, and what to do about it

Status: proposed. Drafted 2026-09-07 from issue #213 and the measurement in
`.dev/reference/260907-eval-scoring-is-rate-bound.md`. The first half of #213, a checklist
wait that scales with the scenario count, is already in; this ADR is the second half and
needs the owner's decision.

## The measurement

Scoring 31 scenarios against the four Ragas metrics on the production judge route took
2693 s at four samples in flight and 2676 s at eight. The worker logged an SDK retry to the
completions endpoint every two to four minutes at both settings. Doubling the calls in
flight changed the total by under one percent, so the calls share a rate: the provider's
limit on the judge key. About 87 s per scenario at this corpus size, whatever the worker
does.

The four metrics do not make one call each. Faithfulness decomposes the answer into
statements and then judges them; answer relevancy generates questions and embeds them;
context precision judges every retrieved chunk. The exact count per scenario is in the
tenant's `model_calls` rows for run `2dab3550`, by purpose, and is the first thing to read
before choosing below. An eval run's cost measures the same rows and was unreadable until
#208, which is why nobody had this number.

## Options

**A. Pay for rate.** Raise the provider's limit on the judge key, or route the four judge
purposes across separate keys so each has its own rate. No code beyond `PURPOSE_ROUTES`.
Keeps the Ragas Judge and its calibration work under #58 as it stands. Cost scales with
scenarios exactly as now; only the wall clock shrinks. The rate is the provider's to grant,
and a tenant with 300 scenarios meets the same wall again.

**B. One call per metric.** Replace the four Ragas metrics with four owned prompts, each
one forced tool call that returns the score and its reasons, the way every other Judge in
this codebase already works (a Judge is one typed tool call, ADR 0007). Roughly a tenfold
cut in calls per scenario, so 31 scenarios score in a few minutes. It is a different Judge:
#58's calibration against the owner's labels starts again, and Ragas's published
behaviour, which is the reason it was chosen, goes with it. Faithfulness in one call is
the hard case; the statement decomposition is what makes Ragas's number defensible.

**C. Both, in order.** Take A now to unblock the staging agent, read the per-purpose call
counts, and decide B on the number rather than on the estimate above. If context precision
alone is half the calls, its one-per-chunk loop can become one call over all chunks
without touching the other three or their calibration.

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
