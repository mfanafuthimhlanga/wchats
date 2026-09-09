# Multi-turn eval scenarios

The eval measures the first message of a conversation and nothing after it. A scenario is a
question and a reference answer, the eval turn runs with an empty history, and the Judge
scores the response against the question text alone. A customer's third message, "how do I
start the dev server", is bound by the project they named in their first, and the eval
cannot send that binding, test the agent's use of it, or score an answer that relies on it.
This plan makes a scenario a conversation whose last customer message is the question.
Issue: #227. Related: #226, #58, #203.

## Data shape

```sql
-- tenant migration 0028
ALTER TABLE eval_scenarios ADD COLUMN turns JSONB NOT NULL DEFAULT '[]'::jsonb;
COMMENT ON COLUMN eval_scenarios.turns IS
  'Prior turns, oldest first, each {"role": "user" | "assistant", "content": text}.
   The question column is the last customer message and is not repeated here.
   Empty for a single-turn scenario, which every row written before 0028 is.';
```

```python
scenario = {
    "id": ..., "question": "How do I start the dev server?",
    "reference_answer": "Run `pnpm dev` ...",
    "turns": [
        {"role": "user", "content": "I'm setting up Mellow's Earth Elements locally."},
        {"role": "assistant", "content": "Happy to help. What do you need?"},
    ],
    "ambiguous": False,   # see #226: True when the right reply is a clarifying question
}
```

`ambiguous` is a second column, `BOOLEAN NOT NULL DEFAULT false`, in the same migration.
A scenario with no turns and `ambiguous = true` has a clarifying question as its reference.

## The loop

`_run_one_eval_turn` takes `history=[]` today because scenarios must not shape each other.
It takes the scenario's own turns instead, in the shape `run_agent_loop` already reads
for a live conversation, so what is measured is what is served. Independence between
rows holds: the history belongs to the scenario, never to the previous row.

`eval_samples` (0027) gains the same `turns` column so the calibration sheet shows the
owner the conversation the answer was given in.

## Scoring

Faithfulness is unchanged: it compares the response with the retrieved contexts.

Answer relevancy compares the response with `user_input`, which today is the raw last
turn. For a scenario with turns, `user_input` becomes the question resolved against its
history, one forced tool call per scenario at scoring time ("Rewrite the last customer
message as a standalone question, using only the conversation"), stored on the
`eval_samples` row as `resolved_question` so the Judge, the sheet and the owner all read
one string. Ragas 0.4 carries `MultiTurnSample`, but its collection metrics for it are
not the four this gate is calibrated on, so the resolution step keeps the calibrated
Judge and changes only its input.

The clarifying-question check (#226) is deterministic: for `ambiguous` scenarios, the
row passes when the response is a question and fails when it answers. It is a fifth
column on the results, not a fifth Judge.

## Scenarios

- **Mined.** `mine_production_scenarios` reads a flagged conversation's messages already;
  it keeps the turns before the flagged message instead of dropping them.
- **Drafted golden.** `draft_golden_scenarios` (#203) gains an optional lead-in: for a
  chunk whose document is one of several in the corpus, the draft carries one prior
  customer turn naming the document, and the question is written as a follow-up.
- **Authored.** The golden bench takes a turns field. The owner writes the lead-in.

The set the first run needs, at least: five follow-ups with a project named earlier
(reference is the answer), five vague openers with no history (reference is the
clarifying question), and the thirty-one existing single-turn rows unchanged.

## PRs, in order

1. Migration 0028 (`turns`, `ambiguous` on `eval_scenarios`; `turns`, `resolved_question`
   on `eval_samples`), the loop passing history, `write_eval_samples` carrying turns.
   Tests: the turn receives the scenario's history and never the previous scenario's;
   a scenario with no turns runs exactly as today.
2. Question resolution at scoring time and the deterministic ambiguity check. Tests: a
   follow-up resolves to a standalone question naming the project; an ambiguous
   scenario passes on a question and fails on an answer; a single-turn scenario is
   scored on its raw question, byte for byte.
3. The miner keeps prior turns; the drafter writes a lead-in; the bench takes turns.
4. Ten authored scenarios on the staging agent, one checklist run, the sheet.

## Risks

- A resolution step is a model call inside the scoring path. It is billed under its own
  purpose, capped in tokens, and a failure leaves `resolved_question` null, which scores
  the raw question and marks the row, never the reference.
- Relevancy calibration (#58) was done on raw questions. A resolved question is a new
  input to the same Judge; the artifact stays valid because the Judge identity is
  unchanged, and the first multi-turn run's rows join the next labelling sheet.
