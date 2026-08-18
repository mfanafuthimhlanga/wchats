# 8.2a and 8.2b · judgement stops sampling, and the gate stops being Spearman

**Landed 2026-08-18 on `chore/m0-gate-followups`.** Two changes that had to happen before the
re-capture, because both of them poison every number downstream if they land after it.

**Still open: `8.2c`, confidence intervals.** The harness prints point estimates, and the practice
says never to quote one. Until it lands, no k can be shown adequate or inadequate.

## 8.2a. A verdict samples at 0. Content does not.

Measured before the change: `grep -rn "temperature" app tests/evals` returned **nothing**. Every LLM
call in the platform sampled at whatever the provider defaults to, including the Actor gate that
runs synchronously before money moves. The same proposed action could be approved on one call and
refused on the next with nothing in the code, the conversation or the arguments having changed.

Nine call sites now send `temperature=0`:

| Site | What it returns |
|---|---|
| `validation_service` x3 | Gatekeeper, Auditor, Strategist verdicts |
| `actor_seam` | the gate before money moves |
| `red_team_service.classify_severity` | the label that decides whether a finding blocks a deploy |
| `strategy_service.run_strategist` | a verdict on a corpus |
| `eval_service`, `retrieval_eval` | the Ragas `InstructorLLM`s, through the same `**kwargs` seam `thinking` already uses |
| `tests/evals/judge.py` | the judge the calibration harness correlates against a human |

**Three are deliberately left sampling, and that half is the one worth reviewing:**
`scenario_service` generates eval scenarios, `red_team.py` generates attack messages,
`retrieval_service._expand_query` generates query variants. Twenty red-team probes at temperature 0
are one probe run twenty times. A later blanket edit adding `temperature=0` to every
`messages.create` in the tree would look tidy, pass every other test, and collapse the red-team
suite's coverage, so **the split is asserted**, not left to whoever edits a call site next.

`_expand_query` is the closest call. Deterministic expansion would reduce measured variance in
reliable@k, which is tempting and wrong: `7.34` decided the corpus measures the SERVED path, so the
variance a customer actually experiences belongs in the number.

## 8.2b. The gate moves to Cohen's kappa, and the human column goes binary

**This supersedes AI-SPEC §5.2. It is an owner decision, taken 2026-08-18**, and it came out of the
owner asking a question rather than out of a review: *"tutorial said human calibration starts binary
with good/bad, if we split to scale of 5 how is that more beneficial to judge calibration"*.

It is not more beneficial. It is less, and there were two defects stacked in one number.

**The scale.** A human cannot hold 1-5 steady over many rows; the same quality gets a 3 one hour and
a 4 the next. Measured while answering: the judge already returned **both** a verdict and a score,
`run_evals.py` already gated on the verdict, and `compute_correlation.py` was the only 1-5 consumer.
So the binary signal existed on the judge side the whole time and only the human column was wrong.

**The statistic.** Spearman is not chance-corrected, and on a mostly-good corpus most of the
agreement it measures is luck. The concrete failure, now `test_a_judge_that_passes_everything_is_
refused`: a judge returning PASS to every input ranks in perfect agreement with any human whose
scores happen to rise, so the shipped harness reported `rho = 1.000` and *"safe to trust automated
results"* over a judge that was not reading the response. That judge scores kappa 0.

```
gate       cohens_kappa >= 0.6 on (human_verdict, judge verdict)
reported   matthews, the 2x2 confusion matrix, spearman rho
```

Three decisions inside that are worth the diff:

- **The confusion matrix prints FIRST**, before any coefficient, because each cell prescribes
  something different and the both-fail cell is the one that stops someone tuning a judge when the
  product is what is broken. A coefficient cannot point at a cell.
- **Undefined is not zero.** Both raters passing everything gives kappa NaN, reported as NOT
  CALIBRATED YET. Returning 1.0 there would be the most dangerous number this file could produce: a
  corpus of twenty good responses would certify any judge that says PASS.
- **Matthews is reported and never gated.** Kappa collapses on imbalanced data, which is the data
  this project has. Switching the gate to whichever statistic looks better is how a gate stops
  meaning anything, so the collapse is shown by a test rather than routed around.

`0.6` is a **choice, not a measurement**. The practice's bands put it at the floor of "substantial",
and nothing in this repo has ever produced a kappa, so there is no observed distribution to set it
against. Move it when there is one.

## What the owner has to do differently

Label **binary**, pass or fail, and write **why** in `notes`. The 1-5 score is optional and feeds
the reported Spearman only. Ten rows, every cell still empty; nothing in this repo may fill them.

## Deviations

- **AI-SPEC.md was not edited.** The supersession is recorded in `compute_correlation.py`'s
  docstring and in BACKLOG `8.2`. Editing a frozen spec artifact is a bigger decision than this
  change, and the archive is explicitly frozen.
- **The calibration sheet's notes were rebuilt** from the scenario descriptions. The originals
  carried em-dashes that did not survive a round trip through the CSV.
- **`test_the_pair_rate_floor_is_a_floor_and_not_a_ban` needed its stub changed**, not just its
  assertions: it hardcoded `verdict: "PASS"`, which is now exactly the pathological judge another
  test in the same module refuses. The stub's verdict follows its score.

## Not proven

- **That DeepSeek's Anthropic-format endpoint honours `temperature`.** The tests pin what is SENT.
  Establishing the rest needs a live run, and DeepSeek is the default provider (`0.7`), so this is
  not an Anthropic question.
- **That kappa 0.6 is the right floor.** No kappa has ever been computed here.
- **Any of it end to end.** No human has labelled a row, so the harness has still never produced an
  agreement number of any kind.
