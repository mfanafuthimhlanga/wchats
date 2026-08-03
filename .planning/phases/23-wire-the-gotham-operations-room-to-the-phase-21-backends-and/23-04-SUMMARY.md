---
phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and
plan: 04
subsystem: widget
tags: [preact, widget-feedback, wire-05, css, accessibility]

# Dependency graph
requires:
  - phase: 23-01
    provides: "message_id as the fourth key on the terminal agent.response SSE payload"
provides:
  - "sendFeedback() — the widget's second POST call, a sibling of sendChat, reusing the module-level JWT"
  - "FeedbackRow.jsx — thumbs up/down + optional 1-5 CSAT radio group, the widget's first client-side degrade-without-required-prop component"
  - "The widget's first prefers-reduced-motion rule, scoped to the score row's entrance only"
  - "message_id threaded end-to-end: Celery task -> SSE payload -> Widget.jsx state -> FeedbackRow prop -> request body"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Hooks-before-early-return: a component with a required-prop degrade guard must call every hook first, unconditionally, then guard — never guard before hooks even when the guard condition happens to be invariant per instance"
    - "AST-accurate re-verification (reused from 23-01) as the fallback when a plan's own naive text-scan verify script false-matches unrelated pre-existing text (this plan's script collided with agent.py's module docstring, not run_agent_turn's own docstring as in 23-01)"
    - "Client-side submission cap (OD-7) as a companion to a server-side rate limit — bounds a specific abuse case (toggling between two states) the server limit permits and the aggregate is most distorted by"

key-files:
  created:
    - apps/widget/src/components/FeedbackRow.jsx
  modified:
    - apps/widget/src/api.js
    - apps/widget/src/widget.css
    - apps/widget/src/Widget.jsx

key-decisions:
  - "Reworded sendFeedback's explanatory comment to avoid the literal substring '.json()' after Task 1's own shape gate false-positived on prose that merely discussed the pitfall it was written to prevent (the plan explicitly instructed writing that exact comment)."
  - "Re-verified agent.py's emit() payload via an AST walk (mirroring 23-01's own precedent) after Task 2's own text-scan gate false-negatived on the module docstring's earlier quoted '\"agent.response\"' occurrence at line 10 -- a different collision site than 23-01's Deviation #1, same root-cause class."
  - "Reordered FeedbackRow's three useState calls to run before the '!messageId -> return null' guard, fixing a Rules-of-Hooks violation the plan's own action text (\"first statement is the degrade guard\") had directed. Inert under this codebase's current call pattern (messageId is fixed per message once created), but a real fragility landmine for any future change that mutates a message object in place. Found during self-review, fixed in its own commit, re-verified against every prior passing check."
  - "Omitted an explicit margin-top on .feedback-row. AgentCluster's own (unmodifiable) flex gap:4px already supplies exactly the 4px separation from the citation row that 23-UI-SPEC.md's Spacing Scale section numerically specifies; stacking an additional 4px margin on top would double the realized gap to 8px -- matching the outer .scroll-area's between-message rhythm and directly contradicting the same sentence's stated intent (\"not a new item in the stream\")."
  - "Classified the silent-revert-on-failure and two-submission-cap behaviors as human_judgment: true in the coverage block rather than claiming automated proof. Both are verified structurally (code inspection, absence-of-banner-markup checks) but not dynamically exercised -- apps/widget has no test framework (pre-existing, accepted gap per 23-RESEARCH.md) and this plan may not add one or add a new dependency (e.g. jsdom) to build one."

requirements-completed: [WIRE-05]

