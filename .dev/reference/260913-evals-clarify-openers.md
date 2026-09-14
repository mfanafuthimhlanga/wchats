# Evals: the agent asks when a question names no project

For whoever changes the agent's prompt, its tools, or the ambiguity rule, and needs to
know how the clarifying behaviour is measured. Issue #255, ADR 0012.

## The failure modes, in this corpus

The Bantuson corpus holds four projects: Mellow's Earth Elements storefront, Sentinel OHS,
W Chats and Beekeeper. A customer who has named none of them can be answered wrongly in
three ways, all observed on staging run `0a99f7ab` (2026-09-13):

1. **Guessed project.** "how do I start the dev server?" answered with the storefront's
   `pnpm dev`. Five of five openers did this.
2. **Answer with a courtesy question on the end.** "Nine to five. Anything else?" reads as
   asking to a text rule and is an answer. Never observed on this agent, found by the
   adversarial review of the first rule.
3. **Ask, then answer anyway.** `clarify` called, then `retrieve`, then a full answer. The
   customer sees an answer; the log shows a question.

## The rubric

```
Dimension:   clarifying behaviour on an ambiguous opener      Critical
PASS:        the turn's last tool call is `clarify`, whatever it retrieved before
FAIL:        the turn answered: its last tool call is `retrieve`, or it called no tool
Measurement: Code. `turn_asked_to_clarify` over the turn's tool log. No Judge.
```

Faithfulness and relevancy are not measured on these rows. A correct clarifying question
retrieves nothing, so there is nothing for them to measure.

## The dataset

Five authored openers, `ambiguous: true`, in
`apps/api/tests/evals/golden_pairs/260912-multi-turn-staging.json`, registered on the
staging agent `ee8087ed` as golden rows. Each is a question at least two of the four
projects can answer. Their references are the clarifying question the owner would ask,
held to the text rule by the writer. Labelled by the owner on 2026-09-12.

Ten follow-ups in the same file are the control: each names its project in a lead-in
turn, and the agent should answer them, not ask.

## How it gates

An ambiguous golden that answered is a `golden_failure` under the existing verdict rule,
so the deploy blocks. The first run blocked on all five. The prompt change on #255 is
what is expected to flip them; the next staging checklist run is the measurement.

## What is not measured yet

Whether the agent asks on a question that IS bound by an earlier turn, which would be a
false clarification. The ten follow-ups measure it indirectly through relevancy, which is
uncalibrated (#58). A code rule for "asked when it should have answered" is the same
shape as this one, on the follow-up rows, and is the next rule to add.
