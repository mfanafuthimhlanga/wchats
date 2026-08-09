# D6 P3 — what a label does downstream

**Branch:** `feat/d6-labelling-loop` (off `feat/d1-agent-invocation` `4179a5c`, **not `main`**).
**Commit:** `edb4fbb` + the honesty correction that follows it, **+ the adversarial-review fixes
of 2026-08-09** — see `.dev/reference/d6-p3-adversarial-review.md` for the findings and
`.dev/traces/260809-d6-p3-review-fixes.md` for what changed. **Every correction the review forced is
marked inline below rather than rewritten over, because the wrong version is the part worth
keeping.**
**Date:** 2026-08-09. **Plan:** `.dev/plans/260808-d6-labelling-loop.md`, section P3.

P1 gave a label its own trust tier and walled the writer off from every model. P2 built the queue
that offers unlabelled rows and the route that writes one. P3 is the question neither answered:
**once a row is labelled, what changes?**

---

## The finding that made this phase more than plumbing

`eval_service.VERIFIED_QA_PROMOTION_DECISION["reason"]` is stamped verbatim into
`eval_runs.config["verified_qa_promotion"]` on **every** run — the `"verified_qa_promotion"` key of
the dict `build_eval_run_config` returns — and returned as `promotion_disabled_reason`. Until this
commit it read:

> **Citation corrected (review, finding 7).** This said `eval_service.py:1591`. The copy is at
> `:1656`; 1591 is inside the `retrieval_config_hash` block. The document's most load-bearing claim
> was unverifiable at its own reference. It now cites the symbol, because line numbers in a file this
> branch is actively editing are stale the moment they are written — and both numbers are already
> wrong again after these fixes.

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

## What now holds the door — and it is THREE things, not two

> **Corrected (review, finding 4).** This section said two locks and named the two P3 built. The
> lock actually carrying the load was the one nobody wrote: **`promote_to_verified_qa` has no
> caller.** The review confirmed the pin is live (adding the call to `run_eval_suite` turned exactly
> one test red and nothing else). Naming two locks while omitting the strongest makes a reader
> mis-rank the risks in precisely the direction that matters — it makes lifting one of the two look
> like the whole decision.

| lock | mechanism | one edit from wrong |
|---|---|---|
| **ZERO — NO CALLER** | `promote_to_verified_qa` is invoked from nowhere under `app/`. `run_eval_suite` returns a literal `promoted: 0`. **This is the guarantee today**; the two below are defence in depth behind it. | Reintroducing the call. Pinned in `test_eval_task.py` and `test_eval_service.py` — and **only** in those two, so a *third* module adding the call trips nothing. That gap is stated, not closed. |
| **RESOLVER** | `select_promotion_candidates` gates on `eval_scenarios.source` — the *question's* origin, which labelling never writes. | Swapping it to `label_trust_tier(scenario)` — the resolver **P1 itself argues is right for reasoning about an answer**. **The hazard is LATENT, not live (review, finding 12):** `label_trust_tier()` and `is_human_labelled()` have zero production callers and no selector projects `label_trust_tier`, so every production scenario dict falls through to the source tier regardless of which resolver is named. The swap changes nothing outside a unit test's hand-built dict. **`BACKLOG 4.12` is the change that makes it real**, and the two must land together. |
| **DECISION** | `VERIFIED_QA_PROMOTION_DECISION["enabled"] is False`, consulted **last**, refusing with `PROMOTION_DISABLED_REFUSAL = "promotion_disabled:eval_only"`. | Flipping the flag. Which is the point — it is now a *decision* with a name, not an emergent property nobody chose. |

The resolver and the decision are pinned by separate tests (M1 and M2 below). A wall whose two
bricks fail together is a wall with one brick.

**Both were mutable module-level dicts, and the review found no absence pin watching either** — while
the label writer, a strictly less dangerous surface, carries four plus a route-level credential
guard. Two assignments from any module in the process lifted both for the life of that process, and
four call sites already performed exactly that mutation via `monkeypatch.setitem`, so the shape was
idiomatic. Both constants are now `MappingProxyType`; the assignment raises. A proxy cannot stop
`eval_service.X = {...}` — rebinding the module attribute — so
`test_label_downstream.py::TestTheLocksAreNotOneAssignmentAway` AST-scans every module under `app/`
for the subscript, rebind and `.update()/.setdefault()/.pop()/.clear()` shapes.

