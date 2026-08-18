# The human ceiling, and why collecting it is easy to get wrong

Any judge in this system is measured by how well it agrees with a person. That agreement has a
maximum, and the maximum is not 1.0: it is how well that person agrees with **themself**. Measure
the ceiling or a kappa has no units.

This note is for whoever collects human labels next, whether through a CSV, the labelling UI
(`2.4`), or the trace miner (`2.28`). The mechanism is in
`apps/api/tests/evals/calibration/agreement.py`; the reasoning is here because it will apply to
every labelling surface built after it, not just to that file.

## What it is

The same rater labels the same rows twice, separated, without seeing their first answers. Cohen's
kappa over the two passes is the ceiling.

A judge is then held to two tests, and both are intervals rather than points:

```
(a) beats chance      judge_ci_low  > 0
(b) reaches ceiling   judge_ci_high >= human_ci_low
```

(b) is an **overlap** test on purpose. Comparing point estimates would fail a judge for noise on a
small sheet, and comparing against a point ceiling would treat the rater's own uncertainty as exact
when it is measured from the same rows. The question is whether the judge is *distinguishably worse*
than the person, not whether it is worse.

## Blindness is structural, not procedural

**Do not put the second verdict in a column beside the first.** The rater reads their own answer
while writing the new one, so what is measured is their memory, not their consistency. The ceiling
inflates towards 1.0, and a judge is then refused for a reason that has nothing to do with the
judge.

"Don't look at the other column" is not a control. A separate artifact is.

Here that is `human_scores_pass2.csv`: `scenario_id`, `dimension`, `human_verdict`, rows shuffled,
verdict column empty, **no notes**. Pass one's notes are the rater's own reasoning about the row, so
carrying them across is carrying the answer across.

Any UI built for this owes the same three properties: the prior verdict absent, the order different,
and the rater's prior reasoning not on screen.

## Measure the ceiling over the rows the judge was scored on

Not over every row that has a second verdict.

- The ceiling caps the judge, so it has to describe the rows the judge was actually measured on.
- Both intervals are then computed at the same n. A ceiling over thirty rows against a judge over
  ten is the tighter interval by construction, and the judge fails (b) for being outnumbered rather
  than for being wrong.

If a judged row has no second verdict, withhold the ceiling and name the row. The rows a rater
happened to finish are not a random sample of the sheet.

## What it costs, and why the sheet size is the real constraint

A row is one (scenario, dimension) pair. The rater's cost is one binary verdict plus a one-line
reason, then the same rows again.

95% bootstrap intervals, measured 2026-08-18:

```
rows   judge, wrong about 2 in 10   rater's own test-retest ceiling
  10   [-0.09, 1.00]  concludes nothing   [0.19, 1.00]  too wide to cap anything
  20   [ 0.17, 0.90]  beats chance
  30   [ 0.26, 0.86]  readable            [0.52, 1.00]
```

Ten rows cannot establish anything on either side. Adding rows to a sheet costs nothing in capture;
it costs rater time, and that is the whole budget.

## Both labels must appear or there is no measurement

Kappa is undefined when one label dominates completely, because chance agreement is already certain.
A bootstrap over a one-sided set inherits it: most resamples contain one label and have no kappa at
all. Those resamples are counted, not dropped, and above `MAX_UNDEFINED_FRACTION` the interval is
reported as undefined rather than taken over the few that survived.

The practical consequence for whoever picks the rows: **choose rows that will produce `fail`s.**
Adversarial and out-of-scope scenarios are where they come from. A sheet that comes back all `pass`
reports "cannot establish" no matter how many rows it has.

## What the ceiling does not tell you

- Not whether the rater is *right*. A confidently wrong rater has a high ceiling.
- Not whether the corpus is representative. It describes these rows.
- Not a licence for a judge that clears it on ten rows. Clearing a ceiling measured at
  `[0.19, 1.00]` means almost nothing; the interval is the answer, not the verdict word.
