# A sentence that joins two chunks is read against both, and the planted recall holds

The grounding rule (ADR 0015) scored each sentence against the single passage carrying most
of its content words. On the first live run under the prompt rules, 11 of the 63 flagged
sentences were true to the documents and carried by two chunks together, none alone (#306).
`grounding-v2` reads a sentence under the floor once more against its best passage joined with
the passage that adds the most words the best one lacks, two at least, at the same floor, and
never for a sentence that asserts a reason or a consequence (`because`, `therefore`, `why`,
`so that`, `which means`, `favours` and the like). `SentenceGrounding.spanned_with` names the
second passage and the reason reads "passages 2 and 6 together carry 44% of its words".

## Measured, 2026-09-24, `ground_rows.py`, floor 0.4, threshold 0.80

| set | grounding-v1 | grounding-v2 |
|---|---|---|
| planted sentences flagged (recall) | 9 of 10 | 9 of 10 |
| of those, caught by words alone or with a number | 8 | 8 |
| stored benchmark answers passing | 8 of 30 | 10 of 30 |
| stored benchmark sentences flagged | 103 of 260 | 83 of 260 |
| regenerated answers under the prompt rules, seed 7 / 11 | 22 / 27 of 30 | 25 / 27 of 30 |
| live run `0897e93b`, flagged sentences | 63 | 54 |

## How the shape was chosen

The first cut took the next-best passage by its own share and had no connective guard. It
grounded 32 stored sentences and moved 8 to 12 passing, and an adversary reading of the 32
found 3 wrong (a "because", a "therefore" and a "Why" whose relation neither passage stated),
3 borderline, and 14 that passed on words the second passage shared by accident, often from
another project's chunk in the joined benchmark text. It also let one plant through the word
rule: "`pnpm preview` serves the bundle on port 4173 by default" read 0.43 across two passages
and was held only by its number. Choosing the second passage by the words it adds, with a
two-word minimum, brought the words-caught plants back to eight; the connective guard removed
the three wrong joins. The 20 sentences v2 grounds are summaries across two chunks: the queue
split, the storefront's browsing flow, the event path through the server, the exit code and the
audit log.

## What it does not fix

A sentence that spreads its words over three or more passages stays flagged; the second reading
joins two. A wrong claim that happens to share words with two passages passes, as one that
shares them with one already did, and a negation of both passages passes on their words. The 54
sentences still flagged on `0897e93b` are the agent's own reasoning and recommendations on open
golden questions, which is the owner's decision on the golden set, not the rule's. The reading
aids on the console and the bench light one passage; until #298 takes the second reading, a
sentence the gate grounds by two passages shows grey.

## Reproduce

From `apps/api`: `.venv/Scripts/python.exe tests/evals/calibration/benchmark/ground_rows.py`
prints the first rows of the table; `tests/unit/test_grounding.py::TestTheBenchmark` pins them
and `TestTwoPassages` pins the reading, the two-word minimum, the connective guard and the
unchanged floor. Disabling the second reading was observed red and restoring it green.
