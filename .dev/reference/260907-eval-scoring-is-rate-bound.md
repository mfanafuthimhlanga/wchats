# Eval scoring on staging is rate-bound, not latency-bound

Measured 2026-09-07 on agent `ee8087ed` (8 documents, 149 chunks, 31 scenarios: 11
golden, 20 exploratory), scoring through `_score_samples` on the production judge route.

| in flight | scored 5 | 10 | 15 | 20 | 25 | 31 |
|---|---|---|---|---|---|---|
| 4 (eval run `2dab3550`) | 618 s | 996 | 1359 | 1766 | 2407 | 2693 |
| 8 (checklist run `52356258`) | 576 s | 1290 | 1320 | 2047 | 2640 | 2676 |
| 4, one key (checklist run `a604e1dd`) | 544 s | 953 | 1335 | 1698 | 2415 | 2661 |
| 4, four keys, one OpenAI project each (checklist run `ec85275a`) | 587 s | 1020 | 1427 | 1782 | 2385 | 2715 |

Doubling the samples in flight changed the total by under one percent. The worker logged
an SDK `Retrying request to /chat/completions` every two to four minutes at both settings.
The calls share a rate, so overlapping them queues more and scores no faster.

Splitting the four judge purposes across four OpenAI projects, one key each (#218, ADR
0009 option A), changed the total by two percent, and upward. The worker confirmed
`judge_keys=4` at the start of run `ec85275a` and logged nine SDK retries over the 45
minutes, about the count on one key. The projects share the wall. Each of the three new
projects sits at OpenAI's default usage tier for a fresh project, so the next thing to read
is the tier on each project's Limits page. Until a project shows a higher tier than the
original, option A is measured and has not paid.

What this decides:

- `EVAL_SCORING_CONCURRENCY` stays at 4. Higher values add retries and nothing else.
- A key per judge purpose stays in the code (#218) and stays unset on the worker until a
  project's tier is raised; set, it is four keys sharing the same wall.
- The checklist ceiling of 2700 s cannot fit 31 scenarios on this judge path. Run 4 blocked
  on `eval_did_not_finish` at 2710 s while the eval completed at 2676 s of scoring plus four
  minutes of answering. The options are in the issue below.
- A scoring rate is about 87 s per scenario at this corpus size, so a ceiling that fits
  should scale with the scenario count rather than sit at a constant.

The first run that measured, `2dab3550`, also gave the calibration data #58 was waiting for:
golden 11 of 11 scored, 0 passed; faithfulness 0.83, answer relevancy 0.75, precision 0.78,
recall 0.79. The pass rule needs faithfulness and relevancy both at 0.9 or above, and
relevancy failed every authored pair, between 0.58 and 0.89, while faithfulness cleared 0.9
on about half. Exploratory passed 4 of 20 with faithfulness 0.91 and relevancy 0.82.

Issue: #213. Related: #205, #207.
