# D6 P3 — adversarial review of `edb4fbb` + `fb065a2`

**Branch:** `feat/d6-labelling-loop` (off `feat/d1-agent-invocation` `4179a5c`, **not `main`**).
**Reviewed:** `edb4fbb` (code) and `fb065a2` (docs + one honesty correction), diffed against `1c2b471`.
**Date:** 2026-08-09. **Reviewer:** adversarial pass, session model.
**Companion:** the implementer's own findings are `.dev/reference/d6-p3-label-downstream.md`.

Everything below was run. Where a claim is marked disproved, the disproof is a command and its
output, reproduced here. Nothing in this document is inferred from the implementer's report.

---

## What I verified for myself, and it holds

| control | command | observed |
|---|---|---|
| **the gate** | `.venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py` | `2101 passed, 12 skipped, 28 warnings in 415.69s (0:06:55)` |
| **ignored-new-files control** (BACKLOG 2.26) | same, plus `--ignore=tests/unit/test_label_downstream.py` and `--deselect` on both `TestTheWriteChangesNothingElse` node ids | `2077 passed, 12 skipped, 2 deselected, 28 warnings in 381.57s (0:06:21)` |

Both reproduce the implementer's numbers exactly (warning count differs by 2 — 28 vs 30 — which is
run-to-run noise in `RuntimeWarning: coroutine … was never awaited`, not a test-status change).

**The 2077 baseline is corroborated without re-running it.** `1c2b471` is docs-only
(`git show --stat 1c2b471` touches six `.dev/` files and no code), so the code baseline for P3 is
`17a5774`, which `.dev/HANDOFF.md` records at 2077/12. The brief's `1873/11` is the branch point
`4179a5c`, before P1 and P2 — the implementer's correction of the brief is right.

**No path exists by which a model, agent, task, judge or fixture writes a `reference_answer` at a
human trust tier.** Searched for specifically, per the standing trap:

- `record_human_label` is the only writer of the label columns; grep over `app/`, `scripts/`,
  `alembic_tenant/` finds no other `UPDATE eval_scenarios` touching them.
- `decision_eval_service.FIXTURE_LABEL_PROVENANCE = "human_authored"` is in-memory only and is
  defused by `eval_service._is_an_eval_scenario`.
- P3 adds **no** write path. Its only new literal, `VERIFIED_QA_PROMOTION_DECISION
  ["producible_label_tier"]`, is a read-only constant in the module that already declares the tier.
- `promote_to_verified_qa` and `select_promotion_candidates` have **zero** production callers
  (`app/` grep returns only `eval_service` itself). That absence is pinned twice
  (`test_eval_task.py:566`, `test_eval_service.py:1001`) and I confirmed the pin is live — see M-C.

---

## Mutation proofs I ran myself

Protocol: `git status --short` empty → mutate → run selector → observe red → `git checkout HEAD --
<path>` unconditionally → run again → observe green. Selector for all four:

```
.venv/Scripts/python.exe -m pytest tests/unit/test_label_downstream.py \
  tests/unit/test_eval_service.py tests/unit/test_label_provenance.py \
  tests/unit/test_eval_task.py -q
```

Green before any mutation: `256 passed in 30.32s`.

### M-A — flip the decision. The mutation the implementer did **not** run.

`VERIFIED_QA_PROMOTION_DECISION["enabled"]: False → True`. This is the single most likely real
edit, and nothing in the 12 proofs covered it.

```
7 failed, 249 passed in 32.03s
FAILED tests/unit/test_label_downstream.py::TestNoLabelReachesACustomer::test_lock_two_the_decision_refuses_a_row_that_clears_every_other_gate
FAILED tests/unit/test_label_downstream.py::TestNoLabelReachesACustomer::test_the_decision_refusal_is_counted_not_swallowed
FAILED tests/unit/test_label_downstream.py::TestNoLabelReachesACustomer::test_the_recorded_decision_names_the_decision_not_an_absent_producer
FAILED tests/unit/test_label_downstream.py::TestNoLabelReachesACustomer::test_the_run_reports_the_flag_beside_the_zero
FAILED tests/unit/test_eval_service.py::TestLabelTrustHierarchy::test_promotion_decision_is_recorded_with_a_reason
FAILED tests/unit/test_eval_service.py::TestBuildEvalRunConfig::test_promotion_decision_is_copied_not_shared
FAILED tests/unit/test_label_provenance.py::TestP1OpenedNoCustomerFacingDoor::test_the_promotion_decision_is_still_recorded_as_disabled
```

