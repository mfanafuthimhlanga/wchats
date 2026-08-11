# Mutation proofs — the ToolResultBlock dead branch (BACKLOG 5.9, 5.8)

**Date:** 2026-08-11 · **Branch:** `chore/local-postgres` · **Fix commit:** `dc67d37`
**Method:** mutate the guard, observe red, `git checkout HEAD -- <file>` unconditionally, observe
green. Verbatim tail of each run, not a summary of it.

Six proofs. Every one went red on the first attempt.

---

## The evidence that preceded the fix (no spend, no model call)

`5.9` said "settle it statically, then decide on the spend". Settled three independent ways:

1. **The SDK's own transcript readers.** `_internal/sessions.py:277-280` considers only
   `"type":"user"` lines and skips those containing `"tool_result"`.
   `_internal/session_summary.py:81-92` returns early unless `entry["type"] == "user"`, then skips
   content carrying a `tool_result` block. Both treat tool results as a **user-entry** phenomenon.
2. **42,334 real CLI entries.** Scanned `~/.claude/projects/**/*.jsonl` — 782 session files written
   by the same `claude.exe` the SDK spawns:

   ```
   transcript files scanned : 782
   candidate lines scanned  : 42335
   tool_result-carrying entries, by (entry type, message role):
       ('user', 'user') -> 42334

   ASSISTANT-carried tool_result count: 0
   ```
3. **The Messages API shape.** `tool_result` is a user-turn content block by construction.

`message_parser.py:148` does carry a `case "tool_result"` under `case "assistant"`. It is
forward-compatible defensiveness; nothing was observed to produce one. Both fixed consumers still
handle that case — tolerance, not reliance.

**Stated limit:** the 42k entries are session JSONL; the SDK reads stdout stream-json. Same message
vocabulary, and line 1 is the SDK's own reading of it, but a direct observation of the stdout stream
would have cost a live model call and was not taken.

---

## M1 — `agent.py`: the `UserMessage` branch disabled

`elif isinstance(msg, UserMessage):` → `elif isinstance(msg, UserMessage) and False:`

```
mutated: UserMessage branch disabled
FAILED tests/unit/test_agent_tool_result_stream.py::test_the_error_flag_does_not_suppress_the_capture[True]
FAILED tests/unit/test_agent_tool_result_stream.py::test_the_error_flag_does_not_suppress_the_capture[False]
7 failed, 5 passed in 19.27s
--- restored ---
............                                                             [100%]
12 passed in 19.10s
```

**Proves:** without the branch, all three channels the production turn path feeds go silent — the
`agent.tool_result` event, the Auditor's `tc["result"]`, and the eval's `RETRIEVE_CHUNKS_KEY`.

## M2 — `agent.py`: the tool name read off the result block again

`tool_names_by_use_id.get(use_id, "unknown")` → `getattr(block, "name", "unknown").removeprefix(...)`

```
mutated: name read off the result block again
FAILED tests/unit/test_agent_tool_result_stream.py::test_the_evals_untruncated_chunks_are_captured
FAILED tests/unit/test_agent_tool_result_stream.py::test_parallel_tool_calls_attribute_results_to_the_right_tool
FAILED tests/unit/test_agent_tool_result_stream.py::test_an_assistant_carried_tool_result_is_still_tolerated
5 failed, 7 passed in 17.32s
--- restored ---
............                                                             [100%]
12 passed in 19.46s
```

**Proves the stacked defect is independently guarded.** `ToolResultBlock` declares only
`tool_use_id` / `content` / `is_error` (`types.py:944-949`), so `getattr(..., "name", "unknown")`
could only ever yield `"unknown"` — and `retrieval_eval.py:194` joins on `tool_name == "retrieve"`,
which `"unknown"` never matches. **Fixing the message type alone would have emitted events that
still joined to nothing.** This is why the two were proved separately.

## M3 — `red_team_probe.py`: the probe ignores `UserMessage` again

```
mutated: probe ignores UserMessage again
FAILED tests/unit/test_red_team_probe.py::test_a_successful_mutation_is_reported_as_succeeded
FAILED tests/unit/test_red_team_probe.py::test_parallel_tool_calls_are_attributed_by_tool_use_id
FAILED tests/unit/test_red_team_probe.py::test_an_identity_block_is_tagged_identity_required_end_to_end
5 failed, 18 passed in 16.73s
--- restored ---
.......................                                                  [100%]
23 passed in 16.36s
```