coverage:
  - id: D1
    description: "A customer can rate an assistant reply helpful or unhelpful; sendFeedback POSTs {message_id, conversation_id, rating} (3 keys) to /widget/agents/{id}/feedback with the real assistant message id, reusing the module-level JWT."
    requirement: "WIRE-05"
    verification:
      - kind: unit
        ref: "Task 1 automated verify block 1 (mocked-fetch transport test) -> WIDGET-FEEDBACK-TRANSPORT-OK"
        status: pass
      - kind: other
        ref: "AST walk of run_agent_turn's emit() calls (agent.py) -> agent.response keys == ['text','citations','conversation_id','message_id']; independently supersedes the plan's own text-scan gate, which false-negatives on the module docstring (see Deviations)"
        status: pass
    human_judgment: false
  - id: D2
    description: "A rated reply can additionally be scored 1-5; the second POST carries the same rating and the same message_id plus csat_score (4 keys)."
    requirement: "WIRE-05"
    verification:
      - kind: unit
        ref: "Task 1 automated verify block 1 -> body key counts (3 then 4) and field names (message_id, rating, csat_score) asserted"
        status: pass
      - kind: other
        ref: "FeedbackRow.jsx handleScore() passes the current `rating` state unchanged into post() -- same body-construction code path as the rating-only call, code-inspected"
        status: pass
    human_judgment: false
  - id: D3
    description: "An assistant message with no message_id renders no feedback control at all -- never a disabled one."
    requirement: "WIRE-05"
    verification:
      - kind: unit
        ref: "Task 1 automated verify block 2 (shape script) -> guard slice contains 'return null' before the JSX return"
        status: pass
    human_judgment: false
  - id: D4
    description: "A failed submission (network error or non-2xx, including 429) reverts the optimistic UI state silently -- no banner, no toast, no retry, one console line."
    requirement: "WIRE-05"
    verification:
      - kind: other
        ref: "Structural check: no banner/toast/role=alert markup anywhere in FeedbackRow.jsx; exactly one .catch() -> exactly one console.error() call site -> WIDGET-FEEDBACK-SILENT-FAILURE-STRUCTURAL-OK"
        status: pass
    human_judgment: true
    rationale: "Structurally strong (no code path exists that could render a failure surface) but not dynamically exercised -- apps/widget has no test framework and this plan may not add one or a new dependency (e.g. jsdom) to render/click the component headlessly. A real browser session (manual click-through) is the only way to watch the revert happen."
  - id: D5
    description: "At most two submissions are ever sent for one message (OD-7's bound): a submission counter caps outbound requests; past the cap a re-click still updates visible state but sends nothing further."
    requirement: "WIRE-05"
    verification:
      - kind: other
        ref: "Code inspection: post() checks sentCount >= MAX_SUBMISSIONS(2) before every send and increments on every send; handleRate/handleScore always call setRating/setScore before the cap check"
        status: pass
    human_judgment: true
    rationale: "Same test-framework gap as D4 -- the cap logic is simple and directly traced, but proving 'a third click sends nothing' requires simulating multiple clicks against a rendered instance, which this package cannot do without a new dependency."
  - id: D6
    description: "The transport never reads a response body -- resolves without throwing against a real 204-no-content response, and its source contains no body-parsing call."
    requirement: "WIRE-05"
    verification:
      - kind: unit
        ref: "Task 1 automated verify block 1 -> r1.status === 204 with no throw; verify block 2 -> no /\\.json\\(\\)/ match in sendFeedback's source"
        status: pass
    human_judgment: false
  - id: D7
    description: "New styles use only the widget's single existing accent (both rating directions, selected CSAT star) and the muted text colour -- no green/red/gold/amber, exactly one reduced-motion block."
    requirement: "WIRE-05"
    verification:
      - kind: unit
        ref: "Task 1 automated verify block 2 -> new .feedback-*/.csat-* CSS blocks contain --accent, contain none of --green/--red/--gold/--amber; exactly one 'prefers-reduced-motion' occurrence in widget.css"
        status: pass
    human_judgment: false
  - id: D8
    description: "No Gotham token, class, or component reaches the widget package; sse.js, AgentCluster.jsx, and both package.json manifests stay byte-unchanged."
    requirement: "WIRE-05"
    verification:
      - kind: unit
        ref: "Task 1 verify block 2 (Gotham-token regex across api.js/FeedbackRow.jsx/widget.css/Widget.jsx) + Task 1/2 git diff --quiet checks on package.json/sse.js/AgentCluster.jsx"
        status: pass
    human_judgment: false
  - id: D9
    description: "The bundle stays under its 20480-byte gzipped ceiling, proven on a real build; measured size recorded."
    requirement: "WIRE-05"
    verification:
      - kind: other
        ref: "npm run build && node scripts/check-size.mjs (run twice: pre- and post-hooks-order-fix) -> 8762 bytes, then 8764 bytes after the fix; ceiling 20480; was 8094 before this plan"
        status: pass
    human_judgment: false

duration: ~50min
completed: 2026-08-03
status: complete
---

# Phase 23 Plan 04: Widget feedback capture (WIRE-05) Summary