### Why the decision gate is LAST rather than an early `return []`

> **THIS WAS THE REVIEW'S ONE HIGH FINDING (finding 1), AND THE PARAGRAPH BELOW IS WRONG.** It is
> kept verbatim because a false justification stamped in four places is worth being able to
> recognise later. P3 removed a stale justification and replaced it with a new one that was never
> true.

> ~~`refusals[PROMOTION_DISABLED_REFUSAL]` is a measurement: **"how many rows would have been written
> into `verified_qa` if the owner flipped the decision."** That is exactly the number the owner needs
> in hand to judge the flip later, and it is the plan's own next step ("stop and measure what mining
> actually yields"). An early return reports the same zero promotions and destroys the number.~~

**Probed directly against the shipped configuration** — all four schema sources, every row carrying
`label_trust_tier='human_authored'`, a non-empty owner answer and 1.0/1.0 scores — the count is `0`
and is structurally always `0`:

```
refusals = {'trust_tier:model_generated': 1, 'trust_tier:customer_negative': 3}
PROMOTION_DISABLED_REFUSAL count: 0
```

Three separate reasons, any one of which is sufficient:

1. **Gate 1 runs first and refuses everything.** `is_promotable_to_verified_qa(source)` refuses every
   source the schema allows, so no row reaches gate 3. What the number really answers is "what would
   flipping the decision promote **given the resolver gate is lifted too**" — two edits, not one.
2. **Nothing under `app/` calls the function.** Neither `select_promotion_candidates` nor
   `promote_to_verified_qa` has a production caller, so the number is never computed.
3. **`run_eval_suite` does not return `refusals`.** Even computed, there is nowhere to read it from.

An owner shown `0` concludes flipping the decision promotes nothing. What `0` means is "the resolver
refused them all before the decision was asked". And the only test that ever showed the count
non-zero first did `monkeypatch.setitem(SCENARIO_SOURCE_TRUST_TIER, 'mined', 'human_authored')` — it
was demonstrated **exclusively inside the complement of its own blind spot**, which is a pattern this
repo has shipped before.

**The ordering is unchanged; the justification is replaced with the true one.** A refused row keeps
its **most specific** reason: a row held by its origin reports `trust_tier:customer_negative`, which
names what would have to change, rather than the decision's reason, which names a gate it never
reached. `promoted + refused == scored` holds under either ordering, so a promotion rate still cannot
be constructed without its denominator. The probe itself is now a test —
`test_the_decision_gate_is_never_reached_by_a_schema_allowed_source` — so the claim cannot come back.

---

## Files changed

| file | change |
|---|---|
| `apps/api/app/services/eval_service.py` | new `PROMOTION_DISABLED_REFUSAL`; `VERIFIED_QA_PROMOTION_DECISION` rewritten (+`scope`, `decided_on`, `producible_label_tier`, `refusal_reason`, new `reason`); the decision gate added to `select_promotion_candidates`; the `LABEL_TRUST_TIERS` and `promote_to_verified_qa` prose corrected. |
| `apps/api/app/worker/tasks/runtime/eval.py` | run returns `promotion_enabled`; `run_eval_suite` docstring states what a labelled row does and does not do. |
| `apps/api/tests/unit/test_label_downstream.py` | **new**, 22 tests → **33** after the review fixes. |
| `apps/api/tests/unit/test_label_provenance.py` | +2 tests → **+1** after the review deleted the duplicate (finding 2). |
| `apps/api/tests/unit/test_eval_service.py` | 2 existing tests lift both locks; **+1 test** after the review (finding 10). All `setitem` on the two locks became `setattr` of a replacement mapping, because the constants are now read-only. |

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

1. The first draft of the new `reason` prose named the writer's module and function. **Red.** The
   prose now describes the writer instead of naming it.
2. The new test file imported the writer for two assertions. **Red.** Those two tests moved into
   `test_label_provenance.py` (`TestTheWriteChangesNothingElse`), and the new file reaches the tier
   through `VERIFIED_QA_PROMOTION_DECISION["producible_label_tier"]`, which is pinned equal to the
   writer's own constant in the one module allowed to see both.

