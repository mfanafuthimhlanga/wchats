# What two eval practitioners do that we do not

Source material, watched in full 2026-08-18 and summarised here rather than stored:

- Dave Ebbelaar (Datalumina), *How to Systematically Setup LLM Evals*, 55 min. Three levels of eval,
  and the human-to-judge alignment loop run in a spreadsheet.
- Technomanagers, *AI Evals Explained: From Basics to Advanced*, 2h32m. Two e-commerce practitioners
  on trace analysis, judge-agreement statistics, RAG retrieval metrics, and judge bias.

**Read this before planning M2 or the improvement loop.** We are ahead of both talks on gates and on
honesty discipline, and behind both on the two things that decide whether a number means anything:
how many times we run something, and how we compare a judge to a human.

## The frame we did not have: capability is not consistency

Both talks separate two questions we currently answer with one number.

| | Question | Metric |
|---|---|---|
| **Capability** | over k tries, did it EVER get this right? | pass@k |
| **Consistency** | over k tries, how OFTEN did it get this right? | reliable@k |

The gap between the two is the diagnosis. pass@k near zero means the system cannot do the task and
no amount of prompt work will help. pass@k high with reliable@k low means it can, and the work is
about variance instead.

**We run every eval scenario exactly once** (`run_evals.py`, a single pass per scenario). So every
number we have conflates the two, and a scenario that fails tells us nothing about which failure it
is. This is the highest-value change available to E2E validation and it is cheap: run k times,
report both.

It also changes what "first earned ship" can mean. A 7/7 red-team result at k=1 is a weaker claim
than it sounds: it says nothing about the eighth attempt.

## Nine gaps, each checked against our code rather than assumed

### 1. Judges run at the provider's default temperature

`grep -rn "temperature" app tests/evals` returns **nothing**. Every judge, validator and Ragas call
samples at whatever the provider defaults to. Judgement is the one task where we want the least
creativity on offer.

Set 0 on every judge call. Note what that does not buy: the masterclass cites 3 to 8 percent verdict
variance surviving temperature 0, from batching and hardware nondeterminism, so a high-stakes gate
should sample a verdict more than once rather than trust a single call.

### 2. Agreement is not chance-corrected and carries no interval

Our calibration gate is Spearman rho >= 0.75 over >= 3 pairs. Two problems the masterclass names:

- **Raw agreement overstates.** Two raters agree by chance often, especially when one label
  dominates. Cohen's kappa subtracts the chance rate. Above about 0.6 agreement starts meaning
  something; below about 0.4 the judge is not tracking the human at all.
- **Imbalance breaks kappa too.** When nearly everything is a pass, kappa collapses even for a good
  judge, and Matthews correlation is the fallback. Our corpus is exactly that shape: mostly-good
  responses with a few failures.

Three pairs cannot support any of this. MIN_PAIRS=3 is a floor against nonsense, not a sample size.

### 3. Nothing we report carries a confidence interval

Their rule: never quote a success rate as a point estimate. A rate over five trials moves by double
digits on the sixth. Quote the interval, and if it spans 60 to 95 percent, that is not a shippable
number wherever the point estimate sits.

This is our own measurement-honesty principle one level deeper, and we are not doing it. We quote
"7/7", "20/20", "2364 passed" with no interval anywhere.

### 4. Pooled rates hide the segment that matters

Both talks: a single success rate is a weighted average that conceals the hard cases. Their example
is a hospital quoting 90 percent across both appendix removals and open-heart surgery.

**Our corpus already carries the segmentation and we discard it.** Scenario files are tagged
`golden_path`, `edge`, `adversarial`, `out_of_scope`. Reporting per category turns a reassuring
number into an actionable one.

### 5. The failure taxonomy is imagined, not mined

Their process, and it is the front end of everything else:

```
read traces -> binary label (never 1-5) -> free-text "why" (open coding)
    -> LLM clusters the free text into failure categories (axial coding)
    -> count per category -> prioritise by frequency AND severity
    -> stop when new traces stop producing new categories (saturation)
```

Two details worth keeping. **Binary, not a 1-5 scale**, because a human scoring 100+ traces cannot
hold a scale steady and the objectivity leaks away. **Saturation is the stopping rule** for "how many
traces are enough", not a percentage.