Restored → `256 passed in 26.73s`. **Verdict: well covered, across three modules.** Good.

### M-B — delete lock one entirely (the resolver gate)

Removed `if not is_promotable_to_verified_qa(source): _refuse(...); continue` from
`select_promotion_candidates`.

```
3 failed, 253 passed in 28.07s
FAILED tests/unit/test_label_downstream.py::TestNoLabelReachesACustomer::test_lock_one_the_gate_reads_the_questions_origin
FAILED tests/unit/test_eval_service.py::TestPromoteToVerifiedQA::test_trust_tier_is_checked_before_score
FAILED tests/unit/test_eval_service.py::TestPromoteToVerifiedQA::test_filed_production_trace_is_refused_as_a_negative_label
```

Restored → green. **The lock is pinned — and the run also exposed F7 below**: P1's own absence pin
`test_a_human_labelled_scenario_is_still_not_promoted_to_verified_qa` stayed **green** with lock one
entirely deleted.

### M-C — lock zero (the one the narrative does not name)

Added `promote_to_verified_qa(scenarios, results["scores"], conn_str)` to `run_eval_suite`.

```
1 failed, 255 passed in 28.79s
FAILED tests/unit/test_eval_task.py::TestPromotionIsUnreachableFromTheTask::test_module_does_not_import_or_call_promote_to_verified_qa
```

Restored → `256 passed`. Note that the *behavioural* tests all stayed green while the task really
did call the promotion path — lock zero has exactly one guard, and it is a source-text scan.

### M-D — a comma-bearing column in the label UPDATE (blind-spot probe on M3's parse)

P3's `test_the_label_write_assigns_exactly_four_columns_and_never_dataset` parses the SET clause by
`body.split(",")`, which a value containing a comma could defeat. Added
`dataset = COALESCE(dataset, 'golden')` to `label_service._LABEL_SQL`:

```
3 failed, 191 passed in 34.97s
FAILED tests/unit/test_label_provenance.py::TestR3TheModelWritersCannotWrite::test_the_human_write_does_not_touch_the_questions_origin
FAILED tests/unit/test_label_provenance.py::TestTheWriteChangesNothingElse::test_the_label_write_assigns_exactly_four_columns_and_never_dataset
FAILED tests/unit/test_eval_label_queue.py::TestTheLabelWrite::test_a_label_is_recorded_at_the_human_authored_tier
```

Restored → green. The parse survives, **and the run is what exposed F2**: two of the three failures
are pre-existing tests.

Final state after all four mutations: `git status --short` empty; the five-module selector reads
`339 passed in 33.34s`.

---

## F1 — HIGH. The decision gate's stated purpose is unachievable, and the count is always 0

The ordering of the third gate is justified, in four separate places, by a measurement:

> `eval_service.py:263-267` — "while promotion is off, `refusals[PROMOTION_DISABLED_REFUSAL]` is
> exactly 'how many rows would have been written into verified_qa if the owner flipped the
> decision' — the number the owner needs in hand to make that call later"

> `eval_service.py:1879-1885` — "which makes `refusals[PROMOTION_DISABLED_REFUSAL]` the measurement
> of what turning promotion on would actually promote. An early `return []` would report the same
> zero and destroy that number."

Repeated in `eval_service.py:1929-1934`, in the commit message, in
`.dev/reference/d6-p3-label-downstream.md` §"Why the decision gate is LAST", and in the trace's
decision 2.

**It is false.** Gate 1 (`is_promotable_to_verified_qa(source)`) runs *before* the decision and
refuses every source the shipped schema allows, so no row ever reaches the decision gate. Probed
directly (temporary test file under `tests/unit/`, run then deleted), with all four schema sources,
each row carrying `label_trust_tier='human_authored'`, a non-empty owner answer and 1.0/1.0 scores:

```
sources        : ['generated', 'mined', 'production', 'red_team']
candidates     : []
refusals       : {'trust_tier:model_generated': 1, 'trust_tier:customer_negative': 3}
PROMOTION_DISABLED_REFUSAL count: 0
is_human_labelled per row       : [True, True, True, True]
source tiers   : {'generated': 'model_generated', 'mined': 'customer_negative',
                  'production': 'customer_negative', 'red_team': 'customer_negative'}
```

