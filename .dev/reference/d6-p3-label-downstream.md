# D6 P3 — what a label does downstream

**Branch:** `feat/d6-labelling-loop` (off `feat/d1-agent-invocation` `4179a5c`, **not `main`**).
**Commit:** `edb4fbb` + the honesty correction that follows it.
**Date:** 2026-08-09. **Plan:** `.dev/plans/260808-d6-labelling-loop.md`, section P3.

P1 gave a label its own trust tier and walled the writer off from every model. P2 built the queue
that offers unlabelled rows and the route that writes one. P3 is the question neither answered:
**once a row is labelled, what changes?**

---

## The finding that made this phase more than plumbing

`eval_service.VERIFIED_QA_PROMOTION_DECISION["reason"]` is stamped verbatim into
`eval_runs.config["verified_qa_promotion"]` on **every** run (`eval_service.py:1591`, via
`build_eval_run_config`) and returned as `promotion_disabled_reason`. Until this commit it read:

> "…Every scenario source the schema currently allows is model-generated or labels a negative, so no
> row is promotable **until a correction UI produces human-verified answers**."

**D6 P1 and P2 built that correction UI.** Every run since P2 landed has been recording a
justification about an absent producer that now exists.

The block comment above `LABEL_TRUST_TIERS` (`eval_service.py:208-212`) carried the same dead
premise, and stated it as a virtue:

> "…promotion is unreachable BY CONSTRUCTION rather than by an `if False`, and it becomes reachable
> the moment a genuinely human-verified source exists, **without anyone having to remember to remove
> a flag**."

That property has **inverted**. `human_authored` is rank 3; `VERIFIED_QA_MIN_TRUST_TIER` is
`human_verified`, rank 2. The tier the shipped writer stamps *clears the minimum*. "It turns itself
on the moment a human tier exists" was a feature when nothing could produce one. Against the owner's
settled decision of 2026-08-08 (**eval-only**) it is precisely the hazard.

An absence a reader has to infer is bad. **A stale statement a reader will believe is worse.**

---

## What now holds the door, and why it is two things

| lock | mechanism | one edit from wrong |
|---|---|---|
| **RESOLVER** | `select_promotion_candidates` gates on `eval_scenarios.source` — the *question's* origin, which labelling never touches (`record_human_label` does not write `source`). | Swapping it to `label_trust_tier(scenario)` — the resolver **P1 itself argues is the right one for reasoning about an answer** — opens promotion for every owner label, in one line, and looks like a bug fix. |
| **DECISION** | `VERIFIED_QA_PROMOTION_DECISION["enabled"] is False`, consulted **last**, refusing with `PROMOTION_DISABLED_REFUSAL = "promotion_disabled:eval_only"`. | Flipping the flag. Which is the point — it is now a *decision* with a name, not an emergent property nobody chose. |

Both are pinned by separate tests (M1 and M2 below). A wall whose two bricks fail together is a wall
with one brick.

### Why the decision gate is LAST rather than an early `return []`

`refusals[PROMOTION_DISABLED_REFUSAL]` is a measurement: **"how many rows would have been written
into `verified_qa` if the owner flipped the decision."** That is exactly the number the owner needs
in hand to judge the flip later, and it is the plan's own next step ("stop and measure what mining
actually yields"). An early return reports the same zero promotions and destroys the number.

The existing invariant `promoted + refused == scored` is preserved: each row is refused exactly once,
so a promotion rate still cannot be constructed without its denominator.

---

## Files changed

| file | change |
|---|---|
| `apps/api/app/services/eval_service.py` | new `PROMOTION_DISABLED_REFUSAL`; `VERIFIED_QA_PROMOTION_DECISION` rewritten (+`scope`, `decided_on`, `producible_label_tier`, `refusal_reason`, new `reason`); the decision gate added to `select_promotion_candidates`; the `LABEL_TRUST_TIERS` and `promote_to_verified_qa` prose corrected. |
| `apps/api/app/worker/tasks/runtime/eval.py` | run returns `promotion_enabled`; `run_eval_suite` docstring states what a labelled row does and does not do. |
| `apps/api/tests/unit/test_label_downstream.py` | **new**, 22 tests. |
| `apps/api/tests/unit/test_label_provenance.py` | +2 tests (`TestTheWriteChangesNothingElse`). |
| `apps/api/tests/unit/test_eval_service.py` | 2 existing tests gain `monkeypatch.setitem(VERIFIED_QA_PROMOTION_DECISION, "enabled", True)`. |

### `promotion_enabled` on the run

`promoted: 0` is **also** what an enabled run that promoted nothing reports. Before D6 that ambiguity
was harmless because the enabled state was unreachable. It is not harmless now, so the boolean
travels beside the count it explains.

### Why 2 existing tests changed

`test_machinery_still_works_once_a_promotable_tier_exists` and
`test_the_promoted_answer_is_the_label_not_the_agents_own_text` exist to prove the promotion
machinery is *gated*, not *dead*. With a third gate they must lift both locks or they would assert
the opposite of what they are for. No assertion was weakened; one `setitem` was added to each.

---

