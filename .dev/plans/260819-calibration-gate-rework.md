# 8.2d · the calibration gate, reworked after four adversarial reviews

**Why this exists:** `8.2c` landed at `e21feb5` and four independent reviewers took it apart. Half (b)
of the gate does not measure what its own docstring claims. Nine of the owner-facing messages are
false on a path a first-time labeller reaches. Eleven of forty-four mutations survived.

This plan replaces half (b) and does a systematic pass over the reporting. It is not a patch list.

## The defect that forces a redesign

`judge_high >= ceiling_low`, against a self-consistent labeller, reduces to `judge_high >= 1.000`.
That requires 2.5% of resamples to contain none of the judge's error rows:

```
(1 - e/n)^n  ~=  e^-e  >=  0.025      =>      e <= 3.68
```

`e` is a count, not a rate, so **the gate is "at most 3 disagreements" at every n**:

```
   n    errors that pass    implied kappa
  12          3                0.500
  20          3                0.700
  40          3                0.850
```

Three consequences, all measured:

- `KAPPA_THRESHOLD = 0.6` was not deleted. It became `-ln(alpha/2)`, a property of the confidence
  level, looser than 0.6 below n=18 and stricter above it.
- **Labelling more rows makes a judge harder to pass.** That is backwards, and the sheet-size
  decision is the owner's one open question.
- Holding the judge fixed and drawing 200 second passes from a 95%-consistent labeller:
  **calibrated in 127 of 200.** The verdict is decided by which rows the labeller happened to flip.

The mechanism is a zero-width interval. A labeller with no self-disagreements produces
`[1.000, 1.000]`, reported as a 95% CI. From 0 errors in 20 the rule of three puts the true rate as
high as 15%, so that interval is the absence of uncertainty, not a measure of it.

## The replacement

Both bootstraps already draw **identical resample indices** (`human_ceiling` forwards no kwargs, so
same seed and same n). It is already a paired bootstrap and the shipped rule throws the pairing away.

```
(a)  the judge beats chance          judge_ci_low > 0
(b1) the labeller set a scale        ceiling_ci_low > 0
(b2) the judge is not distinguishably worse than the labeller
                                     upper-tail CI on the PER-RESAMPLE difference
                                     d_b = ceiling_kappa_b - judge_kappa_b
                                     fails when d_ci_low > 0
```

**(b1) is what the coin-flip labeller breaks.** Two reviewers independently produced a `CALIBRATED`
exit 0 from a ceiling whose interval spans zero. A labeller who cannot reproduce their own verdicts
has not set a low ceiling, they have set none, and the honest answer is that these labels cannot
calibrate a judge. It is (a) applied to the human, so it is derived exactly as (a) is.

**(b2) removes the `e <= 3.68` artefact.** The difference is computed within each resample, so the
shared first-pass label vector cancels instead of being compared across two marginals as if they
were independent. More rows now tighten the difference interval, which is the correct direction.

Worked check to include as a test: a judge wrong about 10% of rows against a perfect labeller must
fail at every n, because it IS distinguishably worse. At small n it must report "cannot
distinguish", not "fails".

## The other structural fixes

| | Fix |
|---|---|
| `beats_chance` decided by float dust: a mathematically exact 0 returns `2.66e-16`, and the shipped seed lands on the wrong side of `> 0` | compute `cohens_kappa` over `fractions.Fraction`, so the degenerate case is exactly 0. No tolerance constant |
| a lopsided corpus produces 24% hard-zero resamples, `low` pins at 0, and the run exits 1 "fix the judge" | a resample whose marginals collapsed carries no information; count it with the undefined ones rather than as a defined zero |
| `MAX_UNDEFINED_FRACTION = 0.2` is "at least 2 minority rows" at every n, and the shipped comment claiming nothing passes because of it is false: 0.2 to 0.4 turns a refusal into a pass | correct the comment, and state the rule in the units it actually has |
| the "95% CI" is a percentile over the DEFINED resamples only, so at the 0.2 boundary it covers 76% | report the coverage it has, or refuse |
| `MIN_PAIRS = 3` is provably impossible: no 3-row corpus can produce a usable ceiling; the minimum is 4 rows at a 2/2 split | derive the floor instead of asserting 3, and stop printing "3 of the 3 verdicts needed" |

