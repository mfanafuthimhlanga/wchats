# 0013: Relevancy is judged, not embedded, and faithfulness gates at 0.80

Status: accepted. Built in #274 for the measurement #270 produced on run `0a99f7ab`,
2026-09-15. Supersedes the instrument half of ADR 0010, whose resolver and whose rule about
which metric reads the rewrite both stand. Related: #58, #60, ADR 0009.

## What was decided

The gated `answer_relevancy` metric is produced by a Judge this repo owns: one forced tool
call over the question and the response, returning pass or fail with a one-sentence reason,
under the owner's rubric.

> The response answers what was asked, directly. FAIL when it answers something else,
> dodges, or pads. Do not judge whether it is true.

Ragas answer relevancy keeps running and keeps its ledger purpose. Its figure is reported
under a new key, `ragas_answer_relevancy`, which `threshold_for` returns None for, so it
gates nothing and carries no verdict.

`EVAL_FAITHFULNESS_THRESHOLD` moves from 0.90 to 0.80.

## Why the old instrument had to go rather than be tuned

Ragas answer relevancy does not read the question and the response as a pair. It asks a
model to write questions the response would answer, embeds those, and reports their mean
cosine similarity to the question asked. The quantity it measures is how close a
back-translation of the answer lands to the question in voyage-3 space. That is a
reasonable proxy and it is not the thing the gate claims to check.

The owner labelled run `0a99f7ab`. The Judge failed 34 of the 41 rows it scored; the owner
failed 18 of 46. A gate disagreeing with the person it exists to protect on nearly half the
corpus is not a gate that needs a different number. Every threshold in [0, 1] keeps the same
ranking, so no threshold turns that instrument into agreement.

The new Judge is the shape every other Judge in this codebase already has: a single typed
tool call, temperature 0, forced tool choice, its own purpose in `PURPOSE_ROUTES`, billed to
the run's ledger (ADR 0008). It also cuts the metric from three judge calls plus four Voyage
embeddings per row to one judge call, which is the cut ADR 0009 option B described for the
whole metric set, taken here for one metric where the instrument had to change anyway.

## Why the gated key did not move

The 46 owner labels, both calibration sheets and `calibrate_run.GATED_METRICS` name
`answer_relevancy`. Renaming the gated key would orphan all of them and the calibration work
would restart from an empty sheet. The key stays and the instrument behind it changes, which
is exactly the case `eval_results.judge_identity` was added for (#47, tenant 0023): a row
written by the new Judge carries `prompt_version = "relevance-judge-v1"` and one written by
the old carries `ragas-<version>`, so no reader has to infer which instrument produced a
verdict from the date on the run.

Two consequences a reader meets. A relevancy score is 1.0 or 0.0 now, never 0.73, so any
series over both sides of this change shows a step that is the instrument and not the agent.
And `EVAL_RELEVANCY_THRESHOLD` no longer picks a point on a scale: it only has to sit
between the two values, which any number in (0, 1] does.

## Why 0.80 for faithfulness

Seven rows on run `0a99f7ab` are owner PASS and Judge FAIL on faithfulness. Their scores run
from 0.56 to 0.89. All seven sit under the old 0.90, and the density is at the top of that
range: an answer that carries the retrieved fact and paraphrases one clause scores in the
0.8s under ragas' statement decomposition, and 0.90 called that ungrounded.

0.80 keeps the low end of the disagreement failing, which is where a real grounding failure
sits, and stops the gate refusing a deploy over a paraphrase. It is where that run's
disagreements sit, not a round number chosen for comfort. The next labelled run re-reads it;
the number belongs to a measurement and moves when the measurement does.

## The re-judge path

A Judge change strands every verdict measured against the old one. `rejudge_eval_run` takes
a finished run and scores the two gated metrics over its stored `eval_samples`, into a NEW
`eval_runs` row carrying `source_run_id` (tenant migration 0030). No agent turn runs, so the
answers under judgement are the answers the labels describe. The source run's rows are never
written, because destroying the measurement the labels were taken against is the one thing a
calibration cannot survive.

It is idempotent on the pair (source run, instrument), where the instrument is every Judge
it pays for and every threshold it writes against. Re-running it unchanged returns the run
that exists; re-running it after any model, effort, prompt or gate moves is a different
measurement and is paid for. The gate belongs in the key even though a gate move restates
no verdict already written down, because the next rejudge decides its verdicts against the
new number and returning the earlier run would hand back a measurement against the old one.

## What this does not claim

No calibration figure. #58's artifact measured the Ragas Judge and does not transfer: the
labels do, the verdicts do not.

**The gate is decided by a model label that has no calibration figure.** `answer_relevancy`
now carries a verdict a language model wrote, and nothing has measured that model against a
human on this corpus. The rubric is the owner's and the instrument it replaced was measured
failing, which is a reason to believe this one is better and not a reason to believe it is
right. The number comes from a rejudge of run `0a99f7ab` scored against the 44 doubly
labelled rows, and **that score is the condition for merging this work**, whatever it says.

The run-level Judge field is a casualty of the same change and is not a blocker. A run's
records carry two identities, `run_judge_identity` refuses to name either as the Judge
behind the run, and `EvalResult.judge_identities` carries one per gated dimension instead.
`calibration.json` is an envelope of one record per dimension, each naming its own Judge,
and `calibration_service` matches them pairwise. Nothing refuses a deploy over the answer
either way: the calibration key is reported and not gated (#54,
`deployment_service._calibration_block`). This closes #275.

**"Reported" means present in the API payload.** `ragas_answer_relevancy` reaches
`GET /agents/{id}/eval-runs` and the per-scenario results route, and the console draws four
channels and does not draw it. A reader wanting the ragas figure reads the payload, not the
screen.

The Judge's reason is produced and discarded. It is asked for because a judge made to state
one decides better, and it is logged rather than stored: keeping it needs a column, or the
revival of the `eval_results.detail` blob that tenant 0023 emptied on purpose, and neither
is in #274. It is capped at 300 characters by the schema and logged for a fail and an
unknown only, because a pass is the expected outcome and one reason per scored row would
put a corpus of model prose in the sink for no question it answers. A reader asking why a
row failed has the log line and not the row.