Our twenty scenarios were written from imagination before the system had traffic. That was the only
option then. It is not the option now, and `2.28` (miner) and `2.4` (labelling UI) currently sit
*after* launch, which is backwards: the taxonomy loop is what lets the system survive contact with
real users.

### 6. No train/dev/test split on the judge prompt

Their discipline: roughly 10 percent of human-labelled traces become few-shot examples in the judge
prompt, about 40 percent are used to iterate that prompt, and the rest is held out and **never**
tuned against. Tuning on the held-out half produces a judge that scores 90 percent on it and falls
over on the next hundred traces.

We have ten rows, no split, and would otherwise build and validate on the same rows.

### 7. The judge's report card should be a confusion matrix, not one number

Four quadrants of judge verdict against human label, each with a different action:

| | human PASS | human FAIL |
|---|---|---|
| **judge PASS** | nothing to do | judge too lenient. Bad answers are reaching customers |
| **judge FAIL** | judge too harsh. Tighten the rubric, read its stated reasons | the AI system is the problem, not the eval |

That last cell is the one that stops a team tuning the judge when the product is what is broken. A
single rho distinguishes none of these.

### 8. Judge bias is testable and we do not test it

- **Verbosity bias.** Judges favour longer answers regardless of correctness. Testable: score the
  same answer padded with true but irrelevant sentences, and assert the verdict does not move.
- **Position bias.** In pairwise comparisons the candidate shown first wins more often. Less relevant
  to us because our judges score single responses, and immediately relevant the moment we compare two
  prompts through a judge.

Both belong in the judge test suite as standing probes, and on the judge's report card.

### 9. Reach for the cheapest eval that can answer the question

Their ladder, cheapest first: **code-based** for anything deterministic (format, length, presence,
membership), **off-the-shelf ML model** for solved generic problems (toxicity, sentiment, PII),
**LLM-as-judge** only for what is both nondeterministic and domain-specific.

Where we over-reach for a judge: **citation validity is a code-based check.** A cited chunk_id either
is or is not in the set retrieved that turn. That is set membership, and it matches the masterclass's
"hybrid" framing, where a contract's 50 dates constrain which dates an answer may name while leaving
which one matters to judgement.

This is an M4.5 unit-economics item before it is a quality one: every judge call demoted to a regex
or a small local model is recurring cost removed.

## Where we are ahead, and should not lose it

- **Levels 1 and 2 exist and are enforced.** 2000+ unit tests behind a merge gate is beyond what
  either talk assumes of its audience.
- **A model-written label may never gate a deploy.** Both talks warn about exactly this; our
  CLAUDE.md already forbids it.
- **Retrieval is instrumented** with recall@k, nDCG@10, MRR and cited-chunk rank. Keep the honest
  caveat already written into `agent_tools.py`: with no per-query relevance labels, those use the
  reranker's own selection as a pseudo-label, so they measure fusion against reranker, not truth.
- **"Unknown is never pass"** is our version of their confidence-interval argument, one level up.

## What this changes

**M2, first earned ship.** A ship verdict from k=1 runs is not evidence of consistency. M2 should
require reliable@k at a stated k, per scenario category, each with an interval.

**M4.5, unit economics.** The eval-type ladder is a cost lever first.

**M5, console.** Their strongest operational claim is that the work is looking at data, and that the
job of tooling is to remove friction from it. That reframes the ops room from a dashboard of scores
into a trace-reading and labelling surface.

**The self-evolving harness.** The loop they describe, which we do not yet close:

```
production traces
  -> human binary labels + free-text reasons        (the only human step)
  -> LLM clusters reasons into a failure taxonomy
  -> taxonomy prioritised by frequency x severity
  -> new scenarios and evals generated per cluster
  -> judge prompt meta-optimised against the DEV split only
  -> agreement re-measured on the held-out split, with kappa and an interval
  -> back to traces, on a schedule, because what good looks like drifts as
     prompts, data and users change
```

The automatable parts are the clustering, the scenario generation, the judge-prompt optimisation and
the drift check. The human labels are the anchor and stay human, which is the rule we already hold.

**Drift is what makes this "survive in production" rather than "launch".** Judge-to-human agreement
is not established once. It decays, and nothing currently re-measures it.
