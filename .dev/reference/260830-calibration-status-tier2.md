# Tier-2 judgement on ticket 16 (PR #123), 2026-08-30

The judge read one bounded artifact (criteria, commits, diff stat, the three-slice trace with its
review pass, the tier-1 report, the fixer's claims) and answered one question: do the claims
match the evidence, and what is asserted but unproven. Verdict **MERGE AFTER** three
observations, each settled below by someone other than the fixer.

## What the judge found that the code reviews did not

- **On this branch no real calibration run can produce anything but `not_calibrated_yet`**,
  and the artifact says so only in pieces. The harness's judge (`tests/evals/judge.py`) builds an
  Anthropic client at a retired model literal with no reasoning effort and a hand-numbered
  rubric, so `JudgeIdentity` refuses what it reports and every real artifact carries
  `judge_identity: null`. Even with the effort filled, that identity names a Judge the deploy
  path never runs (every purpose routes to `gpt-5.6-luna`), so the loader would answer
  `identity_mismatch` on every real deploy. The calibrated path is proven only with an injected
  judge. This is #58's prerequisite, recorded there on 2026-08-30.
- **The record duplicates the harness's calibrated rule**, and any divergence turns a real
  calibrated run into `harness_raised:InvalidCalibrationStatus`. The floor did exactly that once
  (`scored_pairs >= 1` against a verdict-only sheet) before it moved to `pairs >= 1`. No test
  drives `from_harness` over the harness's own `calibration_verdict` outputs; that test is the
  one that closes the class.
- `JUDGE_RUBRIC_VERSION` is a hand-bumped constant, so a rubric edit without a bump groups two
  Judges under one figure. The widening `judge_identity.py` exists to stop, one rung down.
- The trace's "Open after three slices" section described a table the review pass deleted and a
  row rule it reversed, and its type-file test count was off; a trace is read by the next
  session, so a stale open list is the exact failure the section exists to prevent.

## The three conditions and how each was settled

1. **The suite count and the type file's collected count at the head, by the verifier.**
   436 passed in 132.71s over eight modules; `test_calibration_status_type.py` collects 89
   (the trace had said 110 and its arithmetic implied 88; corrected in the trace).
2. **Tier 1's criterion-2 probe repeated at the head with a 2 MiB file.** Four-key artifact,
   `invalid`; `ceiling_beats_chance: false`, `invalid`; `artifact_version: 99`, `invalid` with
   both versions in the log; null identity with a run identity, `artifact_names_no_judge`;
   `pairs: 0`, `invalid`; a 2 MiB file, `unreadable` in 0.45 ms with `read_text` never called; a
   fully populated calibrated payload with `scored_pairs: 0`, `calibrated`. Nothing raised.
3. **One run of `main` with an injected judge reporting a valid identity over a verdict-only
   sheet.** The verifier drove it in scratch: the written record `calibrated`, pairs 4,
   scored_pairs 0; the loader returned `calibrated` with no reason. No repo test pinned both
   halves; one was added on the branch after the verdict, named in the trace's review pass.

## What CI caught that five agents did not

At `f3fa8f3` CI's unit job failed one test, `test_label_provenance.py`'s name-level pin: no
module under `app/services/` may mention `labelled_at`, the `eval_scenarios` label-provenance
column, and the calibration summary named it for the sheet's date. Implementer, adversary,
fixer, verifier and orchestrator each ran the calibration and deployment suites; none ran a
tree-scanning guard that no commit touched. The field was renamed (`labels_made_at`) rather
than the module allowlisted, because the allowlist is for modules that declare the column.
Logged as FM-019; its climb is the static tier of `scripts/gates.py` running the tree-scanning
guards itself.

## Still unproven after the merge

- A real harness run writing an artifact with an identity: blocked on #58.
- The atomic write surviving a kill between write and rename: read, not run.
- That a rubric edit changes the identity: nothing goes red today.

## The method

Same shape as ticket 14: one implementer per slice, tier 1 with its own mutations, a fixer, a
separate verifier on the fixer's head, the judge on the artifact only. The judge's value this
time was the sentence nobody else wrote: the mechanism is honest and complete, and it cannot say
`calibrated` about the platform's Judge until the harness scores with that Judge.