## The owner-facing pass

Every one of these was reproduced by a reviewer driving the CLI.

- `agreement.py` tells the owner to fill `human_verdict_2`, a column this project decided not to
  create. It is the first string a stuck owner reads.
- A fully labelled sheet whose verdicts read `y` reports as **0 labelled**, with no row named and no
  spelling stated. The rejection reasons are computed and discarded, for both sheets. This is the
  F3 defect fixed on the header axis and still live on the value axis.
- With 6 rows labelled and no second pass, the closing headline blames "one label used for every
  row" while kappa 1.000 is printed four lines above. Following it means writing a verdict the
  labeller does not believe.
- "the HUMAN CEILING has never been measured" prints when it WAS measured and was merely unusable.
  It fires on the realistic shape: 10 rows, 8 scorable, 7 pass 1 fail.
- `--emit-second-pass` and `--check` each tell the owner to run the other. The only exit is hand
  editing a file no message mentions.
- `--help`, `-h` and any typo of `--check` fall through to the full paid judge run and exit 1.
- One em dash in a printed string crashes the CLI on a `cmd.exe` codepage, after the calls are paid
  for. It also breaks the zero-em-dash rule.
- A duplicated row in the second sheet silently overwrites a verdict, prints `5 / 4 needed`, and
  lowers the ceiling by a full point.
- Second-pass rows matching nothing in the first sheet count as progress.
- `--check` says READY without checking `ANTHROPIC_API_KEY`, without checking label balance, and
  without naming rejected rows. On the real sheet `pair_rate` is exactly `MIN_PAIR_RATE`, so one
  provider 529 discards the run and `--check` says nothing.

## The tests that do not test anything

- `_print_gate`, `_print_interval`, `_half`: delete the whole reporting surface and 59 tests pass.
- The ceiling's row set: the mutation the trace claims is red is green across both test files.
- `CONFIDENCE = 0.95`: change the tail to `(1 - confidence)` and 87 tests pass.
- `readiness`'s `second_pass_valid` and `second_pass_missing_file`: pinned only at their absent
  values, so a permanently-zero counter passes.
- `test_a_judge_that_reaches_the_ceiling_is_calibrated` passes the same corpus as both judge and
  ceiling, so `ceiling == judge` and it holds whatever the code does.
- `test_a_one_sided_second_pass_is_no_ceiling_at_all` passes because half (a) fails first; it never
  reaches the guard it names.
- Half (a)'s `> 0` boundary is pinned only by a test in an unrelated class.
- The fixture writes a perfect second pass by default, so nearly every test runs against a
  `[1.000, 1.000]` ceiling, which is the one shape the redesign removes.
- Both `inspect.getsource` guards pass while a constant named `KAPPA_FLOOR = 0.7` gates the status.

## Order

1. `cohens_kappa` on exact arithmetic, and degenerate-marginal resamples counted as uninformative.
2. The paired difference, and (b1). Both halves of the ceiling rule together.
3. The floors: derive the row minimum instead of asserting 3.
4. The message pass, driven from a test that asserts on rendered CLI output rather than on a dict.
5. Argument validation, so a typo cannot spend money.
6. Diagnostics that are computed must reach the reader: both sheets' `unusable`, and `gate["reasons"]`
   on every path.
7. Re-mutate. The bar is the reviewer's 44, not my 13.

## Exit

- Every mutation in the reviewers' tables red, and the 11 survivors named individually.
- `--check`, `--emit-second-pass` and a full run driven end to end, with the rendered output asserted
  in tests rather than read once by hand.
- `gates.py full` green.
- The false row in `.dev/traces/260818-kappa-threshold-from-data.md` corrected.
- `.dev/MASTERPLAN.md` and `.dev/PRODUCTION-READINESS.md` stop stating the M1 exit as Spearman 0.75.