**Preact `FeedbackRow` (thumbs up/down + optional 1-5 CSAT), a `sendFeedback` transport sibling to `sendChat`, and the one-line `Widget.jsx` change that keeps 23-01's emitted `message_id` -- giving the already-shipped, never-called `POST /widget/agents/{id}/feedback` route its first real caller, gzipped bundle 8764 bytes against a 20480-byte ceiling.**

## Performance

- **Duration:** ~50 min
- **Completed:** 2026-08-03
- **Tasks:** 2/2
- **Files modified:** 4 (1 created, 3 modified)

## Accomplishments

- `apps/widget/src/api.js` gained `sendFeedback(apiBase, agentId, messageId, conversationId, rating, score)`: builds the `WidgetFeedbackRequest` body exactly (3 keys rating-only, 4 with a score), reuses the module-level JWT, returns the raw `Response` and never parses a body (the route answers 204 with no content).
- `apps/widget/src/components/FeedbackRow.jsx` (new): two 24x24 hand-authored stroke thumb buttons plus a 5-star `role="radiogroup"` CSAT row that appears only once a rating exists. Degrades to `return null` when no message identifier is present. Caps outbound requests at two per message (OD-7). Reverts optimistic state silently on any failed submission, logging exactly one console line.
- `apps/widget/src/widget.css` gained the widget's first `prefers-reduced-motion` rule (scoped to only the score row's entrance) and all new `.feedback-*` rules, using exclusively `--accent` and `--text-3` -- no new hue, no green/red split between the two rating directions.
- `apps/widget/src/Widget.jsx`: `onResponse` now stores `p.message_id` on the assistant message object (closing finding F-2 from 23-01's summary); `FeedbackRow` is mounted as `AgentCluster`'s third child in the assistant branch only, after `CitationRow`.
- Found and fixed a real bug during self-review: `FeedbackRow`'s `useState` calls originally followed the `!messageId` early return, violating the Rules of Hooks. The plan's own action text directed this ordering ("first statement is the degrade guard"); reordered to call all hooks unconditionally first, with zero change to rendered output. See Deviations.
- Independently re-verified the `agent.py` side of the identifier chain via an AST walk after Task 2's own text-scan verify script false-negatived on a pre-existing module-docstring line -- confirmed `agent.response` emits `['text','citations','conversation_id','message_id']` and `agent.escalated` emits `['reason','context','conversation_id']` (no identifier), matching 23-01's summary exactly.
- Real build run twice (before and after the hooks-order fix): gzipped bundle 8762 bytes, then 8764 bytes, against the 20480-byte ceiling (was 8094 before this plan -- this feature cost 670 bytes, 11716 bytes of headroom remain).
- Full backend unit suite re-run to confirm the stated Python baseline held (this plan touches no Python file): **1199 passed, 8 skipped, 0 failed** -- identical to the stated baseline.

## Task Commits

Each task was committed atomically, file-scoped (`git commit -- <file>`), per the shared-git-index constraint:

1. **Task 1: The transport sibling, the control, and its styles** - `a43f7cf` (feat)
2. **Task 2: Keep the identifier, mount the control, prove the budget** - `3904ab4` (feat)
3. **Self-review fix: hooks-order correction (not a plan task; found during adversarial self-review)** - `88bedda` (fix)

**Plan metadata:** this commit (docs: complete plan)

## Files Created/Modified

- `apps/widget/src/api.js` - `sendFeedback()` added as a sibling of `sendChat`
- `apps/widget/src/components/FeedbackRow.jsx` - new: the customer-facing rating + CSAT control
- `apps/widget/src/widget.css` - new `.feedback-*` rules + the widget's first reduced-motion block
- `apps/widget/src/Widget.jsx` - `onResponse` keeps `message_id`; `FeedbackRow` mounted after `CitationRow`

## Decisions Made

See `key-decisions` in the frontmatter for the full list with rationale. Summary:
- Two verify-script wording collisions (a `.json()`-mentioning comment; a docstring-collision on `agent.py`) were fixed by rewording/re-verifying rather than editing unrelated, correct, out-of-scope code to appease a naive gate -- consistent with 23-01's own precedent for this exact phase.
- One real bug (hooks-order) found during self-review and fixed in its own commit.
- One spacing deviation (omitted a redundant `margin-top`) to avoid silently doubling a number the UI-SPEC states explicitly.
- Two behaviors (silent revert, submission cap) honestly classified as needing human judgment rather than overclaiming automated proof neither this plan nor the codebase's existing widget-testing convention can produce without a new dependency.

## Deviations from Plan

### Auto-fixed Issues

**1. [Verification-methodology finding] Task 1's shape gate false-positives on a comment explaining the pitfall it exists to catch**

- **Found during:** Task 1, second `<automated>` verify command, first run.
- **Issue:** The gate does `if(/\.json\(\)/.test(feedbackFn)) throw ...` over `sendFeedback`'s source. The plan's own action text explicitly instructs: "Put that reason in a comment at the return." My first draft of that comment read "...Calling .json() here would throw..." -- the literal substring `.json()` inside prose tripped the same regex the gate uses to detect an actual `.json()` *call*. The gate cannot distinguish code from commentary about that code.
- **Fix:** Reworded the comment to preserve 100% of the explanatory content ("Parsing it as JSON would throw on the success path...") without ever writing the literal three-character call-syntax pattern `.json(`. Verified no other file introduced by this plan contains the pattern.
- **Files modified:** `apps/widget/src/api.js`
- **Verification:** Re-ran Task 1's shape verify block -> `WIDGET-FEEDBACK-SHAPE-OK`.
- **Committed in:** `a43f7cf` (the reworded comment was written before the first commit; no separate fix commit was needed for this one).

**2. [Verification-methodology finding] Task 2's own text-scan gate false-negatives on agent.py's module docstring**

- **Found during:** Task 2, first `<automated>` verify command.
- **Issue:** The script does `agent.slice(agent.indexOf('"agent.response"'))` to locate the terminal emit's payload. `agent.py`'s module-level docstring (line 10, pre-existing, untouched by any plan in this phase) reads: `READ guard on job_events: if an "agent.response" row already exists for this job_id...` -- a literal, double-quoted match that appears at position ~150 in the file, hundreds of lines before the real `emit(job_id, "agent.response", {...})` call at line ~981. `agent.slice(...)` anchors there instead, and the subsequent `resp.indexOf('}')` finds some unrelated closing brace from ordinary Python code between the docstring and the real call, long before `message_id` ever appears. This is the identical bug class 23-01-SUMMARY.md documented as its own Deviation #1 (a different collision site inside the same file: that one was `run_agent_turn`'s own docstring; this one is the module-level docstring above it).
- **Fix:** Did not edit `agent.py`'s docstring (accurate, out of this plan's scope -- this plan touches no Python file at all per its own hard constraints). Independently re-verified the exact claim the gate intended to check via an AST walk over `run_agent_turn`'s real `ast.Call` nodes for `emit(...)`, reading each call's event-name and payload-dict argument nodes directly -- immune to any docstring text. Confirmed: `agent.response` -> `['text', 'citations', 'conversation_id', 'message_id']` (4 keys); `agent.escalated` -> `['reason', 'context', 'conversation_id']` (3 keys, no identifier). Also ran the Widget.jsx-side half of the same gate script in isolation (the half with no docstring-collision risk) -- passes cleanly on its own.
- **Files modified:** none (verification-only finding; `agent.py` was not touched by this plan at all).
- **Verification:** AST script output above; isolated Widget.jsx-side script -> `WIDGET-SEAM-OK`.
- **Committed in:** N/A -- no code change; the finding concerns only the verify script's fragility on pre-existing, correct, out-of-scope text.

