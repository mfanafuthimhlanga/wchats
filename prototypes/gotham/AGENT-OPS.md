# AGENT OPS — the room you stand in after the agent is live

`agent.html` used to be a four-step provisioning wizard (Configure / Ingest / Evals / Deploy).
A stepper models a lifecycle with an END. Operating a live agent has no end. It is a
**flywheel**: production emits failures, failures are triaged, a triaged failure is
**promoted into a permanent regression scenario**, the suite hardens, the gate gets harder
to pass. The page is now built from that loop and from the objects an operator touches:
trace, run, suite, scenario, strategy, probe, finding, prompt version, canary.

## Research (bounded, 8 sources)

**The loop is the product, not a feature.** Error analysis is: gather traces from
production, open-code them in free text, cluster the notes into a *failure taxonomy*,
count each category, then turn the categories into evaluators and the traces into
regression datasets. Hamel Husain calls the taxonomy step "the most important step" and
says a purpose-built annotation interface is "the single most impactful investment you can
make." [1][2] *Applied LLMs* names the same loop **the data flywheel** (§3.3.2 "Build
evals and kickstart a data flywheel") and insists evals re-run on every prompt, RAG or
pipeline change — i.e. they are regression tests. [3]

**The vocabulary the tools agree on.** trace / span / dataset (+ dataset item) /
experiment / score / **annotation queue** / online eval. LangSmith's annotation queues let
a reviewer label a trace and **add it directly to a dataset**; Braintrust versions prompts
as objects and **blocks deploys when scores regress**. That is exactly the "grade a trace,
it becomes a scenario, the gate gets harder" motion. [2][4]

**RAG metrics that are real.** Order-unaware: precision@k, **recall@k**. Order-aware:
**MRR**, **nDCG@k**. Reranker quality is measured as **lift** (BM25 → vector → hybrid →
reranker, reported as a delta on nDCG@10 / recall@10). Generation side: **groundedness /
faithfulness**, **citation coverage**. [5] Ragas 0.4.x supplies faithfulness, answer
relevancy, context precision, context recall.

**Context is a budget, and most of it is wasted.** *Context rot*: quality degrades as
input length grows, well before the token limit; the **lost-in-the-middle** U-curve means
a chunk placed mid-window is often ignored (30%+ accuracy drops reported), and above ~80%
window utilization information starts getting dropped. **Compaction** (summarise and
reinitialise) reclaims 70-90% of tokens. [6] So the honest RAG readouts are: window
utilization, how many retrieved tokens were **carried and never cited**, the compaction
ratio, and the **rank position of the chunk that actually got cited**.

**Red team is a programme with a grammar.** *strategy* (prompt injection direct/indirect,
crescendo jailbreak, PII extraction, policy evasion) → *probe* → *detector* → **finding**
with an ID and a severity (critical / high / medium / low). garak = static probe library +
pass/fail detectors; PyRIT = an orchestrator LLM that escalates multi-turn. [7]

**Customer-service metrics, defined.** **Containment** = share of AI conversations resolved
without escalation. **Deflection** = any conversation that never reached a human (always
higher than containment). **Escalation rate** = share handed to a person. Plus CSAT (1-5 or
thumbs), first-contact resolution, p95 latency, cost per session. [8]

Sources: [1] hamel.dev/blog/posts/evals-faq · [2] langfuse.com/blog/2025-08-29-error-analysis-to-evaluate-llm-applications ·
[3] applied-llms.org · [4] LangSmith / Braintrust / Langfuse docs and comparisons ·
[5] RAG retrieval metric surveys (recall@k, MRR, nDCG, reranker uplift) ·
[6] anthropic.com/engineering/effective-context-engineering-for-ai-agents ·
[7] PyRIT + garak red-team tooling guides · [8] AI support KPI guides (containment vs deflection).

## The six regions, and the mechanism each one steals

