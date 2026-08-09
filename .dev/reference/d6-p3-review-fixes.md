# D6 P3 — the adversarial review's findings, and what closing them changed

**Branch:** `feat/d6-labelling-loop` (off `feat/d1-agent-invocation` `4179a5c`, **not `main`**).
**Commit:** `f78524e`, on top of `fb065a2`.
**Date:** 2026-08-09.
**Findings:** `.dev/reference/d6-p3-adversarial-review.md` — **14** findings (1 high, 4 medium,
6 low, 3 nit) and 7 claims marked unsupported. *(`f78524e`'s commit message says 13. It miscounted;
the list below is the whole of it, and finding 14 — `BACKLOG` section 4's row order — is the one
that message dropped.)*
**Trace:** `.dev/traces/260809-d6-p3-review-fixes.md`. **Corrected phase doc:**
`.dev/reference/d6-p3-label-downstream.md`.

---

## The shape of the branch's defect, stated once

P3's job was to remove a **stale** justification — the `verified_qa` reason string that claimed
promotion was held shut by the absence of a correction UI, which D6 P1/P2 had just built. It did
that. Then it wrote a **new** justification, stamped it in four places plus the reference doc and
the trace, and the new one was never true.

That is a worse failure than the one it fixed. A stale statement was true once and can be dated. A
freshly written false one carries the authority of the commit that introduced it, and every reader
after it will believe it because it is new.

The other thirteen findings are variations on the same theme: a claim that reads as verified —
"asserted by set equality", "a labelled row enters the eval", "two independent locks", "downstream,
asserted rather than assumed" — where the assertion is narrower than the sentence.

---

## Finding by finding

### 1 (HIGH) — the decision gate's ordering was justified by a number that is structurally zero

**The claim.** `refusals[PROMOTION_DISABLED_REFUSAL]` is "how many rows would have been written into
`verified_qa` if the owner flipped the decision" — the number the owner needs to judge the flip, and
the reason the decision gate is consulted last rather than as an early `return []`.

**Why it is false**, three independent reasons, any one sufficient:

1. Gate 1 (`is_promotable_to_verified_qa(source)`) runs first and refuses every source the shipped
   schema allows. No row reaches gate 3, so the count is always 0. What the number actually answers
   is "what would flipping the decision promote **given the resolver gate is lifted too**".
2. Nothing under `app/` calls `select_promotion_candidates` or `promote_to_verified_qa`, so the
   number is never computed.
3. `run_eval_suite` does not return `refusals`, so even computed there is nowhere to read it.

The review's direct probe, over all four schema sources with `label_trust_tier='human_authored'`, a
non-empty answer and 1.0/1.0 scores:

```
refusals = {'trust_tier:model_generated': 1, 'trust_tier:customer_negative': 3}
PROMOTION_DISABLED_REFUSAL count: 0
```

The only test that ever showed the count non-zero first did
`monkeypatch.setitem(SCENARIO_SOURCE_TRUST_TIER, 'mined', 'human_authored')` — **demonstrated
exclusively inside the complement of its own blind spot.**

**Fix — option (a) plus part of (c).** The ordering is kept; the justification is replaced with the
true one. A refused row keeps its **most specific** reason: a row held by its origin reports
`trust_tier:customer_negative`, which names what would have to change, rather than the decision's
reason, which names a gate it never reached. `promoted + refused == scored` holds under either
ordering, so a promotion rate still cannot be constructed without its denominator.