Both were guards doing their job on live code, which is better evidence that they work than any
assertion about them.

> **The second one was self-inflicted (review, finding 2).** One of the two tests needed to name the
> writer only because P3 chose to read the writer's SQL constant directly — and P2 had already
> asserted the identical fact through the **route**, which names no writer and trips no R2. That test
> is deleted; only `test_the_tier_the_writer_stamps_is_the_tier_the_run_record_names` remains, which
> genuinely must see both sides of the boundary. **The ignored-new-files control now carries one
> `--deselect`, not two** — and every hand-maintained node id in that control is a cost, because it
> is the one instrument that can see a *pre-existing* test silently changing status.

---

## What P3 asserts downstream

**1. A labelled row becomes ELIGIBLE to the eval — which is not the same as PRESENT in a run.**

> **Corrected (review, finding 3).** This read: "Fetched (`attempted` +1), carries a label (`valid`
> +1), is put to the agent, and is scored (`scored` +1)", stated unconditionally here, in the
> `run_eval_suite` docstring and in the commit message. **It holds only while the eligible
> exploratory pool is smaller than `EXPLORATORY_SAMPLE_SIZE` (30).** `_EXPLORATORY_SQL` is
> `ORDER BY RANDOM() LIMIT 30`. At 200 eligible rows a label does not move `attempted` at all — it
> changes *which* rows are drawn, and the owner's row has a 30/200 chance on any given night, with
> nothing in the run report to say it was not exercised. Every original test ran with a **three-row**
> pool, so the `4 -> 5` arithmetic carrying the claim was an artefact of the sub-cap regime.

Labelling writes the `reference_answer` the selectors' `WHERE reference_answer != ''` was excluding
the row for; that is the whole of what it does to eligibility. The owner's exact text arrives at the
scorer as the `reference_answer`. `TestLabellingMakesARowEligibleNotPresent` now covers the boundary
(pool == 30: `attempted` does not move) and the owner's actual case (pool == 200: the run is the same
size and the row is absent from it). The harness's cursor double now honours `LIMIT %(limit)s`, which
it previously ignored — that is what made the sub-cap blind spot invisible. Whether a freshly
labelled row should be drawn preferentially once is `BACKLOG 4.14`; until it is, the labelling loop's
feedback latency is unbounded above the sample size.

**2. It joins no golden set.** The label UPDATE assigns exactly
`{reference_answer, label_trust_tier, labelled_by, labelled_at}` — `dataset` is not among them,
asserted by set **equality** rather than by banned substring. `dataset_of(None)` is exploratory. The
golden selector keys on `dataset` and on none of the three label columns. Golden `attempted` is
identical before and after labelling.

> **Corrected (review, finding 2).** P3 claimed the four-column equality as new coverage, and used it
> to justify moving two tests into `test_label_provenance.py` (and therefore two `--deselect` flags
> onto the ignored-new-files control). **The assertion already existed**, shipped by P2:
> `test_eval_label_queue.py::TestTheLabelWrite::test_a_label_is_recorded_at_the_human_authored_tier`
> parses the same SET clause with a more robust regex and asserts the identical set by equality,
> driven through the **real route and the real writer** rather than off a module constant. The
> review's mutation (`dataset = COALESCE(dataset, 'golden')`) turned it red. P3's copy is deleted;
> the control now carries one `--deselect` instead of two. The R2 constraint that "forced" the
> deviation was self-imposed — P2 had already shown the fact can be asserted without naming the
> writer.

**3. Nothing an owner labels reaches a customer.** All three locks — see the table above.
`promote_to_verified_qa` opens no connection for a labelled row even with the resolver lifted.

**4. The counts stay honest.** A labelled row the agent could not answer raises the **denominator**
(`valid`) without raising `scored` — the run must never report a measurement over a population it
did not measure.

**5. A label changes what the DEPLOY GATE reads.** *(Added by the review, finding 5 — P3 stopped at
`verified_qa`, which is dead code with no caller, and never reached the live consumer.)*

