# Trace — D6 P3, what a label does downstream

**Date:** 2026-08-09 · **Branch:** `feat/d6-labelling-loop` · **Commits:** `edb4fbb` + the docs/correction commit that follows it.
**Plan:** `.dev/plans/260808-d6-labelling-loop.md` §P3 · **Full findings:** `.dev/reference/d6-p3-label-downstream.md`

## What actually changed

- `eval_service.py` — `PROMOTION_DISABLED_REFUSAL`; `VERIFIED_QA_PROMOTION_DECISION` rewritten
  (`scope`, `decided_on`, `producible_label_tier`, `refusal_reason`, new `reason`); a third gate in
  `select_promotion_candidates`; the `LABEL_TRUST_TIERS` block comment and `promote_to_verified_qa`
  docstring corrected.
- `eval.py` — the run returns `promotion_enabled`; docstrings state what a labelled row does.
- `tests/unit/test_label_downstream.py` — new, 22 tests.
- `tests/unit/test_label_provenance.py` — +2 (`TestTheWriteChangesNothingElse`).
- `tests/unit/test_eval_service.py` — 2 tests lift the new gate as well as the old one.

## Decisions

1. **The disablement is now a flag, and that reverses a written argument.** `eval_service.py:208-212`
   argued *against* `if False`, on the grounds that promotion was unreachable by construction and
   would open itself the moment a human tier existed. D6 produced that tier. The property inverted;
   the flag is now required, and the comment saying otherwise is corrected rather than left.
2. **The decision gate goes LAST, not first.** Refusing early would report the same zero promotions
   and destroy `refusals["promotion_disabled:eval_only"]` — the count of rows that cleared every
   property gate, which is the only measurement of what flipping the decision would promote.
3. **The old source gate was kept, not replaced.** Two locks, so the plausible one-line "fix"
   (`label_trust_tier` instead of `source`) turns a test red *and* still does not open the door.
4. **`promotion_enabled` beside `promoted: 0`**, because 0 is also what an enabled run that promoted
   nothing reports, and since D6 those are different states.
5. **The counts were already correct** — a labelled row needed no arithmetic change. The work was
   pinning the property (M5, M6, M12), not creating it.

## Deviations

- **Two tests could not go in the new file.** R2 forbids any test module but
  `test_label_provenance.py` from naming the writer, in string constants included. It fired on the
  first draft twice. Both were moved rather than the guard relaxed, which forces the
  ignored-new-files control to carry two `--deselect` flags.
- **The brief's baseline was wrong for this branch.** 1873/11 is the branch point `4179a5c`; HEAD
  (`1c2b471`) is 2077/12, measured by stashing the work rather than computed. `.dev/HANDOFF.md`
  independently records 2077/12 at `17a5774`, which corroborates it.
- **One docstring was corrected after its own mutation proof.** M11 showed the load-bearing half of
  `test_the_owners_answer_is_the_reference_and_never_the_prediction` is real; its docstring also
  claimed the other half pinned audit D1's return, which it cannot — the invocation helper overwrites
  `agent_response` either way. Corrected, and both gates re-run against the corrected tree.

## Gates — observed

| | verbatim |
|---|---|
| baseline `1c2b471` | `2077 passed, 12 skipped, 30 warnings in 362.02s (0:06:02)` |
| after (final) | `2101 passed, 12 skipped, 30 warnings in 370.48s (0:06:10)` |
| ignored-new-files control | `2077 passed, 12 skipped, 2 deselected, 28 warnings in 365.63s (0:06:05)` |

12 mutation proofs, each observed red then restored from `HEAD` and observed green. Table with
verbatim output and exact selectors: `.dev/reference/d6-p3-label-downstream.md`.

## Not proven

No PostgreSQL here. **0016 remains unapplied and was not applied** — no migration can run on this
machine. Every `-m integration` harness skips (the 12 skips above), and a skip is unobserved. No real
`eval_scenarios` row has ever carried a label tier; all SQL is asserted at the string level and
`run_eval_suite` is driven in-process with every boundary doubled.

## Opened

`BACKLOG 4.12` (a run cannot report label provenance — needs a third selector fallback rung),
`4.13` (`promotion_enabled` is returned and unread by the deploy gate).