**3. [Rule 1 - Bug] FeedbackRow called hooks after an early return, violating the Rules of Hooks**

- **Found during:** Self-review pass after Task 2's commit, standing in for the adversarial design/code reviewer this project's global instructions require before any frontend work is considered done.
- **Issue:** `FeedbackRow`'s three `useState` calls followed `if (!messageId) return null`, matching the plan's own action text ("First statement is the degrade guard"). React/Preact require every hook to run unconditionally, in the same order, on every render of a given component instance -- calling them after a conditional early return breaks that contract. In this codebase's current, specific call pattern the bug is inert (a message object's `message_id` is fixed at creation and the array only ever grows by appending, so the branch a given `FeedbackRow` instance takes never flips across re-renders) -- but it is real, fragile, incorrect-by-convention code that would misbehave (or throw "Rendered fewer hooks than expected") the moment any future change mutates a message object's identifier in place instead of only ever appending fully-formed ones.
- **Fix:** Moved all three `useState` calls above the guard; the guard now runs after hooks but before any other logic. Zero change to rendered output or the acceptance-criteria guard check (which only requires `"return null"` to appear before the JSX return, not that it be the literal first statement).
- **Files modified:** `apps/widget/src/components/FeedbackRow.jsx`
- **Verification:** Re-ran Task 1's full shape + transport verify blocks (`WIDGET-FEEDBACK-SHAPE-OK`, `WIDGET-FEEDBACK-TRANSPORT-OK`), Task 2's seam checks (`WIDGET-SEAM-OK`), and a fresh `npm run build` (8764 bytes, gate passes).
- **Committed in:** `88bedda` (fix), its own atomic commit, file-scoped to `FeedbackRow.jsx` only.