Three compounding facts:

1. The count is **structurally 0** for every row the shipped schema can produce. The number the
   ordering was chosen to preserve does not exist.
2. Nothing calls `select_promotion_candidates` or `promote_to_verified_qa` in production, so the
   `refusals` dict is never computed at all, and `run_eval_suite`'s return dict does not carry it.
   The number is not merely zero — it is unreadable from anywhere.
3. The only test that shows it non-zero, `test_the_decision_refusal_is_counted_not_swallowed`,
   first does `monkeypatch.setitem(SCENARIO_SOURCE_TRUST_TIER, "mined", "human_authored")`. It is
   **demonstrated only inside the complement of its own blind spot** — the exact pattern the review
   brief names.

This is the same defect class P3 was written to fix: a statement a later reader will believe,
stamped into the code, that the world does not support. Under the branch's own standard it is worse
than the stale reason P3 corrected, because it is *new*.

**Fix (pick one, do not leave the prose):** (a) state what is true — the count measures "what
flipping the decision would promote *given the resolver gate is also lifted*", which is 0 today; or
(b) if the number is genuinely wanted, put the decision gate ahead of gate 1 and accept that the
refusal then means "policy", surfacing `refusals` on the run; or (c) drop the measurement claim and
keep the ordering on its remaining merit (a refused row keeps its most specific reason).

---

## F2 — MEDIUM. The new label-write test already existed, and the deviation it forced was avoidable

`test_label_provenance.py::TestTheWriteChangesNothingElse::
test_the_label_write_assigns_exactly_four_columns_and_never_dataset` duplicates
`test_eval_label_queue.py::TestTheLabelWrite::test_a_label_is_recorded_at_the_human_authored_tier`
(lines 1083-1099), shipped by P2, which already:

- parses the SET clause — with `re.findall(r"(\w+)\s*=", ...)`, a **more** robust parse than the
  comma split;
- asserts the identical four-column set by **equality**, with the same "not `dataset`" argument;
- does it through the **real route and the real writer** against a recording cursor, not by reading
  a module constant.

M-D above turned both red plus a third. The implementer ran M3 with a single-test selector and so
never saw that the coverage was pre-existing.

The consequence is not cosmetic. The stated deviation — "two tests could not go in the new file
because R2 forbids every test module but `test_label_provenance.py` from naming the writer" — is
true only because P3 chose to read `label_service._LABEL_SQL` directly. P2 had already shown the
same fact can be asserted without naming the writer, by observing the SQL the route emits. That
choice is what forced the two `--deselect` flags onto the ignored-new-files control and
complicated the one control that exists to detect a pre-existing test changing status.

---

## F3 — MEDIUM. "A labelled row enters the eval" holds only for a pool smaller than the sample

`_EXPLORATORY_SQL` is `ORDER BY RANDOM() LIMIT %(limit)s` with `limit = EXPLORATORY_SAMPLE_SIZE`
(30). In a tenant with more than 30 eligible exploratory rows, labelling a row adds **nothing** to
`attempted`: it changes *which* rows are drawn, and the newly labelled row has a 30/N chance of
being scored on any given night.