## R2 fired on this work, twice, and was not weakened

`test_label_provenance.py`'s R2 scan refuses any module in the tree — and any test module but itself —
from **naming** the writer's module or function, *including inside a string constant*, because
`import_module("app.services." + …)` is how the earlier full-dotted-path version was evaded.

1. The first draft of the new `reason` prose said `label_service.record_human_label`. **Red.** The
   prose now describes the writer instead of naming it.
2. The new test file imported `label_service` for two assertions. **Red.** Those two tests moved into
   `test_label_provenance.py` (`TestTheWriteChangesNothingElse`), and the new file reaches the tier
   through `VERIFIED_QA_PROMOTION_DECISION["producible_label_tier"]`, which is pinned equal to the
   writer's own constant in the one module allowed to see both.

Both were guards doing their job on live code, which is better evidence that they work than any
assertion about them.

---

## What P3 asserts downstream

**1. A labelled row enters the eval.** Fetched (`attempted` +1), carries a label (`valid` +1), is put
to the agent, and is scored (`scored` +1). The owner's exact text arrives at the scorer as the
`reference_answer`.

**2. It joins no golden set.** The label UPDATE assigns exactly
`{reference_answer, label_trust_tier, labelled_by, labelled_at}` — `dataset` is not among them,
asserted by set **equality** rather than by banned substring. `dataset_of(None)` is exploratory. The
golden selector keys on `dataset` and on none of the three label columns. Golden `attempted` is
identical before and after labelling.

**3. Nothing an owner labels reaches a customer.** Both locks, separately. `promote_to_verified_qa`
opens no connection for a labelled row even with lock one lifted.

**4. The counts stay honest.** A labelled row the agent could not answer raises the **denominator**
(`valid`) without raising `scored` — the run must never report a measurement over a population it
did not measure.

---

## Gate runs — observed, not asserted

Command, from `apps/api`:

```
.venv/Scripts/python.exe -m pytest tests/unit -q \
  --ignore=tests/unit/test_chunking_service.py \
  --ignore=tests/unit/test_docling_service.py
```

| run | verbatim final line |
|---|---|
| **Baseline at `1c2b471`** (this work stashed with `git stash push -u`, then popped and verified byte-identical against a scratchpad copy) | `2077 passed, 12 skipped, 30 warnings in 362.02s (0:06:02)` |
| **After** (first, pre-correction) | `2101 passed, 12 skipped, 30 warnings in 377.32s (0:06:17)` |
| **After** (final tree) | `2101 passed, 12 skipped, 30 warnings in 370.48s (0:06:10)` |
| **Ignored-new-files control** (final tree) | `2077 passed, 12 skipped, 2 deselected, 28 warnings in 365.63s (0:06:05)` |

Delta = **+24 = 22 (new file) + 2 (`test_label_provenance.py`)**. The control reproduces the observed
baseline exactly, so **no pre-existing test changed status**.

> **The brief's stated baseline of 1873 passed / 11 skipped is the branch point `4179a5c`, not
> `HEAD`.** P1 and P2 added ~204 tests to this branch after that point. The 2077/12 above was
> measured, not computed, by stashing this work and running the gate.

The control adds two `--deselect` flags for the two tests added to an existing file, because R2
forbids them from living in the new one. Without them the control would read 2079 and the +2 would be
indistinguishable from a pre-existing test changing status — which is the only thing the control
exists to detect.

---

## Mutation proofs — 12 guards, each observed red and green

Protocol per guard: mutate → run the exact selector → observe red → `git checkout HEAD -- <path>`
(unconditional; `HEAD` is `edb4fbb`, which contains the work) → run again → observe green.

