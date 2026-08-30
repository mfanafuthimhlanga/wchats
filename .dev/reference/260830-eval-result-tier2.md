# Tier-2 judgement on ticket 14 (PR #118), 2026-08-30

The judge read one bounded artifact (criteria, commits, diff stat, the six-slice trace, the tier-1
report, the fixer's claims) and answered one question: do the claims match the evidence, and what
is asserted but unproven. Verdict **MERGE AFTER** three observations, each settled below.

## The three conditions and how each was settled

1. **The whole suite at the head, by someone other than the fixer.** CI's unit job on `480a6a4`
   passed on `ubuntu-latest`. The fixer's local figure was 3636 passed, 13 skipped.
2. **The two load-bearing mutations re-run.** A separate verifier re-ran them on the head, over
   the 20 touched test files in one process: a dataset's verdict counts need not sum to `scored`,
   4 failed, 993 passed; the results route recomputes `score >= threshold`, 2 failed, 995 passed;
   `from_payload` skips the totals check, 3 failed, 994 passed; each restored to 997 passed. The
   reviewer's zero-rows case, built by hand: `failing_scenarios 0 / unmeasured_scenarios 30`,
   gate `block ['eval_quality_unmeasured']`; thirty passed, gate `ship` past the eval gate.
3. **Stored records the new refusal would reject.** `from_payload` now refuses an
   `eval_runs.result` without verdict counts, which every record the slice-1 writer wrote would
   lack. The only database this branch ever wrote to is the local `wchats_tenant_probe`, and it
   holds zero `eval_runs` rows (queried 2026-08-30 at revision 0023). The branch was never
   deployed, so no tenant holds one. `RULE_VERSION` stays 1.

## What the judge found that tier 1 and the implementers did not

- Slice 4's trace said an all-NULL-verdict run "still ships if the metrics measured"; tier 1
  observed the gate shipping on zero rows against `scored=30`. The judge believed tier 1: slice 4's
  mutation proved the recordless case under a mutated gate and never exercised a record with no
  result rows, and the test that covered it enshrined `unmeasured == 0`.
- A rule change without a version move. Making the verdict-count partition fatal changes what a
  reader accepts; the judge asked for the affected population before merge rather than after.
- The trace opened its review-pass section with "every claim below is an observed output" while
  the fixer's own report said "not independently verified". Same author, two labels.

## Still unproven after the merge, worth a ticket each when the time comes

- Every SQL read this branch adds (`write_eval_result`, `read_eval_result`, `read_run_ledger`,
  `_fetch_retrieved_contexts`, the retrieval `ORDER BY created_at, id`) has run only against
  doubles. The 7.36 gap: integration harnesses are gated and never run.
- The ordering #84 rests on: the retrieval chain is dispatched after `_persist_messages` returns
  and after `message_id` is on the `agent.response` event (`agent.py:1295`). Read by two
  reviewers, pinned by no test.
- `served_model` with one `agent_turn` row and one judge row; F10 (`is True` against a non-bool
  `measured`); the reach-back case in alert and digest and the identity-from-records case in
  `run_judge_identity` (fixed, tested once by the fixer, not re-run).

## The method, for the next milestone

Tier 1 against the code with its own mutations; a fixer; a separate verifier on the fixer's head;
the judge on the artifact only. The judge's value was in the gap between the two reports: it
caught the version question and the trace's mislabelled claims, neither of which a test can see.
