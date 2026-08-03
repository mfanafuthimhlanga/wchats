---
phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and
plan: 01
subsystem: api
tags: [celery, sse, widget-feedback, pytest, wire-05]

# Dependency graph
requires: []
provides:
  - "The assistant message's real id on the terminal agent.response SSE payload (WIRE-05 Gap A)"
  - "_persist_messages returns str (the assistant row's id) instead of discarding it"
  - "Repaired, mock-object-proof test coverage for every _persist_messages patch site in test_agent_task.py"
affects: ["23-04 (widget FeedbackRow — the consumer of payload.message_id)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "AST-node-accurate re-verification (ast.Call/ast.Dict walk) as a fallback when a plan's own naive text-scan verify script false-matches unrelated pre-existing text"
    - "Guard-removal demonstration relocated to the real (unmocked) call site/emit code, since the mocked helper's own body is unreachable from any test that patches it"

key-files:
  created: []
  modified:
    - apps/api/app/worker/tasks/runtime/agent.py
    - apps/api/tests/unit/test_agent_task.py

key-decisions:
  - "Followed the source, not the plan's literal verify-script text, when the two disagreed (Task 1's shape gate false-matched run_agent_turn's own pre-existing docstring); re-proved the same claims via AST inspection instead of editing the out-of-scope docstring to appease the gate."
  - "Relocated both guard-removal mutations from _persist_messages's body (plan's literal wording) to run_agent_turn's real emit-construction code, because every call site mocks _persist_messages so mutating its body is empirically inert for this test suite."
  - "Six mock sites that don't assert on the returned value share one module-level constant (_PERSISTED_ASSISTANT_MSG_ID); the two that do (first-turn, escalation) plus the new dedicated test each get a distinct literal, per the plan's own instruction."

requirements-completed: [WIRE-05]

coverage:
  - id: D1
    description: "_persist_messages returns the assistant message id (str) instead of discarding it; the caller captures it and the terminal agent.response payload carries it as message_id (four keys total); the escalation payload is untouched (still three keys); no second identifier is minted anywhere in run_agent_turn."
    requirement: "WIRE-05"
    verification:
      - kind: other
        ref: "AST-accurate re-check (ast.Call/ast.Dict walk over run_agent_turn) — see Deviations; supersedes the plan's own text-scan gate, which false-matches a pre-existing docstring line"
        status: pass
      - kind: unit
        ref: "tests/unit/test_agent_task.py#test_agent_response_carries_assistant_message_id"
        status: pass
      - kind: unit
        ref: "tests/unit/test_agent_task.py#test_first_turn_creates_conversation_and_stores_sdk_session_id"
        status: pass
    human_judgment: false
  - id: D2
    description: "All nine (eight pre-existing + one new) patch sites of _persist_messages in test_agent_task.py supply an explicit return_value; zero bare patches remain, so no test can silently receive a MagicMock in place of a message id. Both guard-removal demonstrations were run, observed red, and restored."
    requirement: "WIRE-05"
    verification:
      - kind: other
        ref: "python -c <regex shape-check from Task 2's verify block> -> GAP-A-MOCK-SITES-OK sites=9"
        status: pass
      - kind: unit
        ref: "tests/unit/test_agent_task.py (full file) -> 13 passed"
        status: pass
      - kind: unit
        ref: "tests/unit (full suite, --ignore chunking/docling) -> 1199 passed, 8 skipped, 0 failed"
        status: pass
    human_judgment: false

duration: ~45min
completed: 2026-08-03
status: complete
---

# Phase 23 Plan 01: Gap A — assistant message id on the terminal SSE event Summary

**`_persist_messages` now returns the assistant row's id instead of discarding it, and the terminal `agent.response` SSE payload carries it as `message_id` — the value `POST /widget/agents/{id}/feedback` requires — proven by nine mock-proof pytest tests, zero of which can pass on a `MagicMock` standing in for a real identifier.**

## Performance

- **Duration:** ~45 min
- **Completed:** 2026-08-03T08:34:29Z
- **Tasks:** 2/2
- **Files modified:** 2

## Accomplishments

- `_persist_messages`'s return annotation changed from `None` to `str`; it now returns the assistant message id as its final statement, after the transaction commit, with a docstring sentence explaining why the caller needs it.
- The sole call site in `run_agent_turn` captures the return value into `assistant_msg_id`.
- The terminal `agent.response` payload gained a fourth key, `message_id`, carrying that captured value, with a comment naming WIRE-05 and the T-23-GA-01 mitigation rationale. The `agent.escalated` payload is untouched (still three keys, fires before but never carries an identifier).
- All eight pre-existing bare `patch("..._persist_messages")` sites in `test_agent_task.py` (lines 234, 305, 355, 408, 483, 582, 637, 698 in the pre-edit file) now supply an explicit `return_value` — six share a module-level constant, two (first-turn, escalation) get distinct per-test literals.
- `test_first_turn_creates_conversation_and_stores_sdk_session_id` extended with a `message_id` equality assertion.
- The escalation test's capture mechanism was upgraded from `list[str]` (event names only) to `list[tuple[str, dict]]` (event name + payload), enabling two new assertions: the terminal payload carries `message_id`, the escalation payload never does.
- A new test, `test_agent_response_carries_assistant_message_id`, added and passing: asserts the key is present, equals the patch's return value, and is a `str` (not a `MagicMock`) — the assertion that makes the bare-patch regression (T-23-GA-03) impossible to reintroduce silently.
- Both guard-removal demonstrations performed, observed red, and restored (see Deviations — the mutation targets were corrected from the plan's literal wording).

## Task Commits

Each task was committed atomically:

1. **Task 1: Return the assistant message id, capture it, emit it** — `249a94e` (fix)
2. **Task 2: Repair the eight mock sites and prove the emit with a named test** — `1fed4e1` (test)

**Plan metadata:** this commit (docs: complete plan)

## Files Created/Modified

- `apps/api/app/worker/tasks/runtime/agent.py` — `_persist_messages` returns `str`; call site captures it; terminal emit gains `message_id`
- `apps/api/tests/unit/test_agent_task.py` — nine mock-proof patch sites, two extended tests, one new test

## Decisions Made

- Followed source over the plan's literal verify-script text (see Deviations #1 and #2) rather than editing out-of-scope code to make a naive gate pass.
- Kept the shared-constant / per-test-literal split exactly as the plan specified, to keep the eight-then-nine-site repair legible and to guarantee no two asserting tests can be satisfied by the same string by accident.

## Deviations from Plan

### 1. [Source-verification finding, not a code defect] Task 1's own text-scan verify script false-matches a pre-existing docstring line

**Found during:** Task 1, first `<automated>` verify command.

**Issue:** The script does `turn = inspect.getsource(m.run_agent_turn)` then `turn.index('"agent.response"')` to locate the terminal emit's payload dict via naive brace/paren scanning. `run_agent_turn`'s own docstring (lines 662-687, untouched by this plan, pre-existing before this phase) already contains the literal quoted phrase `"agent.response"` (in "returns ... immediately if an `"agent.response"` event row already exists") plus example dict literals (`{"status": "already_complete", "job_id": job_id}`, `{}`) in its `Returns:` section — all of which appear *before* the real `emit(...)` call in the function's source text. `turn.index('"agent.response"')` therefore anchors on the docstring, not the code, and the subsequent `resp.index('}')` / `resp.index(')', ...)` brace-walk terminates inside the docstring, well before `message_id` appears in the real payload. Running the script verbatim fails with `AssertionError: terminal payload does not carry the assistant message id` regardless of whether the implementation is correct.