```
run_eval_suite -> write_eval_results          -> eval_results on PRODUCTION
_fetch_eval_summary_sync: AVG(score) GROUP BY metric, keyed on eval_run_id and
    nothing else                              -> pass_rates
run_deployment_checklist puts eval_summary on the orchestrator payload
the orchestrator applies "all eval metrics >= 0.85" (ship) / "[0.70, 0.85)" (warn)
```

**Where the threshold actually lives, which is one hop short of where the finding put it.**
`apply_signal_evidence_gate` never reads `pass_rates` — it is a one-way floor on the signal's
*presence* (`measured`, `agent_invoked`) and on red-team severity. The 0.85 bar is **prose in
`_DEPLOYMENT_SYSTEM_PROMPT`**, applied by the orchestrator model. The consequence is the same and
slightly worse: the deterministic half can only make a recommendation *more* conservative, so it can
never rescue a rate that labelling depressed.

And labelling depresses rates by construction: **the queue is populated with mined production
FAILURES.** An owner working it adds hard negatives to the scored exploratory population and can
refuse their own deploys by doing the work, with nothing connecting the two. The inverse is equally
live — an owner who pastes the agent's own answer back in as the reference inflates faithfulness. And
an owner-authored answer is not grounded in the retrieved corpus by construction, so `context_recall`
over labelled rows measures something different from `context_recall` over Haiku-written references,
and the run averages both into one dataset mean. That is `BACKLOG 4.12`'s argument arriving at the
deploy gate rather than only at the report. Four tests: the score reaching `eval_results`, the
aggregation having no provenance filter, the arithmetic (0.2 on the labelled row drags faithfulness
to 0.72, under the bar), and where the bar is.

### What `human_authored` is worth — and what it is not

*(Review, finding 11.)* `_label_principal` returns `f"tenant:{tenant.id}"`, so **`labelled_by`
records the ACCOUNT, not the Clerk user.** The tier asserts that a human was in the loop — the
Clerk-JWT credential guard proves that much — but it cannot say **which** human. A tenant with three
console users has three people writing into one queue and one string identifying all of them; a wrong
reference answer found later in the golden set cannot be attributed, and no correction path can name
whose judgement to revisit. `_label_principal`'s own docstring states this and `BACKLOG 4.7` carries
it; what was missing is that this document repeated "the owner CAN produce a `human_authored` label"
without the qualification. Not P3's to fix — the Clerk subject has to be carried out of the
dependency rather than re-derived from the tenant row.

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
| **After the review fixes** (`f78524e`) | `2112 passed, 12 skipped, 30 warnings in 360.40s (0:06:00)` |
| **Ignored-new-files control** (after the fixes) | `2077 passed, 12 skipped, 2 deselected, 28 warnings in 363.04s (0:06:03)` |

Delta = **+24 = 22 (new file) + 2 (`test_label_provenance.py`)**. The control reproduces the observed
baseline exactly, so **no pre-existing test changed status**.

After the review fixes: **+35 = 33 (new file, up from 22) + 1 (`test_label_provenance.py`, down from
2 — the duplicate is deleted) + 1 (`test_eval_service.py`)**, and the control reads the same
`2077 / 12 / 2` it read before. Six pre-existing tests changed content in that run — four `setitem`
call sites became `setattr`, one assertion became a reason rather than a count, one stopped pinning
`measured` — and all six are inside the control's population, so their staying green is observed
rather than assumed. Full detail: `.dev/reference/d6-p3-review-fixes.md`.

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
| M3 | `dataset` absent from the label UPDATE | added `dataset = 'golden',` to the SET | ~~`test_the_label_write_assigns_exactly_four_columns_and_never_dataset`~~ — **that test is deleted (finding 2); the guard is P2's `test_eval_label_queue.py::TestTheLabelWrite::test_a_label_is_recorded_at_the_human_authored_tier`, which the review's own mutation turned red, and which was already covering this before P3 wrote a second copy** | `1 failed in 14.15s` — `Extra items in the left set: 'dataset'` | `1 passed in 12.53s` |
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

0. **Everything in `.dev/reference/d6-p3-adversarial-review.md`.** 14 findings, 1 high, 7 claims
   marked unsupported. The fixes are in `.dev/traces/260809-d6-p3-review-fixes.md`; nothing from that
   list is left open except what is explicitly recorded there as `not_done`.
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
