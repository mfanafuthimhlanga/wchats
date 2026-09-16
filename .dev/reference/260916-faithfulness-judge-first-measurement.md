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
