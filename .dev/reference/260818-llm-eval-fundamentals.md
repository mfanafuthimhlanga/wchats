# LLM eval fundamentals

The working knowledge from two eval walkthroughs, written down so it outlives the session that
watched them. This is the general practice; `260818-eval-practice-gap-analysis.md` is where our
system falls short of it, and BACKLOG section 8 is the queue.

Sources, summarised rather than stored: Dave Ebbelaar (Datalumina), *How to Systematically Setup LLM
Evals*, 55 min; Technomanagers, *AI Evals Explained: From Basics to Advanced*, 2h32m.

## 1. Why an eval exists at all

Traditional QA works because inputs are constrained and outputs are determined. A login form takes a
username and a password and has three outcomes. An LLM product takes a free text box and has
unbounded outcomes, several of which are correct at once. So the same test discipline does not carry
over, and three specific problems replace it:

- **You do not understand your own data.** At scale, users ask things nobody on the team imagined.
- **There is a gap between what you want and what you can specify.** You recognise a good answer on
  sight and cannot write the instruction that produces it.
- **Behaviour is inconsistent.** Small wording changes produce different outputs, so a system that
  passes your examples can still feel unreliable.

An **eval** is a single metric measuring one specific aspect of quality. A system has many. They get
used three ways: background monitoring, real-time guardrails, and labelling data for improvement.

## 2. Capability is not consistency

The most useful distinction in either talk, and the one that decides what work to do next.

| | Question | Metric |
|---|---|---|
| Capability | over k tries, did it EVER succeed? | pass@k |
| Consistency | over k tries, how OFTEN did it succeed? | reliable@k |

- pass@k near zero: the system **cannot** do the task. Prompt tuning is wasted effort; change the
  model, the tools, or the architecture.
- pass@k high, reliable@k low: it **can**, and the work is variance.
- A deterministic system has pass@k equal to reliable@k. The gap between them IS the AI problem.

**A single run cannot tell these apart.** Any success rate quoted from one pass per case is silent
about which of the two failures it is looking at.

## 3. The improvement loop

```
analyse  ->  collect real examples, categorise failure modes
measure  ->  turn those failure modes into metrics
improve  ->  change prompts, models, architecture
         ->  back to analyse
```

Most teams only do the third step, which is why they never get past the demo. The loop is what
compounds; a single fix does not.

## 4. Three levels of eval, by cost and cadence

| Level | What | When to run |
|---|---|---|
| **1. Unit tests** | fast assertions on structured output, categories, formats, ranges | every code and prompt change |
| **2. Human and model evaluation** | systematic review of quality, then an aligned LLM judge | weekly or fortnightly |
| **3. A/B testing** | real users, business outcomes | major releases only |

Level 1 goes further than people expect. For a multi-step pipeline, assert at every step: the
classification, the router's choice, the confidence range, the presence of a citation block, the
final API call's success. The tests to write come from the failure modes you have actually seen.

Keep a folder of **raw captured events** in the repo and run the suite against them. This is why
capturing raw events matters at all: they become the fixtures.

## 5. Choosing the type of eval: cheapest thing that answers the question

| Type | Use when | Examples |
|---|---|---|
| **Code-based** | the criterion is deterministic | JSON validity, length limits, set membership, enum values, a Luhn check |
| **Off-the-shelf ML model** | nondeterministic but generic and long solved | sentiment, toxicity, PII detection |
| **LLM as judge** | nondeterministic AND domain-specific | did it hand off to a human at the right moment, did it stay in scope, is the tone right |

The quadrant to avoid: an expensive, complex solution on a low-complexity problem. Parts of a
nondeterministic system are still deterministic, and those parts should never reach a judge.

## 6. Reference-based, reference-free, and hybrid

- **Reference-based**: a correct answer exists. Data extraction, a numeric result, a SQL answer.
  Note the reference is the ANSWER, not the method: many queries reach the same correct number.
- **Reference-free**: no single right answer. Tone, helpfulness, an open-ended summary.
- **Hybrid**, and most real evals live here: part of the answer is constrained and part is judgement.
  A contract containing 50 dates constrains which dates an answer may name, while which one matters
  is judgement. The constrained half should be checked by code.

## 7. Traces

A **trace** is the full log of one request through the system; **spans** are its steps. Read them for
three separate things: correctness, latency per step, and token cost per step.

Trace complexity tracks architecture: a single LLM call has two spans, a RAG turn has four or five,
an agentic multi-tool turn has more. The more spans, the less a verdict on the final output tells
you, because the failure could be at any one of them.

## 8. Trace analysis, which is the front end of everything

```
1. read traces            a domain expert, ideally ONE, for consistency of judgement
2. label BINARY           good or bad. Never 1-5
3. open coding            free text, in your own words, why it was bad
4. axial coding           an LLM clusters those free-text reasons into failure categories
5. count and prioritise   frequency AND severity, not frequency alone
6. stop at saturation     when new traces stop producing new categories
```

**Binary, not a scale.** Over a hundred traces a human cannot hold a 1-5 scale steady; the same
quality gets a 3 one hour and a 4 the next, and the objectivity leaks out. Binary survives fatigue.