| # | guard | mutation | selector | red | green |
|---|---|---|---|---|---|
| M1 | the DECISION gate | deleted the `if not …["enabled"]` block from `select_promotion_candidates` | `test_lock_two_the_decision_refuses_a_row_that_clears_every_other_gate`, `test_the_decision_refusal_is_counted_not_swallowed` | `2 failed in 17.70s` | `2 passed in 12.12s` |
| M2 | the RESOLVER gate | swapped the gate to `label_trust_tier(scenario)` | `test_lock_one_the_gate_reads_the_questions_origin` | `1 failed in 14.23s` — `assert {'promotion_disabled:eval_only': 1} == {'trust_tier:customer_negative': 1}` | `1 passed in 12.19s` |
| M3 | `dataset` absent from the label UPDATE | added `dataset = 'golden',` to the SET | `test_the_label_write_assigns_exactly_four_columns_and_never_dataset` | `1 failed in 14.15s` — `Extra items in the left set: 'dataset'` | `1 passed in 12.53s` |
| M4 | golden selector has no label clause | `AND (dataset = %(golden)s OR label_trust_tier = 'human_authored')` | `test_the_golden_selector_cannot_be_reached_by_labelling` | `1 failed in 14.37s` | `1 passed in 11.98s` |
| M5 | golden membership is asserted, not inherited | `dataset_of` returns golden for NULL | `TestGoldenMembershipIsNeverInherited` | `2 failed, 1 passed in 18.81s` — `assert 4 == 2` | `3 passed in 16.83s` |
| M6 | the denominator is the FETCHED set | `summarise_run_validity(scored_scenarios, …)` | `TestTheCountsStayHonest` | `1 failed, 2 passed in 18.91s` — `assert 4 == 5` (`valid` dropped a row it fetched) | `3 passed in 16.47s` |
| M7 | the recorded reason is current | restored the pre-D6 reason text | `test_the_recorded_decision_names_the_decision_not_an_absent_producer` | `1 failed in 18.86s` — `assert '2026-08-08' in '…until a correction UI produces human-verified answers.'` | `1 passed in 11.89s` |
| M8 | the run states the flag | removed `promotion_enabled` from the return | `test_the_run_reports_the_flag_beside_the_zero` | `1 failed in 19.01s` — `KeyError: 'promotion_enabled'` | `1 passed in 21.81s` |
| M9 | the run record stays flat (shallow copy) | added `"supersedes": [...]` to the decision | `test_the_recorded_decision_stays_flat_because_the_copy_is_shallow` | `1 failed in 14.78s` — `Extra items in the left set: 'supersedes'` | `1 passed in 15.30s` |
| M10 | the empty-label exclusion | `WHERE reference_answer != ''` → `WHERE TRUE` in `_GOLDEN_SQL` | `test_the_selector_is_the_only_thing_standing_between_the_two_states` | `1 failed in 14.97s` | `1 passed in 12.03s` |
| M11 | the owner's text survives the row builder | `"reference_answer": row[3]` → `row[2]` | `test_the_owners_answer_is_the_reference_and_never_the_prediction` | `1 failed in 19.23s` — `+ Do you refund?` | `1 passed in 20.77s` |
| M12 | the row is really fetched | `rows.extend(_cur.fetchall())` → `rows.extend([])` | `test_labelling_makes_a_row_the_selector_returns`, `test_the_labelled_row_is_put_to_the_agent` | `2 failed in 20.19s` | `4 passed in 17.02s` |

**M2 is the most informative.** The mutation is the plausible "fix" a future engineer makes after
reading P1. Under it the row clears lock one and falls straight into lock two, so the refusal reason
changes from `trust_tier:customer_negative` to `promotion_disabled:eval_only`: the test goes red
(**the swap cannot be silent**) *and* the door stays shut (**the second brick holds**). Both halves
of the two-lock argument, demonstrated in one run.

`git status --short` was empty after the last restore.

---

## One claim was corrected after its own mutation proof

M11 established that the first assertion of
`test_the_owners_answer_is_the_reference_and_never_the_prediction` is real. Its **docstring**
additionally claimed the second assertion pinned audit D1's return. **It does not.**
`_invoke_agent_for_scenarios` — real and doubled alike — sets `agent_response` from the turn with
`{**s, "agent_response": …}`, overwriting whatever the row builder put there, so reinstating
`agent_response = reference_answer` upstream would not turn it red. The docstring now says exactly
what the assertion is (a fixture sanity check) and names where D1 is actually pinned
(`test_eval_agent_invocation.py`, and `run_eval_for_agent`'s tautology refusal). Both gates were
re-run against the corrected tree; the numbers in the table above are the corrected tree's.

---

## What is NOT proven — plainly

- **No PostgreSQL server on this machine.** Migration 0016 has not been applied and cannot be here.
  No real `eval_scenarios` row has ever carried a `label_trust_tier`, no CHECK constraint has ever
  rejected anything, and every `-m integration` harness **skips** — a skip is unobserved, never a
  pass. The 12 skips in every run above are that.
- **No migration was run.** None can be, and none is claimed to have been.
- All SQL is asserted at the **string level**. `run_eval_suite` is driven in-process with every
  boundary doubled. What is proven is arithmetic, SQL shape and gate behaviour — not that Postgres
  accepts any of it.
- **No live label has ever been written**, so the yield of the loop is still unmeasured. The plan's
  own next step ("measure what mining actually yields before any console work") is untouched by P3
  and is a `BACKLOG` row, not a result.
- The decision gate is **process-local state**. A second process with a different build of
  `eval_service` would carry its own flag. Nothing in the database records that promotion was off at
  the time a row was written — only `eval_runs.config` records it, per run.

---

## Follow-ups this phase found and did not close

1. **The eval cannot report label provenance.** `run_eval_suite`'s selectors do not project
   `label_trust_tier`, so a run cannot say how many of its scored observations rested on an
   owner-authored reference versus a Haiku-written one. Those are measurements of different quality
   averaged into one mean per dataset — the same argument `summarise_run_validity` already makes for
   golden versus exploratory, on a new axis. Deliberately not done here: adding the column to the
   selector needs a **third** fallback rung (pre-0016 tenants would otherwise fall through to the
   pre-0014 query and silently lose their golden set), and that is its own change with its own
   degradation flag.
2. **`promotion_enabled` is not read by the deploy gate.** `deployment_service` does not consult it.
   Harmless while the flag is off; worth wiring before it is ever flipped on.
3. **P4 (console queue) remains unstarted**, per the owner's "backend only this run".
