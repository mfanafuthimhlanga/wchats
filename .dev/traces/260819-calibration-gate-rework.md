# 8.2d · the calibration gate, reworked after four adversarial reviews

**Landed 2026-08-19** on `chore/m0-gate-followups`. Plan: `.dev/plans/260819-calibration-gate-rework.md`.
Supersedes half of `8.2c` (`260818-kappa-threshold-from-data.md`), which is now annotated with its
own false claims rather than deleted.

## What was wrong

`8.2c` deleted `KAPPA_THRESHOLD = 0.6` and replaced it with `judge_high >= ceiling_low`. Against a
self-consistent labeller that reduces to `judge_high >= 1.000`, which needs 2.5% of resamples to
contain none of the judge's error rows:

```
(1 - e/n)^n  ~=  e^-e  >=  0.025      =>      e <= 3.68
```

`e` is a count, so the gate was **"at most 3 disagreements" at every n**. It replaced one chosen
constant with `-ln(alpha/2)`: looser than kappa 0.6 below n=18, stricter above, and **harder the more
rows the owner labels**. Measured: one fixed judge, 200 second passes drawn from a 95%-self-consistent
labeller, CALIBRATED in 127 of them.

Two reviewers separately produced exit-0 `CALIBRATED` from a ceiling whose own interval spanned zero,
because nothing asked whether the ceiling was any good. The worse the labeller, the lower the bar.

## The rule now

```
(a)  the judge beats chance          judge_ci_low   > 0
(b1) the labeller set a scale        ceiling_ci_low > 0
(b2) the judge is not distinguishably worse
                                     d_ci_low <= 0, d measured WITHIN each
                                     resample as ceiling_kappa - judge_kappa
```

**(b1) is (a) applied to the human.** A labeller who cannot reproduce their own verdicts has not set
a low ceiling; they have set none, and the answer is that these labels cannot calibrate a judge.

**(b2) is paired.** Both bootstraps already drew identical resample indices by accident, so the
shipped rule was throwing away a pairing it already had. Measuring the difference inside each
resample cancels the shared first-pass label vector, is correctly calibrated where comparing two
marginal intervals is not, and removes the `e <= 3.68` artefact because it never asks an interval to
reach exactly 1.000.

## The other structural fixes

| | |
|---|---|
| `beats_chance` was decided by float dust | `cohens_kappa` computes over `Fraction`. **46 reachable tables at n <= 40** return `9.5e-17` in float for a kappa that is exactly 0, and the shipped seed landed on the wrong side of `> 0` |
| a lopsided corpus exited 1, "fix the judge" | kappa is NaN when EITHER marginal is degenerate. With one rater on a single label, `observed == expected` identically, so 0.0 arrived whatever the other rater did: an absence wearing the clothes of a finding |
| 3 rows returned `[-0.000, +1.000]` and read as a measured failure | an interval running from at-or-below chance to perfect is refused. Both bounds derived: 0 is chance, 1 is kappa's maximum |
| `CONFIDENCE` was three characters nothing pinned | `tails(confidence)` is a named function with its own tests |
| `MIN_PAIRS = 3` is provably impossible | `MIN_MINORITY_ROWS = 2`, derived from `undefined_fraction ~= e^-m`, and `--check` refuses a sheet that cannot support an interval |

## The owner-facing pass

Every item was reproduced by a reviewer driving the CLI, and every fix is now asserted against
rendered output rather than against a dict.

- The one string a stuck owner reads told them to fill `human_verdict_2`, a column this project
  decided never to build. Three of the four reviewers found it independently.
- A sheet whose verdicts read `y` reported as **0 labelled** with nothing named. Both sheets'
  rejection reasons are printed now, by row.
- The closing headline blamed "one label used for every row" over a run whose kappa was 1.000 four
  lines above. It prints the gate's own reasons instead of guessing.
- "the HUMAN CEILING has never been measured" printed over a ceiling that WAS measured and was
  merely unusable. Three distinct messages now: absent, unusable, or below chance.
- `--help`, `-h` and every typo of `--check` fell through to the full paid judge run and exited 1.
  Unknown arguments refuse before anything is imported.
- One em dash in a printed string crashed the CLI on a `cmd.exe` codepage, after the calls were paid
  for.
- A duplicated second-pass row silently overwrote a verdict and lowered the ceiling a full point.
  Neither copy wins now, and the row is named.
- Second-pass rows matching nothing in the first sheet counted as progress. Only paired rows count.
- `--check` said READY without checking `ANTHROPIC_API_KEY` in `os.environ` or the label balance.

## Mutations

**24 of 24 red.** The first sweep came back **10 red, 14 survivors** — most of them guards this
commit had just introduced. The 14 are individually named in the plan and each has a test now.

Two survivors needed evidence rather than a test:

- **float versus exact.** A brute-force sweep of every 2x2 table at n <= 40 found 46 where the sign
  at zero differs. `test_a_table_whose_kappa_is_exactly_zero_returns_exactly_zero` uses the smallest.
- **`low > 0` weakened to `>= 0`.** No corpus anywhere in the suite produces a lower bound of exactly
  0.0, which is why it survived 44 earlier mutations. `calibration_verdict` is now fed a hand-built
  interval so the comparison is tested at the boundary rather than near it.

The sharpest of the new tests: feed the same rows as both judge and ceiling, and every resample
produces the same kappa twice, so the difference interval has **exactly zero width**. Independent
resampling cannot produce that.

## Observed

```
tests/unit/test_calibration_harness.py     74 passed
tests/unit/test_agreement_threshold.py     32 passed
tests/unit/test_agreement_statistics.py    18 passed
mutations                                  24 of 24 red
--check on the real sheet                  exit 3; names the API key, the label balance,
                                           and all ten unreadable rows by name
```

## Two changes that go beyond fixing a defect

Both are visible in the diff and either can be reverted on its own.

1. **`cohens_kappa` returns NaN where it returned 0.0**, whenever either rater used a single label.
   That flipped a deliberately-pinned test from "measured failure, exit 1" to "no measurement, exit
   3". An all-`pass` sheet now reports "cannot establish" instead of "the judge failed".
2. **The write guard is a rule, not a filename allowlist.** No module outside
   `tests/evals/calibration` and `tests/unit` may name a calibration sheet. The list version was
   already wrong the day it was written and went red whenever anyone added a test file.

## Still open

- **`--emit-second-pass` is a one-way door.** Emit the sheet, add a row to the first sheet later, and
  the emitter refuses to overwrite while every message tells you to run it. The exit is hand-editing
  a file no message mentions. Fixing it means deciding whether the emitter tops up an existing sheet
  the way `capture_responses.py --runs` tops up a scenario. **Owner decision, `8.15`.**
- `8.13`: `ruff`, `lizard` and import-linter all scan `app` only, so none of this file is statically
  checked.