| Region | What it holds | Mechanism, and where it came from |
|---|---|---|
| **Live** | sessions 1,208 · containment 0.78 · escalation 0.14 · CSAT 4.3 · thumbs down 0.09 · p95 3.9 s · cost R 0.42 | **VITALS.** One channel owns one value of bone (`--ch-1..4`, four luminances, never four hues). Its numeral is **tied to the head of its own trace** by a 1px connector. A channel is only allowed to go red when it **breaches its own threshold** — thumbs down 0.09 over a 0.05 ceiling is the one red thing here, and it is what fills the bench. |
| **Retrieval health** | chunk strategy · recall@10 · nDCG@10 · MRR · reranker lift · groundedness · citation coverage · **context window utilization** · compaction ratio · cited-chunk rank · index staleness | **AERIAL.** The window is not a card, it is drawn to scale: two bars, one for 24,600 of 200,000 tokens, one for what is inside it, with the **cited** portion of the retrieved block lit and the 12,460 carried-but-never-cited tokens left dark. Two reds (recall@10 0.87, 3 stale documents) and both are *why* the traces on the bench failed. |
| **The bench** | 8 failing production traces, contact sheet left, one print under the lamp right | **DARKROOM + TERRARIUM.** The contact sheet is DARKROOM's: dense grid to scan, one large surface to judge on, a bench tally (`to grade · filed · held · dismissed`), and P / H / X grade keys. TERRARIUM supplies the law: a graded failure is **not deleted, it is filed** — promoting a trace inserts it into the suite below, increments the scenario count and the born-in-production count, and a `.voice` line states what happened. A filed trace **cannot be withdrawn**. |
| **Judgement** | run 0341 · pass 0.94 vs a 0.90 floor · 52 scenarios · **21 born in production, 31 authored** | **ORRERY.** A ledger, engraved: every scenario carries its **provenance** (`trace tr-8102` / `authored` / `finding f-0093`). Provenance is the whole argument for the flywheel, so it is a column, not a tooltip. |
| **Adversary** | 5 standing strategies · 84 probes · coverage · findings by severity | **WARDEN.** Running the programme surfaces a **critical** finding, which sets `data-gate="blocked"` and repaints the entire room over 600 ms (the mechanism already lives in tokens.css). Containing it clears the room *and files it as a scenario* — a red team finding feeds the same flywheel a customer complaint does. |
| **The prompt** | v13 canary at 10% · v12 live · v11 retired · a real diff · promote / roll back | **MERIDIAN.** Versions, not a form. The editor is not duplicated here; it links to `soul.html`. Promote and roll back act in place, never in a modal. |

## The flywheel, made physical

Select a trace → read the customer turn, the agent turn, and the judge's line on why it
failed → **File into the suite** (or `P`). The frame keeps its place on the sheet but takes
a bone check-mark and its new scenario id; a row appears at the top of the Judgement
ledger with origin `trace tr-8852`; `52 → 53`; `born in production 21 → 22`. Nothing is
thrown away and the suite only ever gets harder to pass.

## Deviations, and why

- **DARKROOM grades in colour (red χ, amber ○). Ours do not.** Under this system's law a
  colour is a claim about whether the *agent* can be trusted. A grade is a claim about the
  *operator's* judgement, so the marks are bone: filed = a lit ring and a check, held = a
  muted dash, dismissed = a struck line at 42% opacity. The only red on the bench is each
  trace's own failing score, which is a verdict about the agent.
- **The diff has no green and no red.** Added lines are bone, removed lines are struck and
  dim. A diff is not a verdict.
- **Region order follows the brief (Live → Retrieval → Bench → Judgement → Adversary →
  Prompt)** rather than leading with the bench, because the bench sits directly above
  Judgement and the promotion is visibly a *downward* move into the suite.
- **No "Configure" and no stepper anywhere.** The agent is provisioned; that job is done.

## Verified

Chromium over `file://`: zero console errors; grading promotes and increments (52 → 53 →
54); a critical finding flips `data-gate` to `blocked`, repaints `--live` and the hairlines
red, and clears back to `open`; no horizontal overflow at 1440, 1280 or 900px;
`prefers-reduced-motion` skips the probe delay and the row fade.