**Fix:** Did not touch `run_agent_turn`'s docstring (the plan is explicit that Task 1 is "Three edits, all inside `agent.py`. Nothing else in the file changes.") — editing it just to satisfy a naive grep would be scope creep against an explicit constraint, and the docstring is accurate as written. Instead, independently re-verified every claim the gate intended to check via an AST walk of the real `emit(...)` call nodes: `ast.parse(textwrap.dedent(inspect.getsource(m.run_agent_turn)))`, filtering `ast.Call` nodes whose `func.id == 'emit'`, and reading each call's 2nd positional arg (event name) and 3rd positional arg (payload `ast.Dict`) directly — a technique immune to any docstring text. Confirmed: `agent.escalated` keys == `['reason', 'context', 'conversation_id']` (3, unchanged); `agent.response` keys == `['text', 'citations', 'conversation_id', 'message_id']` (4); the `message_id` value node is `ast.Name(id='assistant_msg_id')` (the captured return, not a fresh identifier); the call site's target is `assistant_msg_id`; and the literal substring `uuid.uuid4()` does not appear anywhere in `run_agent_turn`'s own source. All of Task 1's acceptance criteria hold.

**Files modified:** none (verification-only finding).

**Also fixed:** while diagnosing this, an unrelated self-inflicted issue surfaced — my first draft of the `message_id` comment referenced the route as `.../agents/{id}/feedback`, and that literal `{id}` curly brace was *itself* enough to trip the same naive brace-scan (a different, self-caused false negative, layered on top of the pre-existing one). Reworded the comment to avoid literal braces before diagnosing the deeper pre-existing issue above.

