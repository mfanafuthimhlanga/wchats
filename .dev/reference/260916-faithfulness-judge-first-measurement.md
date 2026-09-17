# The faithfulness Judge measured for the first time, and it does not beat chance

Run 735fb9fa on the five-section corpus, re-judged on 2026-09-16 with the 4096-token
completion cap (#283) into run 479d53bc, scored against the owner's 30 faithfulness rows
labelled twice blind. Read this before touching the faithfulness gate or its judge.

## The numbers

| | |
|---|---|
| labelled rows with a judge verdict | 30 of 30 (19 of 30 before the cap fix) |
| owner self-agreement | 29 of 30, ceiling interval [0.37, 1.00] |
| judge kappa against the owner | 0.020, interval [-0.19, +0.25] |
| owner minus judge | [+0.36, +1.13], the judge is distinguishably worse |
| both fail | 1 |
| judge fail, owner pass | 12 |
| judge pass, owner fail | 1 |
| spend | 0.12 USD, 147 calls |

The both-fail cell is one row, so this is the judge and not the agent.

## What the labels measured

The owner said after scoring that the retrieved text was too hard to read row after row,
so some faithfulness passes were given because the answer was known to be correct, not
because every claim was found in the contexts. Some of the twelve rows above may be
answers that are right and unsupported, which is the case faithfulness exists to catch.
The kappa therefore compares a judge measuring support with a labeller partly measuring
correctness, and it decides nothing about the gate. The next pass uses a page that
shows each claim beside its best-matching passage.

## The 12 rows the judge fails and the owner passes, by stored score

0.792, 0.786, 0.778, 0.767, 0.706, 0.692, 0.676, 0.618, 0.606, 0.600, 0.500, 0.444.
The gate is 0.80. Ten of the twelve sit between 0.60 and 0.79. Ragas faithfulness splits
the answer into claims and scores the fraction it can pin to a chunk; the owner reads
support at the answer level. The one row the judge passed and the owner failed scored
0.867 and stated a "no" where the project has only warn and block.

## The relevance Judge on the same run

`relevance-judge-v1` passed 48 of 49 rows; the owner passed 30 of 30 labelled. No human
fail exists to calibrate against, which is why relevancy is reported and not gated
(ADR 0014). The Ragas figure on the source run was 47 fails of 49.

## Where the files are

Sheets and the v2 artifact on `chore/labels-735fb9fa`; the per-dimension harness that
wrote the artifact is on #276. `calibrate_run.py --score 479d53bc... --labels-from
735fb9fa...` reproduces it against the staging tenant, spending nothing.

## The third pass, 2026-09-17, read sentence by sentence

The owner labelled the same 30 rows again on the page that edges each sentence of the
answer by its word overlap with the retrieved text and lights the carrying passage on a
click. Reading support rather than correctness moved three rows and the sheet now reads
29 pass, 1 fail. Two earlier fails became passes because the answer is carried by the
contexts even where the owner knows the concept does not exist in the project, and one
earlier pass became a fail because retrieval never fetched the passage the answer needed.
The judge against this pass:

| | |
|---|---|
| judge fail, owner pass | 13 |
| judge pass, owner fail | 1 |
| both fail | 0 |
| kappa point | -0.07, and the interval is not a measurement |

One fail label cannot anchor a kappa interval, so the harness refuses the figure: 37% of
bootstrap resamples carry no information. What the pass does show is that the 13 rows
the judge fails at stored scores 0.44 to 0.79 are answers the owner reads as carried by
the retrieved text, sentence by sentence. Ragas counts claims and the owner reads
support, and the gate at 0.80 sits inside the range where those two readings part.
Calibrating the judge needs rows with unsupported claims in them, which this corpus
does not contain (BACKLOG 8.4).

The page is `tests/evals/calibration/page/`, built from a run's sheet by `build.py`, and
the three passes sit beside each other in `runs/735fb9fa.../` as `human_scores_pass1.csv`,
`human_scores_pass2.csv` and `human_scores.csv`.

## The seeded rows, 2026-09-17: the owner passes answers built to be unfaithful

Ten answers from the run, each with one sentence added that the retrieved text does not carry,
checked absent by word before labelling. Ground truth by construction: all ten are unfaithful.

| | |
|---|---|
| owner, on the page, sentence by sentence | 9 pass, 1 fail |
| the one owner fail | the row that was already a retrieval miss |
| judge, production path with the 4096 cap | scores 0.36 to 0.875 |
| judge fails at the 0.80 gate | 7 of 10 |
| judge fails at 0.88 | 10 of 10 |
| real owner-passed rows a 0.88 gate would also fail | 18 of 29 |

Two things follow. The owner's faithfulness labels are not support labels, on a flat sheet or on
the page: nine answers each carrying a planted claim were passed. So every kappa above compares the
judge with a labeller measuring something else, and none of them says anything about the judge.
Against construction truth, the judge caught seven of ten planted claims at the gate and all ten
below 0.88, and the three it missed at 0.80 are answers where the planted claim is one of about
eight, which is what a claim-counting metric does.

What the judge cannot be told apart from is the 13 real rows it fails at 0.44 to 0.79. Those may
be claim-counting on long answers or real unsupported claims; the owner's reading cannot say, and
the seeded set shows why. The 40-row score is on `seeded_unfaithful.csv` and
`judge_scores_seeded.csv` beside the sheets, kappa 0.00 against the owner, which is the labeller and
not the judge.