**Proves `5.9` as filed.** With the branch gone the transcript is literally `'\n'` — zero
`skill=… verdict=…` lines. RTX-01's assertions iterate exactly those lines, so a mutated build
reports a clean confused-deputy result while being structurally incapable of reporting anything
else. That is the ~$0.12 vacuous pass, demonstrated rather than argued.

## M4 — `red_team_probe.py`: the single hand-copied needle restored

`tuple(m.lower() for m in IDV_BLOCK_MESSAGES)` → `("requires identity verification",)`

```
mutated: back to the one hand-copied substring
FAILED tests/unit/test_idv_message_verdict_pin.py::test_the_check_failed_message_is_covered
FAILED tests/unit/test_idv_message_verdict_pin.py::test_the_matcher_derives_its_needles_rather_than_copying_them
FAILED tests/unit/test_red_team_probe.py::test_an_identity_block_is_tagged_identity_required_end_to_end
6 failed, 26 passed in 17.90s
--- restored ---
................................                                         [100%]
32 passed in 38.75s
```

**Proves `5.8`.** Two of the gate's three messages fall through to `"succeeded"` under the old
needle.

## M5 — `tools.py`: one IDV message inlined back as a literal

```
mutated: expired-token message inlined again
.......F.                                                                [100%]
FAILED tests/unit/test_idv_message_verdict_pin.py::test_every_idv_return_site_uses_a_pinned_constant
1 failed, 8 passed in 32.92s
--- restored ---
.........                                                                [100%]
9 passed in 27.30s
```

**Proves the durable half of the `5.8` fix.** The parametrised tests iterate
`IDV_BLOCK_MESSAGES`, so they cannot see a *fourth* message added as an inline literal — exactly how
the original defect arose. The AST guard can, and does.

## M6 — `test_red_team_rtx.py`: `get_sync_db` bound above the redirect again

```
mutated: get_sync_db bound before the redirect
    assert None is not None
ERROR tests/integration/test_red_team_rtx.py::test_identity_bypass - Assertio...
1 error in 29.87s
--- restored ---
.                                                                        [100%]
1 passed in 37.99s
```

**Proves the fixture fix**, and shows the added assert earning its place: the mutated run fails with
`clean_tenant seeded the ephemeral control DB but read back None — get_sync_db was not redirected`,
where the original failure was an opaque
`sqlalchemy.orm.exc.UnmappedInstanceError: Class 'builtins.NoneType' is not mapped`.

---

## M1′ / M2′ — re-proved after the module-scope lift (`5102ddf`)

The full unit gate failed `test_agent_options_seam::test_agent_py_has_no_nested_function_definitions`:
`agent.py` forbids nested `def`s, because the static seam guards attribute calls to the module-scope
function containing them and a nested def can hide a second `ClaudeAgentOptions` construction from
that attribution. The handler was lifted to module scope. **Both proofs re-run against the new
shape**, because a proof of the old shape says nothing about the shipped one:

```
###### M1' — UserMessage branch disabled (post-lift) ######
7 failed, 5 passed in 28.00s
--- restored ---
12 passed in 41.10s

###### M2' — name read off the result block again (post-lift) ######
5 failed, 7 passed in 42.49s
--- restored ---
12 passed in 31.95s
```

Worth recording separately: **the gate caught this, not review and not the targeted runs.** Every
module touched by the change was green before the full suite ran — the violated guard lives in
`test_agent_options_seam.py`, which the change does not touch and which no reasonable "related
modules" selection would have included. It is an argument for running the whole gate rather than the
neighbourhood.

## One harness defect found by a proof failing for the wrong reason

The five new `test_red_team_probe.py` tests failed on first run **after** the fix, with an empty
transcript — the same symptom as the defect. Cause was in the test harness, not the product:
`_sdk_blocks()` called `importlib.import_module` per call, so the test built messages from one set
of class objects while the probe was patched with another, and `isinstance` matched nothing. Now
cached, with the reason written at the definition. Worth recording because a harness that
manufactures the symptom under test is the most expensive kind of false negative: it would have
been read as "the fix does not work".

## What no test can prove here

The stdout stream-json shape itself. Every test in this change constructs SDK dataclasses directly,
so it verifies the *loop* against the observed shape, never that the CLI emits that shape. The three
evidence lines above are what carry that claim, and one live turn (`test_confused_deputy`, ~$0.12)
is what would close it — now worth spending, which it was not before.