**Verification:** AST re-check script (see key-files); output `ALL AST-ACCURATE CHECKS PASS`.

**Committed in:** `249a94e` (the code is correct; this finding concerns only the verify script's fragility, which is not itself a file this plan owns).

---

### 2. [Source-verification finding] Task 2's guard-removal demonstration, as literally worded, targets code no test can observe

**Found during:** Task 2, guard-removal demonstration step.

**Issue:** The plan specifies mutation (a) "delete the return statement from the helper" and mutation (b) "return the user-message local instead of the assistant one" — both describing edits to `_persist_messages`'s body in `agent.py` — and claims these should turn `test_agent_response_carries_assistant_message_id` (and, for (a), also `test_first_turn_creates_conversation_and_stores_sdk_session_id`) red. Empirically false: every one of the nine patch sites in `test_agent_task.py` mocks `_persist_messages` entirely via `patch(..., return_value=...)`, so `run_agent_turn` never executes the real function body during any of these unit tests — the mock's configured `return_value` is what flows through regardless of what the real function does or returns. I proved this directly: deleted the real `return assistant_msg_id` statement, ran the two named tests, and both still PASSED (`2 passed`) — the mutation is inert.

**Fix:** Relocated both mutations to the real (unmocked) code path that the tests actually exercise — `run_agent_turn`'s own call-site capture and emit-payload construction, which is exactly the wiring Task 1 built and Task 2's tests are meant to guard:
- Mutation (a) equivalent: removed the `"message_id": assistant_msg_id` line from the terminal emit's payload dict. Result: both `test_agent_response_carries_assistant_message_id` and `test_first_turn_creates_conversation_and_stores_sdk_session_id` FAILED (`2 failed` — `AssertionError: agent.response payload missing message_id`), matching the plan's stated expectation exactly, just via the correct mutation target.
- Mutation (b) equivalent: changed the emitted value from `assistant_msg_id` to a different, already-in-scope local (`str(local_conversation_id)`) — the closest observable analog to "the wrong identifier is emitted," since `_persist_messages`'s internal `user_msg_id` local is not reachable from `run_agent_turn`'s scope at all. Result: `test_agent_response_carries_assistant_message_id` FAILED (`message_id must equal the value _persist_messages returned, got: '00000000-0000-0000-0000-000000000030'`).
- After each mutation, restored `agent.py` via `git checkout -- apps/api/app/worker/tasks/runtime/agent.py` (HEAD already carried Task 1's committed, correct code) before any further step, matching "restored from HEAD unconditionally before any pass or fail assertion." Re-ran the full `test_agent_task.py` suite after both restores to confirm green (13 passed) before committing Task 2.

**Files modified:** `apps/api/app/worker/tasks/runtime/agent.py`, temporarily, twice, both times restored via `git checkout --` before proceeding; final committed state is Task 1's code, byte-identical to before this demonstration.

**Verification:** Both mutations observed red (test ids named above), both restores confirmed via `git diff --stat` (clean) and a full green re-run of `test_agent_task.py` (13 passed).

**Committed in:** N/A — no net code change; the demonstration is captured here and in the two pytest transcripts above.

---

### 3. [Operational — shared git index, not a plan/code deviation] First Task 1 commit briefly swept in a sibling plan's staged file

**Found during:** Task 1 commit.

**Issue:** This repo runs three parallel plan executors (23-01, 23-02, 23-03) against one shared working tree and git index (not isolated worktrees). Between my `git add apps/api/app/worker/tasks/runtime/agent.py` and `git commit`, plan 23-02's executor staged its own in-progress file (`apps/api/app/services/redteam_programme_service.py`) in the same shared index. My commit — issued without a path-scoped `git commit -- <file>` — captured both staged files, producing a single commit that mixed Gap A (mine) with Gap B's in-progress work (theirs) under a "23-01" commit message. Caught immediately by inspecting `git show --stat HEAD` (`2 files changed` where I expected 1).

**Fix:** `git reset --soft HEAD~1` (moves HEAD back one commit only; leaves the index and working tree byte-identical to their pre-commit state — nothing discarded) → `git restore --staged apps/api/app/services/redteam_programme_service.py` (unstages only the sibling's file; its working-tree content is untouched, exactly as if my interference had never happened) → re-committed with only `agent.py` staged, verified via `git diff --cached --name-only` immediately before the commit call. For Task 2's commit, repeated the same "stage, then immediately verify the staged set before committing" discipline as a precaution; no further collision occurred (Task 2's commit shows exactly the one intended file).

**Files modified:** none beyond what was already intended; no sibling-plan content was lost or altered — `redteam_programme_service.py` returned to "modified, unstaged" and 23-02's executor went on to commit it themselves as `432888b`.

**Verification:** `git show --stat 249a94e` shows exactly `apps/api/app/worker/tasks/runtime/agent.py | 16 ++++++++++++++--`, 1 file changed. `git show --stat 1fed4e1` shows exactly `apps/api/tests/unit/test_agent_task.py`, 1 file changed.

**Committed in:** N/A (corrective git operations, not a code change).

---

**Total deviations:** 0 code-behavior deviations (all three items above are verification-methodology or shared-workspace-process findings, not changes to what was built). No Rule 1/2/3 auto-fixes were needed against the plan's functional content — the plan's own `<action>` blocks for both tasks were implemented as written.

**Impact:** None on scope or correctness. Items #1 and #2 are recorded because this phase's own framing (`23-VALIDATION.md`'s epigraph: "could this gate have failed on the defect it is listed against?") demands verify scripts be trustworthy, and both of Task 1/Task 2's *literal* scripts had a blind spot the corrected checks close. Item #3 is recorded for the orchestrator's awareness of the shared-index risk this phase's own `<git_lock_warning>` already flagged as live, not hypothetical.

## Issues Encountered

None beyond the three items documented above under Deviations (each was fully resolved before proceeding).

## Full Unit Suite — honest count and caveat

`cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py` → **1199 passed, 8 skipped, 0 failed** (baseline: 1191 passed, 8 skipped, 0 failed).

**Caveat, stated plainly:** this plan's own change adds exactly **one** new test (`test_agent_response_carries_assistant_message_id`), so a suite count attributable to Task 2 alone would be 1192, not 1199. The extra +7 reflects that `apps/api/tests/unit/test_redteam_programme.py` was concurrently modified (uncommitted) in the shared working tree by sibling plan 23-02 at the moment this full-suite run executed — confirmed via `git status --porcelain apps/api` showing that file as `M` both before and after my own commits, and via `git log` showing 23-02's `432888b` commit touched only `redteam_programme_service.py`, not its test file (i.e., the test-file changes were present on disk but not yet committed by 23-02 at run time). This is not a defect — 0 failed is 0 failed regardless of which plan's tests contributed passes — but the raw delta (+8) should not be read as "Task 2 added eight tests." It added one.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- WIRE-05's backend prerequisite (Gap A) is closed: the terminal `agent.response` SSE payload now carries `message_id`, matching `WidgetFeedbackRequest.message_id: UUID` (as a UUID-formatted string) with no new column, route, or migration.
- **Not yet complete:** WIRE-05 as a whole still needs the widget-side consumer (23-04) — `apps/widget/src/Widget.jsx`'s `onResponse` handler and a new `FeedbackRow.jsx` component — before a customer can actually submit feedback. This plan only proves the value crosses the SSE boundary; nothing in `apps/widget` was touched (correctly out of scope per the plan's own instruction: "Do not touch `apps/widget` in this plan").
- No blockers for 23-04: the payload shape (`message_id` as a plain string, UUID-formatted) is stable and covered by tests that will fail loudly if a future change alters it.

---
*Phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and*
*Completed: 2026-08-03*
