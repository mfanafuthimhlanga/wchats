# Mutation proofs: the judge's context (`5.16`)

> **Round 2 is at the bottom and supersedes round 1's conclusion.** An adversary pass defeated the
> guards proved here, five ways, all green. Read round 2 before trusting M1's reading.


Four proofs that `tests/unit/test_judge_sees_agent_context.py` fails on the defect it names, plus
two attempts that were invalid and are recorded as such. Each mutation was applied to
`apps/api/app/worker/tasks/runtime/agent.py`, the module run, then the file restored from a byte
copy taken before the first mutation.

**Restoration is by copy, not by `git checkout HEAD --`.** The fix was uncommitted at proof time,
so checking out HEAD would have deleted it rather than restored it. Final hash after the last
restore matched the pre-mutation copy exactly (`9A41DBF4…`), and the module was green again.

## M1: the original line, back at the dispatch site

```python
retrieved_context_json = json.dumps([str(r)[:600] for r in retrieve_results][:3])
```

```
FAILED test_the_dispatch_site_slices_nothing
FAILED test_the_dispatch_site_calls_the_helper
2 failed, 8 passed in 11.21s
```

**Read the 8 passed, not the 2 failed.** Every behavioural test in the module stayed green with the
whole defect restored, because they call `_judge_retrieved_context` directly and it was still
correct. Nothing was calling it. That is `1.32` in a new spelling: a correct thing defined and never
wired is invisible to every test that drives it directly, and only a structural pin sees it.

## M2: a 600-char cut inside the helper

`contexts.extend(chunks)` to `contexts.extend([c[:600] for c in chunks])`.

```
FAILED test_a_chunk_longer_than_the_old_cap_reaches_the_judge_whole
FAILED test_every_retrieve_call_reaches_the_judge
FAILED test_judge_context_is_exactly_what_the_agent_was_shown
FAILED test_the_helper_applies_no_cap_of_its_own
FAILED test_the_judges_context_is_json_the_auditor_can_parse
5 failed, 5 passed in 10.77s
```

## M3: the three-call cap, inside the helper

`for tc in tool_calls_log:` to `for tc in tool_calls_log[:3]:` (line 368).

```
FAILED test_every_retrieve_call_reaches_the_judge
FAILED test_the_helper_applies_no_cap_of_its_own
2 failed, 8 passed in 14.60s
```

## M4: the unparsed fallback dropped

`contexts.append(str(tc["result"]))` to `continue` (line 379), leaving the counter incrementing.

```
FAILED test_an_undecodable_payload_falls_back_and_is_counted
1 failed, 9 passed in 10.60s
```

The counter alone is not enough: a retrieve call that decoded to nothing must still put something in
front of the judge, or a decode failure reaches the Auditor as `[]` and is indistinguishable from a
turn that retrieved nothing. That is `5.11`, the empty-context era, with a different cause.

## Two invalid attempts, recorded rather than redone quietly

Both were string-anchor replacements whose anchor did not match, so the file was never mutated and
the run executed pristine code:

| Attempt | Anchor | What it printed |
|---|---|---|
| M3, first try | `    for tc in tool_calls_log:` asserted unique | `AssertionError`, then `10 passed` |
| M4, first try | `contexts.append(str(tc["result"]))` through PowerShell quoting | `SyntaxWarning: invalid escape sequence '\]'`, then `10 passed` |

**A `10 passed` from a mutation that was never applied is not a proof, and it looks exactly like
one.** Both anchors failed for the same reason: the mutation was expressed as a string that had to
survive PowerShell quoting to reach Python. The retries used line numbers with an assertion on the
line's content, which either mutates or raises, and cannot silently no-op.

This is the same failure the `5.1` proof hit (recorded in retro K): a mutation proof that
compensated for itself and stayed green in both states. The general form is that **the proof needs
its own evidence that the mutation landed**, printed before the test run, not inferred from the
result.

---

# Round 2: the adversary defeated the guards above

An adversarial review of commit `283310e` reintroduced the whole `5.16` defect five ways. **Every one
was 10/10 green** against the two AST guards round 1 proved. Round 1's M1 reading, "only the AST pins
caught it", was therefore too generous to them: they caught **one spelling**.

| Mutation | Adversary's observed result |
|---|---|
| `capped_contexts = [c[:600] for c in judge_contexts[:3]]`, dispatch uses it | `10 passed in 16.25s` |
| `json.dumps(_cap_judge_context(judge_contexts))` | `10 passed in 13.67s` |
| `json.dumps(list(islice(judge_contexts, JUDGE_CTX_CAP)))` | `10 passed in 19.15s` |
| helper still called, result discarded, rebuild from `tc["result"]` | `10 passed in 17.37s` |
| second `retrieved_context_json = ...` on the next line, `[:600]` and `[:3]` verbatim | `10 passed in 12.98s` |
| `json.dumps(judge_contexts[:JUDGE_CTX_CAP])` on the dispatch line | `1 failed, 9 passed` (the only one caught) |

## The same five against the behavioural pin

`_dispatch_validation_chain` now exists as a seam and `TestWhatTheAuditorIsActuallyHanded` asserts on
`run_auditor.si.call_args`. Re-running the adversary's own five, each mutation printing
`=== <name> APPLIED ===` before its run so an unapplied mutation cannot masquerade as a pass:

```
=== M-A different variable name APPLIED ===
FAILED ...::test_the_auditor_receives_every_chunk_untruncated
FAILED ...::test_every_retrieve_call_reaches_the_auditor
FAILED ...::test_a_chunk_longer_than_the_old_cap_arrives_whole
3 failed, 9 passed in 16.23s

=== M-C truncating helper APPLIED ===
3 failed, 9 passed in 16.21s

=== M-D islice + named constant APPLIED ===
2 failed, 10 passed in 17.95s

=== M-E old repr source restored APPLIED ===
3 failed, 9 passed in 19.42s

=== M-I second overwriting assignment APPLIED ===
3 failed, 9 passed in 11.33s

=== RESTORED ===
```

Restored by byte copy taken before the first mutation; `git diff --stat` against the working tree
showed only the intended fix afterwards, and the module returned `12 passed`.

**M-D fails on 2 rather than 3 because `islice(contexts, 3)` keeps the first three elements intact,
so the single-long-chunk test still passes.** Two failures is a fail; the detail is recorded because
a partial catch is the kind of thing that gets rounded up to "caught" on a second reading.

## The conclusion that replaces round 1's

Round 1 concluded that structural pins are what catch wiring defects. **That is half right and the
wrong half is load-bearing.** A structural pin catches the spelling its author imagined. What catches
the defect is asserting on the value the consumer is handed, which requires a seam that a test can
reach. The full argument is in
`.dev/reference/260815-wiring-is-invisible-to-behavioural-tests.md`.
