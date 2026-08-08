# D6 — the labelling loop

**Branch:** `feat/d6-labelling-loop` off `feat/d1-agent-invocation` (`4179a5c`), **not `main`**.
D6 touches `eval.py` and `scenario_service.py`, which D1 rewrote; branching off `main` would conflict
on every hunk. Consequence: this stacks on an unmerged branch. **HANDOFF's merge gotcha applies —
merge only the top of the stack, once everything below it has landed.**

**Baseline:** 1873 passed / 11 skipped / 0 failed / 366s, observed 2026-08-08 at `4179a5c`.

---

## What D6 actually is

`BACKLOG 2.4` records it as "mined scenarios are inert by construction — written with
`reference_answer=''`, selected by `WHERE reference_answer != ''`". True, and it undersells it twice.

**First: the exclusion is correct and must stay.** An unlabelled row must never be scored — the same
principle that keeps an agent out of `human_scores.csv`. `eval.py:286` pins it across module
boundaries by `test_the_scenario_is_inert_to_the_eval_selector_by_construction`. The defect is not
the selector. It is that **nothing can ever move a row out of the unlabelled state.**

**Second, and larger:** `eval_service.LABEL_TRUST_TIERS` defines five tiers, and
`SCENARIO_SOURCE_TRUST_TIER` maps all four scenario sources to `model_generated` or
`customer_negative`. Nothing produces `human_verified` (2) or `human_authored` (3). So:

| | Waiting on a tier nothing can produce |
|---|---|
| the eval | mined production failures, owner-filed failing traces and contained red-team findings are all stored and never scored |
| `verified_qa` | `VERIFIED_QA_MIN_TRUST_TIER = "human_verified"`, so `promotable_answer` can never clear the gate — the customer-facing verified-answer path is dead code today |

The vocabulary anticipated this loop. Nothing built it.

## Scope

Close the gap at the bottom: a path by which the owner supplies a `reference_answer`, at a trust tier
that says a human authored it, structurally rather than by assertion.

- `alembic_tenant` migration — label provenance on `eval_scenarios`, widening 0011's CHECK the way
  0011 itself did (introspect the constraint name, do not hardcode it).
- `scenario_service` / `eval_service` — the labelling write, and the tier it earns.
- `app/api/v1/evals.py` — list unlabelled, submit a label.
- The console queue is **P4 and gated on a decision below.**

## Phases

### P1 — the tier that does not exist yet

Give the system a way to represent "a human wrote this answer." Migration plus vocabulary.

- The tier must be carried by the LABEL, not inferred from the scenario's `source`.
  `SCENARIO_SOURCE_TRUST_TIER` reasons about where the *question* came from; a mined question with an
  owner-written answer is `customer_negative` in origin and `human_authored` in label, and collapsing
  those two is how a `model_generated` string ends up admitted on a human tier — the exact failure
  `promotable_answer`'s docstring already warns about.
- **No model may ever write at a human tier.** Structural, not advisory: the write path that stamps
  `human_authored` must be unreachable from any agent, task or judge. A guard test that mutates the
  restriction and observes red, per the repo rule.

### P2 — the queue

`GET` unlabelled scenarios, `POST` a label. Ordering is the interesting part.

- **Order by uncertainty, not by recency.** `validators.py:220` already emits judge `confidence` into
  `job_events` and it is discarded for ranking (`BACKLOG 6.4`). Surfacing the rows the judges were
  least sure about is worth 5-10x per owner label over surfacing the newest.
- A labelled row becomes eligible to the existing selector with no change to the selector.
- Report `(unlabelled, labelled, eligible)` as counts with their denominator, per the house rule that
  a rate without its denominator must not be constructible.

### P3 — what a label does downstream

- Labelled rows enter the eval. Whether they enter the **golden** set is a separate assertion, not
  inherited — `dataset` designation stays explicit, as `eval.py:372` already insists.
- `verified_qa` promotion is **decision-gated, see below.** If it stays off, P3 records the
  disablement on the run the way `eval_service` already does, rather than leaving an absence.

### P4 — the console queue (gated)

A labelling queue in the GOTHAM console. Not started until P1-P3 land and the decision below is made.
Frontend work here runs the standard gate: the design skills, then an adversarial review against the
codex before it is shown, and the four frontend gates that `BACKLOG 1.4` notes are absent from CI.

## The decision this plan turns on

**Does an owner label flow into `verified_qa`, which `retrieval_service.verified_qa_lookup` serves to
customers ahead of retrieval?**

- **Eval-only** — labels improve measurement, nothing reaches a customer. `verified_qa` stays dead
  code with its reason recorded.
- **Eval + verified_qa** — the loop pays off twice: the owner's answer becomes the served answer for
  that question. It also means **one mistyped label is served to real customers ahead of the
  retrieval pipeline**, with no eval between the typo and the customer.

**SETTLED by the owner, 2026-08-08: eval-only.** Labels improve measurement; nothing reaches a
customer. `verified_qa` stays gated off, and P3 records the disablement with its reason on the run
rather than leaving an absence a later reader has to infer — the shape `eval_service` already uses.
Turning it on later is additive, and by then there will be labels to judge the loop by, which there
are not today.

**Also settled: backend only this run.** P1-P3, then stop and measure what mining actually yields
before any console work. If the yield is zero that is a finding about `mine_production_scenarios`,
and a queue UI built for an empty queue would have been the expensive way to discover it. P4 stays
written down and unstarted.

## Out of scope

- `BACKLOG 6.5` (`message_feedback` into the dataset) — the other label source, its own change.
- `6.1` golden-set refresh policy, `6.2` per-tenant baselines. Downstream of having labels at all.
- Repairing `mine_production_scenarios`' question recovery (see Risks) unless it proves to be a blocker.

## Risks

- **`verified_qa` is a customer-facing write.** If the decision is "eval + verified_qa", the blast
  radius of a bad label is a real customer getting a wrong answer served ahead of retrieval. That
  wants a confirmation step and an audit row at minimum, and it is the reason the question is being
  asked rather than assumed.
- **Mining may produce few rows, or none.** `mine_production_scenarios` needs `jobs.conversation_id`
  to recover the question and `continue`s past every job without it — its own docstring admits the
  emit payload carries neither `conversation_id` nor `question`. A queue with nothing in it is a
  plausible outcome, and it is a finding about mining rather than about the queue. Measure the yield
  before building UI for it.
- **Stacked on an unmerged branch** whose two owner decisions are still open (`BACKLOG 0.4`, `0.5`).
  If D1 changes in review, this rebases.

## What cannot be proven here

No PostgreSQL: the migration will not be applied, the integration suite skips, and no real mined row
will ever be seen. Unit-provable; end-to-end unprovable, same standing debt as `0.2`, `2.14` and `3.5`.