Every new test runs with a three-row exploratory pool, so `attempted 4→5`, `valid 4→5`,
`scored 4→5` are artefacts of a pool below the sample size. The claim as written — in the
`run_eval_suite` docstring ("it acquires a reference_answer, so it is fetched, counted in `valid`,
put to the agent and scored"), in the reference doc's assertion 1, and in the commit message — is
unconditional and the tests cannot support it unconditionally.

This is the practical answer to "what does a label do downstream", and it is the one the owner will
care about when the yield question (BACKLOG 4.10) is finally asked. Say it, and consider whether a
freshly-labelled row should be preferentially drawn once.

---

## F4 — MEDIUM. Both "independent locks" are mutable module state with no absence pin

`SCENARIO_SOURCE_TRUST_TIER` and `VERIFIED_QA_PROMOTION_DECISION` are plain module-level `dict`s,
read at call time. Any module in the process can lift either with one assignment:

```python
eval_service.VERIFIED_QA_PROMOTION_DECISION["enabled"] = True
eval_service.SCENARIO_SOURCE_TRUST_TIER["mined"] = "human_authored"
```

Two tests in the repo already do exactly this via `monkeypatch.setitem` (and P3 added two more
call sites), so the shape is idiomatic and discoverable. There is **no** absence pin — nothing of
the R2/R3 kind — forbidding a module under `app/worker/` or `app/services/` from assigning into
either constant, even though the label writer, a strictly less dangerous surface, carries four
independently-pinned restrictions plus a credential guard.

The mitigation is real (nothing calls the promotion path), which is why this is medium rather than
high. But the phase's own claim is "two independent locks, because either alone is one edit away
from being wrong" — and both are one edit away from being wrong in the *same* way, by the same
mechanism, unwatched. A `MappingProxyType` or a module-level `Final[bool]` read directly would cost
one line each.

---

## F5 — MEDIUM. The downstream analysis omits the deploy gate

P3 is "what a label does downstream". The most consequential downstream consumer of an eval score
is not `verified_qa` — which is dead code with no caller — but the **deploy gate**:

`eval_results` → `deployment_service._fetch_eval_summary_sync` → `pass_rates` →
`apply_signal_evidence_gate`, which blocks a deploy on "any eval metric pass_rate < 0.70".

A labelled row's Ragas scores land in `eval_results` like any other. So:

- Labelling **mined production failures** — the queue's whole population — systematically adds hard
  negatives to the scored set, in a direction that can block a deploy. That may be exactly right,
  but it is a behavioural consequence of labelling that nothing states and nothing tests.
- An owner-authored answer is not grounded in the retrieved corpus by construction, so
  `context_recall` and `faithfulness` over labelled rows measure something different from the same
  metrics over Haiku-written references — which is BACKLOG 4.12's argument, arriving at the deploy
  gate rather than only at the report.
- Conversely an owner who pastes the agent's own answer inflates the gate.

Neither the reference doc's "What P3 asserts downstream" list nor any test reaches past
`verified_qa`.

---

## F6 — LOW. Stale prose of exactly the kind P3 exists to remove, in a file P3 edited

`apps/api/app/worker/tasks/runtime/eval.py:35-38`, module docstring:

> "verified_qa promotion is not performed by this task at all. It is disabled behind eval_service's
> label trust hierarchy, and the decision — with its reason — is recorded on the run…"

That is the pre-D6 single-lock justification. P3's whole thesis is that stating it alone is now
wrong. The `run_eval_suite` docstring 610 lines below **was** updated (`:648-660`); this was not.

Same at `eval.py:1208-1210`: `# Always 0 — promotion is disabled behind the trust gate`.

---

## F7 — LOW. A P1 absence pin is now satisfied by the wrong lock (observed, not argued)

`test_label_provenance.py::TestP1OpenedNoCustomerFacingDoor::
test_a_human_labelled_scenario_is_still_not_promoted_to_verified_qa` asserts only
`sum(refusals.values()) == 1`, never which refusal. Under **M-B** — the resolver gate deleted
outright — it stayed **green**.

Its docstring claims it pins "that adding the human tier did not, by itself, open that write". After
P3 it is satisfied by lock two alone. The new file states the principle exactly
("Testing them together would mean one test passing on the strength of either, which is how a wall
loses a brick without anybody noticing") and did not apply it to the pre-existing test one module
over. One line — `assert refusals == {"trust_tier:customer_negative": 1}` — fixes it.

---

## F8 — LOW. Citation drift in the persisted findings

`.dev/reference/d6-p3-label-downstream.md:16` cites `eval_service.py:1591` as where the decision is
copied into `eval_runs.config`. The line is **1656**; 1591 is inside the `retrieval_config_hash`
block. A `path:line` a reader follows to the wrong place is the same failure mode as F1, smaller.

---

## F9 — LOW. A new test pins a behaviour that contradicts a project constant

`test_the_unscored_labelled_row_does_not_move_a_metric` asserts `metric["measured"] is True` over
**2** observations. `summarise_run_validity` sets `measured: bool(values)` — measured at n=1 — while
`eval_service.MIN_SCORED_OBSERVATIONS = 3` exists precisely because "a rate alone cannot refuse a
one-observation run". The floor is applied to the invocation, never per metric per dataset.

The behaviour is pre-existing; what is new is that a test now locks it in, so a later change
applying the floor per metric would read as a regression rather than a fix.

---

## F10 — LOW. Half of "the disablement is recorded with its reason" is unpinned

Nothing asserts that `scope`, `decided_on`, `producible_label_tier` or `refusal_reason` actually
reach `eval_runs.config["verified_qa_promotion"]`.

- `test_promotion_decision_is_copied_not_shared` exercises the real `build_eval_run_config` but
  touches only `enabled`.
- `test_the_recorded_decision_stays_flat_because_the_copy_is_shallow` asserts the **constant's** key
  set, not the config's.
- The new harness monkeypatches `build_eval_run_config` wholesale, so `test_label_downstream.py`
  proves nothing about what a run records.

A future edit narrowing the recorded dict to `{"enabled": ...}` passes everything.

---

## F11 — NIT. The new tests alias a real schema source

`monkeypatch.setitem(eval_service.SCENARIO_SOURCE_TRUST_TIER, "mined", "human_authored")` (twice).
Every pre-existing test that needed a promotable tier used a hypothetical name (`"owner_written"`)
so as not to alias a live source, while `test_no_schema_allowed_source_can_produce_a_human_label_tier`
asserts the opposite for `'mined'`. `monkeypatch` restores, so this is safe today; it drops a
separation the earlier tests kept deliberately.

## F12 — NIT. BACKLOG ordering

4.12 and 4.13 were inserted before 4.8, so the table now runs 4.9, 4.10, 4.11, 4.12, 4.13, 4.8.

## F13 — NIT/observation. The stated hazard for lock one does not exist in the tree

"Swapping it to `label_trust_tier(scenario)` … would open promotion for every owner label in one
line" is not supported: `label_trust_tier()` and `is_human_labelled()` have **zero** production
callers, and none of `run_eval_suite`'s three selectors projects `label_trust_tier`, so every
production scenario dict resolves through the source fallback. The swap would change nothing outside
the unit test's hand-built dict. The system is therefore safer than described — but the hazard model
in the code comments is wrong, and closing BACKLOG 4.12 (projecting the column) is precisely what
would make it right, which is worth writing into 4.12 itself.

## F14 — LOW, out of P3 scope but bears on the trap. `human_authored` names an account, not a human

`app/api/v1/evals.py:1186` — `_label_principal` returns `f"tenant:{tenant.id}"`. So `labelled_by`
records the **tenant**, not the Clerk user. The Clerk-JWT credential guard proves *a* human was in
the loop; the row cannot say **which**, and a tenant with several console users cannot attribute a
label. P2 scope, disclosed in P2's own docstring, but P3's reference doc repeats "the owner CAN
produce a human_authored label" without it.

---

## What P3 got right, stated so the ledger is balanced

- **The finding itself is real and important.** The recorded `reason` had gone false the moment P2
  landed, and every run since was stamping it into `eval_runs.config`. Catching a stale statement in
  the artefact a future reader trusts most is the highest-value thing this phase produced.
- The 12 mutation proofs are honest: I re-ran M-A/M-B/M-C/M-D-shaped mutations independently and the
  guards behaved as reported. Restores were unconditional and the tree was clean afterwards.
- The docstring correction in `fb065a2` — narrowing a claim *after* its own mutation proof showed
  the second assertion was weaker than the prose — is the right instinct, applied voluntarily.
- The gate, the control and the baseline correction all reproduce.
- R2 firing twice on the implementer's own work, and being worked around rather than weakened, is
  real evidence the import wall still functions.

## Not observed, and therefore not claimed

- **No PostgreSQL on this machine.** `alembic_tenant` 0016 is unapplied and cannot be applied here.
  I ran no migration and none could run. Every `-m integration` harness skips — the 12 skips in both
  of my gate runs are that, and a skip is unobserved, never a pass.
- No real `eval_scenarios` row has ever carried a `label_trust_tier`. All SQL findings above are at
  the string level, and `run_eval_suite` was driven in-process with every boundary doubled.
- I did not run the frontend or widget gates. This phase touches no frontend code.
- I did not re-measure the 2077/12 baseline by stashing; I corroborated it from `1c2b471` being
  docs-only plus HANDOFF's record at `17a5774`, and by the control reproducing it.
- I did not merge, rebase, or touch `settings.json` or any hook.