**Frequency alone is the wrong priority order.** A rare failure in a leadership reporting bot that
produces a wrong growth number outranks a common cosmetic one, because a decision gets made on it.

**Saturation, not a percentage, is the stopping rule.** Nobody reads a thousand traces. Read until
thirty or forty more produce no new category.

## 9. Building an aligned judge

The order is not negotiable: **humans label first, then the judge is built to agree with them.** A
judge written before anyone looked at data is a metric nobody can interpret.

```
collect input/output pairs  ->  human labels them binary, with a written reason
                            ->  judge labels the same pairs, with a written critique
                            ->  measure agreement
                            ->  rewrite the judge prompt
                            ->  repeat
```

The rewrite can be automated: give a strong model the disagreements, the human reasons, and the
current judge prompt, and ask it to close the gap. That is the highest-leverage automatable step in
the whole discipline.

**Always ask the judge for its reasoning, not just a verdict.** It costs tokens and latency, and it
is what makes a disagreement diagnosable instead of just visible.

**Split the labelled data.** Roughly 10 percent become few-shot examples inside the judge prompt,
about 40 percent are used to iterate the prompt, and the rest is held out and never tuned against.
Tuning on the held-out set produces a judge that scores beautifully on it and collapses on the next
hundred traces.

**Use a model at least as strong as the one being judged.** Never a weaker one.

**Alignment decays.** Prompts change, data changes, users change, and what counts as good drifts
with them. Agreement is re-measured on a schedule, not established once.

## 10. Judging the judge

**Raw agreement overstates.** Two raters agree by luck, especially when one label dominates.

- **Cohen's kappa** subtracts the chance rate. Roughly: below 0.4 the judge is not tracking the human;
  0.6 to 0.8 is substantial; above 0.8 is strong.
- **Kappa breaks on imbalanced data.** When 95 percent of responses are good, kappa collapses even
  for a good judge, because chance agreement is already near certain. Use Matthews correlation there.
- **The confusion matrix is the report card**, because each quadrant has a different action:

| | human PASS | human FAIL |
|---|---|---|
| **judge PASS** | nothing to do | judge too lenient. Bad answers are reaching customers |
| **judge FAIL** | judge too harsh. Read its stated reasons, loosen the rubric | the system is the problem, not the eval |

That last quadrant is the one that stops a team tuning a judge when the product is what is broken.

## 11. Reporting a number honestly

- **Never quote a success rate as a point estimate.** A rate over five trials moves double digits on
  the sixth. Quote an interval, and treat a wide one as not shippable at any point estimate.
- **Never quote a pooled rate.** It is a weighted average that hides the hard segment, the way a
  hospital quoting 90 percent hides that open-heart surgery is 40 percent. Report per query type:
  exact, exploratory, long-tail, adversarial, whatever the segments are.
- **Set judge temperature to 0.** Judgement wants no creativity. Expect 3 to 8 percent verdict
  variance to survive anyway from batching and hardware nondeterminism, so sample a high-stakes
  verdict more than once.
- **Two numbers travel together**: the system's success rate, and the judge's reliability. The first
  is meaningless without the second, because an unreliable judge produced it.

## 12. RAG needs its own metrics

A verdict on the final answer cannot tell you whether retrieval or generation failed, and if the
wrong documents came back, generation never had a chance.

**Similarity is not relevance.** A query about mobile conversion and a document about desktop
conversion score high on cosine similarity and the second one is wrong. Use ranking metrics instead:

| Metric | Question | Matters most when |
|---|---|---|
| **Precision@k** | of what I retrieved, how much was relevant? | small context window, on-device, low latency. One bad document in three poisons generation |
| **Recall@k** | of everything relevant, how much did I find? | high stakes. Medical, legal. A missed document is the failure |
| **MRR** | how high did the first relevant document rank? | search and shopping, where position drives whether the user ever sees it |

## 13. Judges have systematic biases, and they are testable

- **Position bias.** In pairwise comparison the candidate shown first wins more often. Test by
  swapping the order and seeing whether the verdict moves. Worst when the two candidates are close.
- **Verbosity bias.** Longer answers score better regardless of correctness. Test by padding a
  correct answer with true but irrelevant sentences. Mitigate with explicit instruction, few-shot
  examples that punish it, or a length penalty.

Report both on the judge's report card, alongside its agreement score.

## 14. The mistakes both talks name

1. **Tool-first thinking.** Reaching for a new vector DB, a bigger model, or an eval platform instead
   of understanding the failure.
2. **Generic metric obsession.** "Helpfulness 4.2" tells you nothing. Start binary and specific.
3. **Avoiding your data.** The highest value-to-effort activity in the discipline is a person reading
   traces, and it never stops being that.
4. **Unaligned judges.** Assuming the judge works because it returns numbers.

## 15. What good looks like

You are succeeding when changes ship confidently, failures are caught before users see them, system
behaviour is understood, and improvements compound.

You are struggling when every fix breaks something else, user complaints are surprises, progress
feels like trial and error, and you cannot tell whether a change helped.
