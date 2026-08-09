# Trace — D6 P3 adversarial-review fixes

**Date:** 2026-08-09 · **Branch:** `feat/d6-labelling-loop` · **Commit:** `f78524e` on `fb065a2`.
**Findings:** `.dev/reference/d6-p3-adversarial-review.md` (13, one high) ·
**Full report:** `.dev/reference/d6-p3-review-fixes.md` ·
**Corrected phase doc:** `.dev/reference/d6-p3-label-downstream.md`

## What actually changed

- `eval_service.py` — the measurement justification for the gate ordering removed from three sites
  and replaced with the true one (most-specific-reason); `SCENARIO_SOURCE_TRUST_TIER` and
  `VERIFIED_QA_PROMOTION_DECISION` are `MappingProxyType`; lock zero named in the
  `LABEL_TRUST_TIERS` block, `promote_to_verified_qa`'s docstring and the recorded `reason`; gate 1's
  hazard restated as latent; module docstring's stale pre-D6 sentence corrected.
- `eval.py` — module docstring names all three locks (was one); `promoted: 0` comment;
  `run_eval_suite` docstring says **eligible, not present** and carries the deploy-gate chain.
- `test_label_downstream.py` — 22 → 33 tests. Cursor double honours `LIMIT`. New:
  `TestLabellingMakesARowEligibleNotPresent` (3), `TestALabelChangesWhatTheDeployGateReads` (4),
  `TestTheLocksAreNotOneAssignmentAway` (3), the decision-gate probe (1). `setitem` → `setattr`
  helpers; `owner_written` instead of aliasing `mined`.
- `test_label_provenance.py` — the duplicate four-column test deleted; the "still not promoted" test
  asserts the refusal reason instead of a count.
- `test_eval_service.py` — `+1` (the whole decision reaches the run record); four `setitem` sites
  became `setattr`.
- `.dev/BACKLOG.md` — `4.8` reordered; `4.12` gained the arming note and the deploy-gate half;
  `4.14` opened.

## Decisions

1. **The gate ordering is kept; only its justification changed.** Option (a) of the finding, not (b).
   Moving the decision gate first to make its count readable would trade a real benefit (a refused
   row keeps its most specific reason) for a number nothing reads — `run_eval_suite` does not return
   `refusals` and nothing under `app/` calls the function.
2. **The false paragraphs are struck in place, not deleted,** in the reference doc and the P3 trace.
   P3's failure was replacing a stale justification with a new false one; a reader needs to be able
   to see that shape, and a clean rewrite hides it.
3. **`MappingProxyType` over a `Final[bool]` read.** The finding offered both. The proxy keeps the
   two constants as one readable statement of policy and closes the subscript assignment; the rebind
   it cannot close is covered by an AST scan, which a `Final` would have needed anyway.
4. **The deploy-gate claim was corrected downward.** The finding says
   `apply_signal_evidence_gate` blocks on `pass_rates`. It does not — it never reads them. The 0.85
   bar is prose in `_DEPLOYMENT_SYSTEM_PROMPT`. Implemented as the code actually behaves, and pinned
   both ways.
5. **Lock zero's pins are described as module-scoped, not tree-wide.** Writing "pinned" over two
   module-local absence checks would be the same defect this whole pass exists to remove.

## Deviations

- **`eval_service.py`'s module docstring was fixed although finding 6 cites only `eval.py`.** It
  carried the identical stale premise ("re-enabling it is a decision that needs human-verified
  labels behind it") in a file this pass was already editing.
- **Finding 5's last hop was implemented differently from how it is written** — see decision 4.
- **Finding 11 produced documentation only.** `_label_principal` is not changed: the Clerk subject
  is not on the dependency, `BACKLOG 4.7` already carries the residue, and the finding says it is
  not P3's to fix.
- **One mutation was discarded mid-proof.** J's first attempt (`len(rows)`) raised `NameError`, so
  the test failed on a crash rather than on its assertion. Restored, re-mutated with
  `bucket["valid"]`, recorded.

## Gates — observed

| | verbatim |
|---|---|
| baseline `fb065a2` (re-run by the review, both controls reproduced) | `2101 passed, 12 skipped, 30 warnings in 415.69s` |
| after (`f78524e`) | `2112 passed, 12 skipped, 30 warnings in 360.40s (0:06:00)` |
| ignored-new-files control | `2077 passed, 12 skipped, 2 deselected, 28 warnings in 363.04s (0:06:03)` |

`2112 − 2077 = 35 = 33 + 1 + 1`. The control reads the same `2077 / 12 / 2` the previous phase's
control produced, so **no pre-existing test changed status** — which matters more than usual here,
because six pre-existing tests changed content inside the control's population.

11 mutation proofs, each observed red then restored from `HEAD` and observed green; `git status
--short` empty after the last. Verbatim output and exact selectors:
`.dev/reference/d6-p3-review-fixes.md`.

## Not proven

No PostgreSQL here. **0016 remains unapplied and no migration was run.** Every `-m integration`
harness skips (the 12 skips above) and a skip is unobserved. `AVG(score) GROUP BY metric` was never
executed — the deploy-gate arithmetic is computed in Python over the rows the run really wrote, and
the aggregation's lack of a provenance filter is pinned separately at the string level. The
orchestrator's 0.85 bar is applied by a model, not by code. `ORDER BY RANDOM()` is not emulated.
Lock zero's absence pins are module-scoped, so a third module adding the call trips nothing.

## Opened

`BACKLOG 4.14` (a fresh label is not drawn preferentially — unbounded feedback latency above the
sample size, and it is a decision rather than a bug fix). `4.12` extended.