---

**Total deviations:** 3 (2 verification-methodology findings requiring no code change beyond a comment reword already folded into Task 1's commit; 1 real Rule-1 bug found via self-review and fixed in its own commit).
**Impact:** No scope creep. The hooks-order fix is the only behavioral code change beyond the plan's own instructions, and it changes no observable output -- it only makes the component's hook-calling contract correct under future change, not just under today's exact usage.

## Issues Encountered

Attempted a real pixel-level screenshot verification of `FeedbackRow` (per this session's global frontend-quality-gate instructions) using a throwaway Preact preview harness (`apps/widget/src/__preview.jsx` + `apps/widget/__preview.html`, never committed) driven by Playwright. The sandbox's cached Chromium build (from `apps/admin`'s existing Playwright install) is pinned to a different browser revision than the currently-resolved `playwright-core@1.61.1` expects, and `npx playwright install chromium` timed out after 3 minutes without completing a download -- this environment appears to restrict or heavily throttle access to the Playwright CDN. Stopped the dev server, deleted all three throwaway harness files (`git status` confirms nothing from this attempt remains), and fell back to the rigorous structural/source-level verification documented throughout this summary. The two behaviors that genuinely need a live render to observe (silent revert on failure, the submission cap) are honestly marked `human_judgment: true` in the coverage block rather than overclaimed.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- WIRE-05 is now closed end-to-end: the identifier generated inside a Celery task (23-01) reaches a request body in a customer's browser (this plan), the route that has existed since Phase 21 has its first real caller, and `message_feedback` can now receive rows.
- A customer can rate a reply and, optionally, score it 1-5; a reply with no identifier degrades to no control, never a broken one; the bundle remains comfortably under its hard ceiling (8764 / 20480 bytes) with exactly one runtime dependency (`preact`).
- **Already-documented, already-accepted follow-up (not new):** `message_feedback` has no unique constraint on `message_id` (confirmed by 23-RESEARCH.md's Open Question 1 and this phase's own threat register `T-23-WF-04`, disposition `mitigate`). A customer who rates then scores the same reply can produce two rows, double-weighting that message in `thumbs_down_rate` (the CSAT average itself is unaffected -- the null-score row is filtered). This plan's two-submission cap bounds the skew to exactly 2x for any one message; a uniqueness constraint + upsert would close it fully but is a migration, out of this plan's and this phase's scope by its own rule.
- No blockers for any later phase. Manual/visual click-through verification of `FeedbackRow` in a real browser (not available in this execution sandbox) remains a reasonable follow-up before this feature is shown to an end user, per the `human_judgment: true` entries above.

## Self-Check: PASSED

- `apps/widget/src/api.js` -- FOUND, contains `sendFeedback`
- `apps/widget/src/components/FeedbackRow.jsx` -- FOUND, contains `aria-pressed`
- `apps/widget/src/widget.css` -- FOUND, contains `.feedback-*` rules and exactly one `prefers-reduced-motion` block
- `apps/widget/src/Widget.jsx` -- FOUND, contains `message_id` and `FeedbackRow`
- Commit `a43f7cf` -- FOUND in `git log`
- Commit `3904ab4` -- FOUND in `git log`
- Commit `88bedda` -- FOUND in `git log`
- `apps/widget/package.json` -- byte-unchanged (confirmed via `git diff --quiet`)
- `apps/widget/src/sse.js`, `apps/widget/src/components/AgentCluster.jsx` -- byte-unchanged (confirmed via `git diff --quiet`)
- Backend unit suite -- 1199 passed, 8 skipped, 0 failed (baseline held; this plan touches no Python file)
- No stub patterns found in any file this plan created or modified.

---
*Phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and*
*Completed: 2026-08-03*