Rewritten in all four code sites (`PROMOTION_DISABLED_REFUSAL`'s comment,
`select_promotion_candidates`' docstring gate 3, the inline comment at the gate itself, and
`test_the_decision_refusal_is_counted_not_swallowed`'s docstring), plus the reference doc and the
trace — both of which keep the false paragraph **struck in place** rather than deleted, because the
wrong version is the part worth being able to recognise later. The commit message cannot be
rewritten; the new commit message says so.

**And the probe is now a test.** `test_the_decision_gate_is_never_reached_by_a_schema_allowed_source`
runs the review's probe against every source in `SCENARIO_SOURCE_TRUST_TIER` and asserts the
decision-gate count is 0 and every refusal is a `trust_tier:` one. The claim cannot come back
without turning it red.

### 2 (MEDIUM) — the four-column label-write test duplicated a P2 test

`test_eval_label_queue.py::TestTheLabelWrite::test_a_label_is_recorded_at_the_human_authored_tier`
(shipped by P2) already parses the SET clause with `re.findall(r"(\w+)\s*=", ...)` and asserts the
identical four-column set **by equality**, driven through the real route and the real writer. The
review's mutation (`dataset = COALESCE(dataset, 'golden')`) turned it red.

P3's copy read `label_service._LABEL_SQL` directly, which is why R2 forced it out of the new file
and onto the ignored-new-files control as a second `--deselect`. Self-inflicted: P2 had already
shown the fact can be asserted without naming the writer.

**Fix.** Deleted. `TestTheWriteChangesNothingElse` keeps only
`test_the_tier_the_writer_stamps_is_the_tier_the_run_record_names`, which genuinely must see both
sides of the import boundary. The control carries one `--deselect` instead of two, and the class
docstring plus `TestGoldenMembershipIsNeverInherited`'s cross-reference both now point at the P2
test.

### 3 (MEDIUM) — "a labelled row enters the eval" holds only below the sample size

`_EXPLORATORY_SQL` is `ORDER BY RANDOM() LIMIT 30`. Above 30 eligible rows a label does not raise
`attempted` at all — it changes *which* rows are drawn. Every P3 test ran with a **three-row** pool,
so the `attempted 4->5 / valid 4->5 / scored 4->5` arithmetic carrying the claim was an artefact of
the sub-cap regime.

**Fix.** The `run_eval_suite` docstring and the reference doc now say **eligible, not present**, and
name the consequence: at 200 eligible rows the owner's answer has a 30/200 chance per night with
nothing in the run report to say it was not exercised.

The harness's cursor double now honours `LIMIT %(limit)s`, which it previously ignored — that is
what made the blind spot invisible. `ORDER BY RANDOM()` is deliberately **not** emulated, and the
tests assert nothing that a shuffle would decide. Three new tests
(`TestLabellingMakesARowEligibleNotPresent`): the SQL carries both clauses; at pool == 30 a label
moves `attempted` by nothing; at pool == 200 the run is the same size and the labelled row is absent
from it.

`BACKLOG 4.14` opened for the preferential-draw decision, framed as a decision rather than a bug
fix — biasing the exploratory sample toward hand-answered rows is exactly the overfitting the
rotation exists to prevent (`6.1`).

### 4 (MEDIUM) — both locks were mutable module dicts, and the load-bearing lock was never named

Two separate defects in one finding.

**(a) The locks were one assignment from open.** `SCENARIO_SOURCE_TRUST_TIER` and
`VERIFIED_QA_PROMOTION_DECISION` were plain module-level dicts read at call time. Any module in the
process could lift both with two lines, no absence pin was watching, and four call sites already
performed exactly that mutation via `monkeypatch.setitem` — so the shape was idiomatic and
discoverable. Meanwhile the label **writer**, a strictly less dangerous surface, carries four
independently-pinned restrictions plus a route-level credential guard.

Both are `MappingProxyType` now; the subscript assignment raises `TypeError`. A proxy cannot stop
`eval_service.X = {...}`, so `TestTheLocksAreNotOneAssignmentAway` AST-scans every module under
`app/` for the subscript, attribute-rebind and `.update()/.setdefault()/.pop()/.clear()` shapes.
`dict()` over a proxy still yields a fresh plain dict, so `build_eval_run_config`'s shallow-copy
semantics are unchanged — pinned. All eight `setitem` call sites became `setattr` of a replacement
mapping, through named helpers in the two test modules.

**(b) Lock zero.** The narrative said "two independent locks" and omitted the one carrying the load:
**`promote_to_verified_qa` has no caller anywhere under `app/`.** The review confirmed the pin is
live. Named now, strongest first, in the `LABEL_TRUST_TIERS` block comment, `promote_to_verified_qa`'s
docstring, `eval.py`'s module docstring, the recorded `reason` string, the reference doc and
`test_label_downstream.py`'s module docstring.

**Stated precisely rather than overstated:** the two pins that cover lock zero
(`test_eval_task.py::TestPromotionIsUnreachableFromTheTask::test_module_does_not_import_or_call_promote_to_verified_qa`
and `test_eval_service.py::test_run_eval_for_agent_does_not_promote`) are **module-scoped**. A third
module introducing the call trips neither. That gap is written down, not closed — closing it is a
tree-wide caller scan and was out of this bounded pass.

### 5 (MEDIUM) — the downstream analysis stopped at dead code and never reached the deploy gate

P3 asserted three downstream claims, all terminating at `verified_qa`, which has no caller. The live
consumer of every score a labelled row produces went unmentioned.

**The chain, now in the `run_eval_suite` docstring and the reference doc:**

```
run_eval_suite -> write_eval_results -> eval_results on PRODUCTION
_fetch_eval_summary_sync: AVG(score) GROUP BY metric, keyed on eval_run_id and nothing else
    -> pass_rates
run_deployment_checklist puts eval_summary on the orchestrator payload
the orchestrator applies "all eval metrics >= 0.85" (ship) / "[0.70, 0.85)" (warn)
```

**One correction to the finding.** It says `apply_signal_evidence_gate` "blocks a deploy on"
`pass_rates`. It does not — verified by reading the function body: it reads `eval_signal`,
`agent_invoked`, and red-team severity/coverage, and never touches the rates. The 0.85 bar is prose
in `_DEPLOYMENT_SYSTEM_PROMPT`, applied by the orchestrator model. The consequence is the same and
slightly worse: the deterministic half is one-way and can only make a recommendation *more*
conservative, so it can never rescue a rate that labelling depressed.

Four tests (`TestALabelChangesWhatTheDeployGateReads`): the labelled row's score reaching
`eval_results` on PRODUCTION through the real task; the aggregation having no provenance filter; the
arithmetic (0.2 on the labelled row drags faithfulness from 0.9 to 0.72, under the bar); and where
the bar actually lives. `BACKLOG 4.12` gains the deploy-gate half of the argument.

**What the third test does not claim:** the aggregate is computed in Python over the rows the real
run handed to `write_eval_results`. There is no PostgreSQL here, so `AVG(score) GROUP BY metric` was
not executed and is not claimed to have been. The second test is what pins that the collector's
aggregation has no other input.

### 6 (LOW) — stale pre-D6 prose in a file P3 edited

`eval.py`'s module docstring still read "verified_qa promotion … is disabled behind eval_service's
label trust hierarchy" — the single-lock justification P3's own thesis condemns — 610 lines above the
`run_eval_suite` docstring P3 did update. Same at the `promoted: 0` comment.

Both rewritten to name all three locks. **`eval_service.py`'s own module docstring carried the
identical defect** ("re-enabling it is a decision that needs human-verified labels behind it") and is
fixed in the same pass — not cited in the finding, declared here as a deviation.

### 7 (LOW) — a docstring that read stronger than its assertion

`test_a_human_labelled_scenario_is_still_not_promoted_to_verified_qa` asserted
`sum(refusals.values()) == 1`, which after P3 added a third gate was satisfied by **either** lock.
The review deleted the resolver gate outright and the test stayed green with the exact lock its
docstring claims to pin entirely removed.

Now `assert refusals == {"trust_tier:customer_negative": 1}`. Mutation I below is that same deletion,
observed red.

### 8 (LOW) — a line citation that pointed at unrelated code

`.dev/reference/d6-p3-label-downstream.md` cited `eval_service.py:1591` for the
`VERIFIED_QA_PROMOTION_DECISION` copy. The copy is at `:1656`; 1591 is inside the
`retrieval_config_hash` block. The document's most load-bearing claim was unverifiable at its own
reference. Now cited by symbol (`build_eval_run_config`'s `"verified_qa_promotion"` key), with a note
saying why — and both line numbers are already wrong again after this commit, which is the point.

### 9 (LOW) — an assertion pinned against the project's own floor

`test_the_unscored_labelled_row_does_not_move_a_metric` asserted `metric["measured"] is True` over 2
observations. `summarise_run_validity` sets `measured: bool(values)` — true at n=1 — while
`MIN_SCORED_OBSERVATIONS = 3` exists because "a rate alone cannot refuse a one-observation run". The
floor is applied to the invocation, never per metric per dataset. Someone closing that gap correctly
would turn this test red, and it would read as a regression rather than as the fix.

The observation count is now the assertion; `measured` is documented as current behaviour
(`metric["measured"] == (metric["observations"] > 0)`) with the tension named in the docstring.

### 10 (LOW) — half of "recorded with its reason" was unpinned

Nothing asserted that `scope`, `decided_on`, `producible_label_tier` or `refusal_reason` reach
`eval_runs.config["verified_qa_promotion"]`. `test_promotion_decision_is_copied_not_shared` exercises
the real `build_eval_run_config` but touches only `enabled`; P3's flatness test asserts the
**constant's** key set; and `test_label_downstream.py` monkeypatches `build_eval_run_config`
wholesale. A future edit narrowing the recorded dict to `{"enabled": ...}` would leave every test
green.

`test_the_whole_decision_reaches_the_run_record_not_just_the_flag` asserts set equality between the
recorded dict and the constant, every value carried verbatim, and the four meaning-bearing keys by
name. Mutation H is exactly that narrowing — and it left the pre-existing sibling **green**, which is
the finding demonstrated rather than argued.

### 11 (LOW) — `labelled_by` names an account, not a person

`_label_principal` returns `f"tenant:{tenant.id}"`. `human_authored` asserts a human was in the loop
(the Clerk-JWT guard proves that) but cannot say which one. A tenant with three console users has one
string for all three, so a wrong reference answer found later cannot be attributed.

Out of P3's scope to fix, and the finding says so. `_label_principal`'s own docstring already states
it and `BACKLOG 4.7` carries the residue; what was missing is that the reference doc repeated "the
owner CAN produce a `human_authored` label" without the qualification. Added, under "What
`human_authored` is worth — and what it is not".

### 12 (NIT) — the lock-two tests aliased a real schema source

`monkeypatch.setitem(SCENARIO_SOURCE_TRUST_TIER, "mined", "human_authored")` installed, for the
duration of two tests, a state that `test_no_schema_allowed_source_can_produce_a_human_label_tier`
asserts is impossible. Every pre-existing test used the hypothetical `"owner_written"` for exactly
this reason. Both now use `"owner_written"`, through the `_with_source_tier` helper.

### 13 (NIT) — the resolver lock's stated hazard does not exist yet

`select_promotion_candidates`' comment warned that swapping the gate to `label_trust_tier(scenario)`
"would open promotion for every owner label in one line". Unsupported: `label_trust_tier()` and
`is_human_labelled()` have zero production callers, and none of `run_eval_suite`'s three selectors
projects `label_trust_tier`, so every production scenario dict falls through to the source-based
tier. The swap would change nothing outside a unit test's hand-built dict.

The comment now says the hazard is **latent**, and names `BACKLOG 4.12` — projecting the column into
the selectors — as the change that arms it. `4.12` now carries the reciprocal note: it and the gate
must land together with the gate explicitly re-argued.

### 14 (NIT) — `BACKLOG` section 4 ran 4.9, 4.10, 4.11, 4.12, 4.13, 4.8

`4.8` moved above `4.9`.

---

## Gate runs — observed, verbatim

Command, from `apps/api`:

```
.venv/Scripts/python.exe -m pytest tests/unit -q \
  --ignore=tests/unit/test_chunking_service.py \
  --ignore=tests/unit/test_docling_service.py
```

| run | verbatim final line |
|---|---|
| **Baseline at `fb065a2`** (the review re-ran both controls itself and both reproduced) | `2101 passed, 12 skipped, 30 warnings in 415.69s` |
| **After these fixes** (`f78524e`) | `2112 passed, 12 skipped, 30 warnings in 360.40s (0:06:00)` |
| **Ignored-new-files control** (`f78524e`) | `2077 passed, 12 skipped, 2 deselected, 28 warnings in 363.04s (0:06:03)` |

The control adds, to the two standing `--ignore` flags:

```
--ignore=tests/unit/test_label_downstream.py
--deselect "tests/unit/test_label_provenance.py::TestTheWriteChangesNothingElse::test_the_tier_the_writer_stamps_is_the_tier_the_run_record_names"
--deselect "tests/unit/test_eval_service.py::TestBuildEvalRunConfig::test_the_whole_decision_reaches_the_run_record_not_just_the_flag"
```

**Delta arithmetic: 2112 − 2077 = 35 = 33 (`test_label_downstream.py`, up from 22) + 1
(`test_label_provenance.py`, DOWN from 2 — the duplicate is deleted) + 1 (`test_eval_service.py`).**

**Why the control matters more than usual this time.** Test-count arithmetic cannot see a
pre-existing test changing status, and this pass changed the CONTENT of six pre-existing tests:

- four `monkeypatch.setitem` call sites in `test_eval_service.py` became `setattr` of a replacement
  mapping (forced by `MappingProxyType`);
- `test_a_human_labelled_scenario_is_still_not_promoted_to_verified_qa` traded a count for a reason;
- `test_the_unscored_labelled_row_does_not_move_a_metric` stopped pinning `measured`.

Two more pre-existing tests in `test_label_downstream.py` changed (`owner_written` instead of
`mined`), and one was deleted from `test_label_provenance.py`. All six of the first group are inside
the control's population, so their staying green is **observed**, not assumed. The control reads
`2077 / 12 / 2` — byte-identical to the number the previous phase's control produced and the number
`.dev/HANDOFF.md` independently records at `17a5774`.

---

## Mutation proofs — 11 guards, each observed red then green

Protocol per guard: mutate → run the exact selector → observe red → `git checkout HEAD -- <path>`
(unconditional; `HEAD` is `f78524e`, which contains the work) → run again → observe green.
`git status --short` was **empty** after the last restore.

| # | guard | mutation | selector | red | green |
|---|---|---|---|---|---|
| A | the decision gate is never reached (finding 1) | moved the `if not …["enabled"]` block ABOVE the trust-tier gate in `select_promotion_candidates` | `test_label_downstream.py::TestNoLabelReachesACustomer::test_the_decision_gate_is_never_reached_by_a_schema_allowed_source` | `1 failed in 18.34s` — `assert 4 == 0`, `{'promotion_disabled:eval_only': 4}` | `1 passed in 13.62s` |
| B | the exploratory draw is capped, in the SQL (finding 3) | deleted `LIMIT %(limit)s` from `_EXPLORATORY_SQL` | `test_label_downstream.py::TestLabellingMakesARowEligibleNotPresent` | `1 failed, 2 passed in 19.30s` — `assert 'LIMIT %(limit)s' in "… ORDER BY RANDOM()"` | `3 passed in 17.02s` |
| B2 | the task draws no more than the cap (finding 3) | `"limit": EXPLORATORY_SAMPLE_SIZE` → `EXPLORATORY_SAMPLE_SIZE * 10` at the call site | `test_label_downstream.py::TestLabellingMakesARowEligibleNotPresent` | `2 failed, 1 passed in 19.05s` — `assert 203 == (2 + 30)` | `3 passed in 16.86s` |
| C | the score reaches PRODUCTION (finding 5) | `write_eval_results(run_id, scores, conn_str)` → `branch_conn_str or conn_str` (audit D2 reinstated) | `…::TestALabelChangesWhatTheDeployGateReads::test_the_labelled_rows_score_is_written_to_eval_results` | `1 failed in 19.16s` — `'postgresql://neon-branch/tenant' != 'postgresql://production/tenant'` | `1 passed in 16.88s` |
| D | the pass-rate aggregation has no provenance filter (finding 5) | added `AND label_trust_tier IS NULL` to `_fetch_eval_summary_sync`'s metric query | `…::test_the_pass_rate_query_cannot_exclude_a_labelled_row` | `1 failed in 15.47s` — `assert 'WHERE eval_run_id = %s GROUP BY metric' in …` | `1 passed in 13.45s` |
| E | the ship bar is prompt prose, not gate code (finding 5) | added `if any(v < 0.85 for v in rates.values()): blocked = True` to `apply_signal_evidence_gate` | `…::test_the_ship_bar_is_prose_in_the_prompt_not_code_in_the_gate` | `1 failed in 15.41s` — `pass_rates` found in the gate body | `1 passed in 13.57s` |
| F | the locks are read-only mappings (finding 4a) | `SCENARIO_SOURCE_TRUST_TIER: … = MappingProxyType({` → `dict({` | `test_label_downstream.py::TestTheLocksAreNotOneAssignmentAway` | `1 failed, 2 passed in 16.10s` — `SCENARIO_SOURCE_TRUST_TIER is a dict` | `3 passed in 16.22s` |
| G | no module under `app/` writes to a lock (finding 4a) | added `_es.SCENARIO_SOURCE_TRUST_TIER = {"mined": "human_authored"}` to `eval.py` — the rebind a proxy cannot stop | `…::TestTheLocksAreNotOneAssignmentAway::test_no_module_under_app_writes_to_either_lock` | `1 failed in 19.86s` — `{'eval.py': ['line 159: rebinds .SCENARIO_SOURCE_TRUST_TIER']}` | `3 passed in 16.22s` |
| H | the whole decision reaches the run record (finding 10) | `"verified_qa_promotion": dict(VERIFIED_QA_PROMOTION_DECISION)` → `{"enabled": …["enabled"]}` | `test_eval_service.py -k "the_whole_decision_reaches_the_run_record or promotion_decision_is_copied"` | `1 failed, 1 passed in 15.69s` — missing `'scope'`, `'min_trust_tier'`, … | `2 passed in 13.45s` |
| I | the refusal names the ORIGIN's tier (finding 7) | deleted the resolver gate from `select_promotion_candidates` | `test_label_provenance.py::TestP1OpenedNoCustomerFacingDoor::test_a_human_labelled_scenario_is_still_not_promoted_to_verified_qa` | `1 failed in 14.37s` — `{'promotion_disabled:eval_only': 1}` vs `{'trust_tier:customer_negative': 1}` | `1 passed in 12.12s` |
| J | the observation count is the SCORED count (finding 9) | `"observations": len(values)` → `bucket["valid"]` | `test_label_downstream.py::TestTheCountsStayHonest::test_the_unscored_labelled_row_does_not_move_a_metric` | `1 failed in 19.38s` — `faithfulness claims 3 observations … assert 3 == 2` | `1 passed in 16.63s` |
| K | a hard negative reaches the rate (finding 5) | filtered `results["scores"]` to `>= 0.85` before `write_eval_results` | `…::test_a_hard_negative_label_lowers_the_rate_the_orchestrator_reads` | `1 failed in 19.00s` — `assert 0.9 < 0.9` | `1 passed in 16.86s` |

**H is the most informative.** The mutation is the plausible payload-shrinking edit, and under it the
**pre-existing** `test_promotion_decision_is_copied_not_shared` stayed **green** while the new test
went red. That is finding 10 demonstrated on live code rather than argued: the tree really did have
no assertion that anything beyond `enabled` reaches the run record.

**I is the second.** It reproduces the review's own mutation B, under which the old
`sum(refusals.values()) == 1` assertion stayed green with the lock it documents entirely removed.

**One mutation was discarded and re-run.** The first attempt at J used `len(rows)`, which is not in
scope in `summarise_run_validity` — the run raised `NameError` and the test failed with a `KeyError`
on `datasets` rather than on the assertion. That is a crash, not a guard firing, so it proves
nothing. Restored and re-mutated with `bucket["valid"]`, which is the semantically plausible wrong
answer, and recorded that.

---

## What is NOT proven — plainly

- **No PostgreSQL server on this machine.** Migration 0016 has not been applied and cannot be here.
  **No migration was run and none is claimed to have been.** Every `-m integration` harness
  **skips** — the 12 skips in every run above are that — and a skip is unobserved, never a pass.
- **`AVG(score) GROUP BY metric` was never executed.** The deploy-gate arithmetic test computes the
  mean in Python over the rows the real run handed to `write_eval_results`. What is pinned
  separately is that the collector's aggregation has no other input and no provenance filter.
- **The orchestrator's 0.85 bar is applied by a model, not by code.** The tests assert the sentence
  is in `_DEPLOYMENT_SYSTEM_PROMPT` and that `apply_signal_evidence_gate` does not read the rates.
  Whether a Sonnet orchestrator actually applies it is unobserved here and always has been.
- **`ORDER BY RANDOM()` is not emulated.** The cursor double truncates. The tests assert counts and
  the absence of a row from one particular draw, never an identity a shuffle would decide.
- **Lock zero's absence pins are module-scoped.** A third module introducing a
  `promote_to_verified_qa` call trips nothing. Stated in the code and here; not closed.
- **All SQL remains asserted at the string level**, and `run_eval_suite` is driven in-process with
  every boundary doubled.
- **No live label has ever been written.** The loop's yield is still unmeasured (`BACKLOG 4.10`).

---

## Opened

`BACKLOG 4.14` — a freshly labelled row is not drawn preferentially, so the labelling loop's
feedback latency is unbounded above the exploratory sample size. Framed as a decision, not a bug:
biasing the rotating half toward hand-answered rows is the overfitting the rotation exists to
prevent.

`BACKLOG 4.12` gained two paragraphs — that projecting `label_trust_tier` is what arms the resolver
lock's stated hazard, and that the deploy gate (not just the report) is where the two populations get
averaged into one number.
