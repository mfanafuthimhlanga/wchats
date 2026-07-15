# Phase 21 — Domain Notes: operator vocabulary & "what good looks like"

*Condensed from a design-session research digest (agent-ops / evals-in-production / RAG-health / red-team-as-programme). This is DOMAIN context — what practitioners consider good — to steer OPS-01..16 toward real, defensible metrics rather than vanity numbers. Not an implementation spec; the RESEARCH.md + PLAN.md own the how.*

## 1. The improvement flywheel (the product's spine) → OPS-09..12
Canonical loop (Hamel Husain / Shreya Shankar): **review traces → open-code the first upstream failure → cluster into a failure taxonomy → quantify by category → promote a failing trace into the golden/reference dataset → write an eval for it.** A single domain expert owns the taxonomy ("benevolent dictator", not a committee). A filed trace becomes a permanent regression scenario — it can never silently recur. This is exactly TERRARIUM "file it into the suite" and maps to OPS-09 (failing-trace surfacing), OPS-10 (grade filed|held|dismissed; filed is irrevocable), OPS-11 (`promote_trace_to_scenario`), OPS-12 (provenance ledger / born-in-production count).

**Judge alignment** (load-bearing honesty signal): don't just deploy an LLM judge — measure it against the human labeler and track **TPR/TNR**. Known failure: judges have high TPR (>96%) but catastrophic TNR (<25%) — they rubber-stamp bad output (agreeableness bias). Calibrate on a small human-labeled set and watch for judge drift. If the console shows a judge verdict, it should be honest about whether the judge is trustworthy.

## 2. Evals in production → OPS-07, OPS-12, OPS-16
- **Offline** = golden dataset in CI; **online** = scoring live traffic. You do NOT score 100% of traffic (a judge call ≈ the cost of the traffic). Standard: **sample 1–10% of live traces + 100% of guardrail-flagged/low-confidence traces.**
- **The CI regression gate** blocks a merge when a prompt / model / retrieval-config change regresses the golden set past threshold. Watch **cohort/slice regression** (aggregate can rise while a slice drops).
- **Prompt/version object model** (OPS-16): `prompt → version → label` (production/latest/canary). Deploy/rollback by moving a label, no code change. Verbs: canary (% routing), shadow, rollback, **pin** (model version — silent model upgrades change behaviour). History is never overwritten.

## 3. RAG-health vitals (this is a RAG agent — its instruments) → OPS-05..08
- **Retrieval:** recall@k, precision@k, context recall, context precision, MRR, nDCG@k. **Reranker lift is large & quotable** (Anthropic contextual retrieval: top-20 failure −49% embeddings+BM25, −67% with reranking). OPS-05 records the BM25→vector→hybrid→reranker delta (BM25 via native `tsvector`/`ts_rank_cd`, per CLAUDE.md — no pg_search).
- **Groundedness/generation:** faithfulness, answer relevancy (ragas 0.4.x names already used), citation/attribution coverage, hallucination rate. TruLens "RAG triad" (context relevance · groundedness · answer relevance) needs no ground truth.
- **Context engineering (newest, most differentiated):** **context-window utilization** is a real health metric because of **context rot** — every model degrades as input grows, well before the window is full (a 200k model can degrade at 50k). Over-filling *hurts*. Track retrieved-tokens vs budget, carried-but-never-cited tokens, compaction ratio (OPS-06).
- **Index health:** staleness (source newer than last embed), embedding drift / re-embed on model upgrade, dead-chunk detection, out-of-scope/refusal accuracy (OPS-08).
- **Customer-service KPIs (headline):** resolution vs deflection vs containment, escalation rate, CSAT. Benchmarks: ~two-thirds resolution = median, 70–75% strong, 80%+ best-in-class. **Thumbs up/down is near-useless raw (<1% click; "happy users close the tab")** — lean on implicit signals (rephrase, abandon, escalate, return) + judge scores, not a thumbs-rate dial that implies false precision. (OPS-02 message_feedback still captures explicit thumbs/CSAT, but OPS-03 metrics should foreground containment/escalation/latency/cost, computed from real rows.)

## 4. Red-team as an ongoing programme → OPS-13..15
- **Object model (name precisely):** promptfoo = **plugins (what to test: harm class)** × **strategies (how to attack: injection, crescendo, DAN)**. The space is **harm-category × attack-strategy**; track **ASR per cell** + pass-rate per probe → a **coverage matrix** is the natural object (OPS-13: `red_team_strategies` + `red_team_probes` + coverage rollup).
- **Findings** (OPS-14 `red_team_findings`, one row/finding: severity, status): cluster, dedupe, assign severity, track to closure. **A critical finding blocks the release** (OPS-15: wire to `run_deployment_checklist` → `recommendation='block'` → `POST /approve-deployment` returns 422). A fixed jailbreak becomes a permanent regression probe → files into `eval_scenarios` (`source='red_team'`) — the same flywheel a complaint feeds.

## 5. Mapping to the ops-room mechanisms (from the Gotham prototype / MESH)
- **Gate is the hero (WARDEN/DARKROOM):** `data-gate="blocked"` = deploy blocked by an open critical finding — real industry practice. The finding record is the object behind the seal.
- **TERRARIUM "file it":** promote failing trace → regression test; fixed jailbreak → regression probe. A first-class ceremony, not a delete.
- **VITALS four channels** = ragas faithfulness / answer relevancy / context recall / context precision — add context-window utilization as a distinct vital.
- **ORRERY ledger** = eval provenance (born-in-production vs authored counts).
- **MERIDIAN** = prompt versions / canary / rollback.
- **Numbers to feature (mono/tabular):** resolution/deflection %, reranker lift (−49%/−67%), ASR per cell, judge TPR/TNR, sampling rate (1–10%), context-window utilization %.

## 6. Honest-empty-state discipline
Every metric OPS-01..16 surfaces must be computed from a stored row — never mock/seeded. Where a signal genuinely doesn't exist yet, say so ("not tracked yet"), don't fabricate. This is the whole point of Phase 21: turn the four honest-empty ops-room regions into real data.
