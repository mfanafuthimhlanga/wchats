# Four prompt rules move the grounding pass rate from 14 to 22 of 30

The deploy gate scores faithfulness by the grounding rule (ADR 0015): each sentence of an
answer by the share of its content words the best retrieved passage carries, floor 0.4, every
number present in the retrieved text, 0.80 to pass. An agent that paraphrases, reasons or
opens with a heading fails it. Four lines in the platform template's MUST block
(`app/services/agent_prompt.py`) tell the agent to build each sentence from the passage's own
words, to state what the passages state and say what it lacks, to take every figure and name
from the passages while repeating the customer's own details as given, and to write sentences
with one CITATIONS block at the end. This note holds the measurement behind them and the
command that reproduces it.

## The number

Thirty real benchmark answers (`tests/evals/calibration/benchmark/rows.csv`), regenerated
from the same question and the same retrieved text under the platform default soul, scored by
the rule at 0.80. Model from the `agent_turn` route, effort none, seed passed to the provider.
Produced by `regen_rows.py` on the committed template, 2026-09-24.

| template | seed | pass 0.80 of 30 | sentences flagged | sentences | median answer chars |
|---|---|---|---|---|---|
| without the rules | 7 | 14 | 56 | 135 | 553 |
| without the rules | 11 | 14 | 51 | 149 | 582 |
| with the rules | 7 | 22 | 20 | 140 | 540 |
| with the rules | 11 | 27 | 13 | 139 | 488 |

Paired by row, before to after:

| seed | fail to pass | pass to fail |
|---|---|---|
| 7 | 11 | 3 |
| 11 | 13 | 0 |

Rows flip between seeds under one template too: 6 rows without the rules, 7 with them, so
the pass total carries about plus or minus 3 of noise and the 8 to 13 pass move is the rules.
Answer length is unchanged. No answer splits its CITATIONS into more than one block. Under the
rules, 14 to 16 sentences across the thirty answers are whole-sentence declines, and one
answer at seed 11 is a bare decline (`e54b8c25`, "what should I do if I spot one myself",
where the retrieved text does mention recording a claim as `BLUFF`).

An earlier wording of the rules, measured with a scratchpad harness on 2026-09-24, passed
24 and 26 of 30 against a 16 baseline but halved answer length, produced bare declines where
the documents did answer, split CITATIONS blocks in three answers, and forbade a customer's
own booking details. The wording above replaced it before anything was committed.

## What the harness is not

- **Not the live loop.** No conversation history, so the ten follow-up questions are
  answered cold; no tools, so clarify and escalate cannot happen; the retrieved text as one
  chunk where the live retrieve tool frames and caps several; the default soul. The stored
  answers in `rows.csv` came from the staging agent's live loop under its own soul and pass
  8 of 30 against the same rule. How much of the gap to 14 is the soul and how much the
  loop is unmeasured.
- **Not a quality judgement.** The rule rewards quoting. An answer that quotes the wrong
  passage passes (`2eeef1f2` answers "how a customer message moves through W Chats" with the
  owner's four-step journey). The claims column is what a person reads.
- **Thirty answers, two seeds.** The paired counts are the evidence; the totals alone are
  within one seed of noise of each other at the top end (22 against 27).

## Reproduce

From `apps/api`, spending one model call per answer:

```bash
.venv/Scripts/python.exe tests/evals/calibration/benchmark/regen_rows.py --spend --seed 7
.venv/Scripts/python.exe tests/evals/calibration/benchmark/regen_rows.py --spend --seed 7 --without-rules
```

Each writes `regen-<label>-seed<seed>.csv` beside `rows.csv` and prints its row of the table.
The default output files are ignored by git. `tests/unit/test_agent_prompt.py` pins the nine
phrases that carry the rules; deleting the decline sentence was observed red and restoring it
green on 2026-09-24.

## What moved with it

The worst permitted turn's spend, re-measured by `tests/unit/test_turn_budget_ceiling.py`:
$0.200549 through the call the guard reads and $0.244893 for the whole turn, against
$0.200019 and $0.244260 before. The $0.40 ceiling is 1.99 times the guard figure.

## On staging, the live loop

One eval run on the Bantuson agent (`ee8087ed`) with the template merged (`main` at
`77bd663`), 2026-09-24, run `0897e93b`, 60 agent turns in 25 minutes. The before figure is a
rejudge of the previous run `09941b0f` under the same rule (`ed6cb31d`, no model call), so
both columns are the rule over the same 50 scenarios the results route returns.

| | before, old template | after, new template |
|---|---|---|
| pass 0.80 of 50 | 17 | 30 |
| median faithfulness | 0.708 | 0.866 |
| flagged sentences | 175 | 63 |
| golden dataset pass / fail / unmeasured | | 35 / 25 / 0 |

Paired by scenario: 17 fail to pass, 4 pass to fail. The agent's soul columns are empty, so
the gap between the harness's 14 of 30 and the stored 8 of 30 was the live loop, not a soul.

The gate still blocks a deploy: 25 golden scenarios fail and one failed golden is enough.
The next lever is the failing answers themselves. The 63 flagged sentences are on the claims
route for `0897e93b`, and the bench (`build_claims.py`) renders them for a reading sitting.
