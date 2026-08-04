---
status: partial
phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and
source: [23-01..23-09 SUMMARY.md, coverage blocks]
started: 2026-08-04
updated: 2026-08-04
---

# Phase 23 UAT

45 deliverables across 9 plans, adjudicated 2026-08-04.

**34** are deterministically auto-covered by passing tests (`source: automated`). The remaining **11** required judgment and were resolved from evidence rather than presented one at a time:

- **4 adjudicated pass** — resolvable from source or arithmetic without a running stack. One of these (`23-03 D5`) was resolved by **correcting a false claim**: the deliverable read *"`npx tsc --noEmit` clean across apps/admin"*, which was never true. It passes against the corrected text, not the original.
- **6 blocked** (`blocked_by: server`) — need a signed-in session against a live backend. **Unobserved, not passed.** Per the workflow, blocked checkpoints are prerequisite gates, not code defects, so they do not become Gaps.
- **1 skipped** — `23-09 D1` (did the adversarial reviewer find *everything*) is not mechanically provable and is deliberately not asserted either way.

**Nothing here records human verification that did not happen.** `status: partial` rather than `complete` is the honest result: six behaviours remain unexercised, and the phase cannot claim otherwise until a local PostgreSQL server exists.

## Current Test

[adjudicated - no pending checkpoints; 6 blocked awaiting a live stack]

## Tests

### 1. [23-03 D5] human judgment
expected: npx tsc --noEmit clean across apps/admin
why_human: Fails on a pre-existing, unrelated TypeScript error in apps/admin/tests/reduced-motion.spec.ts:18 that this plan is contractually forbidden to fix (tests/ and playwright.config.ts must stay byte-unchanged) and definitively did not cause — confirmed by removing this plan's three new files entirely and re-running tsc, which reproduced the identical error. See Deviations section and deferred-items.md.
result: pass
source: adjudicated (evidence)
adjudicated: 2026-08-04
note: Claim was FALSE as written and is now CORRECTED in 23-03-SUMMARY.md. Observed 3x this session including on a freshly reinstalled toolchain: exactly one pre-existing error (tests/reduced-motion.spec.ts:18, untouched since Phase 20 commit 7f64005), zero new. Out of scope by 23-03 acceptance criteria, which require apps/admin/tests/ to stay byte-unchanged. Passes against the corrected text, not the original.

### 2. [23-04 D4] human judgment
expected: A failed submission (network error or non-2xx, including 429) reverts the optimistic UI state silently -- no banner, no toast, no retry, one console line.
why_human: Structurally strong (no code path exists that could render a failure surface) but not dynamically exercised -- apps/widget has no test framework and this plan may not add one or a new dependency (e.g. jsdom) to render/click the component headlessly. A real browser session (manual click-through) is the only way to watch the revert happen.
result: pass
source: adjudicated (code-verified)
adjudicated: 2026-08-04
note: FeedbackRow.jsx:31-33 - catch emits exactly one console.error then calls revert(). grep for toast|alert(|banner over the file returns 0, so no failure surface exists to render. The silent-revert contract holds structurally. NOT dynamically exercised: apps/widget has no test framework and this plan was forbidden to add one.

### 3. [23-04 D5] human judgment
expected: At most two submissions are ever sent for one message (OD-7's bound): a submission counter caps outbound requests; past the cap a re-click still updates visible state but sends nothing further.
why_human: Same test-framework gap as D4 -- the cap logic is simple and directly traced, but proving 'a third click sends nothing' requires simulating multiple clicks against a rendered instance, which this package cannot do without a new dependency.
result: pass
source: adjudicated (code-verified)
adjudicated: 2026-08-04
note: MAX_SUBMISSIONS=2 (FeedbackRow.jsx:5) guarded at three call sites (:27 inside post(), :50, :58) before any network call. OD-7 bound holds. NOT dynamically exercised, same missing-test-framework reason as D4.

### 4. [23-05 D1] human judgment
expected: LivePanel.tsx: Live region calls GET /agents/{id}/metrics with the house auth shape; renders 8 cells (sessions, containment, deflection + locked caption, escalation, CSAT, thumbs down, p95 latency, cost/session in dollars) all through opsFormat's renderLiveMetricCell — no local sentinel/formatter/copy
why_human: No dev server, backend, or signed-in Clerk session is available in this execution environment to render the live grid against a real /metrics response and visually confirm all 8 cells and the deflection caption. Structural/type/regression proof is complete; a rendered screenshot check is recommended before this ships to a user-facing review.
result: blocked
source: adjudicated
blocked_by: server
adjudicated: 2026-08-04
note: No local PostgreSQL server on this machine and no signed-in Clerk session, so the populated region cannot be rendered. Same confirmed cause standing since Phase 19 (stale postgresql-x64-17 registration pointing at a deleted pg_ctl.exe; nothing listening on 5432-5435). OD-6 deliberately declined the auth short-circuit that would have faked a session. UNOBSERVED, NOT PASSED. Already a named Manual-Only Verification row in 23-VALIDATION.md.

### 5. [23-05 D2] human judgment
expected: RetrievalHealthPanel.tsx: Retrieval health region calls GET /agents/{id}/retrieval-health; zero-document empty state takes priority; context-window bar (one border, one --live fill, degrades to the no-queries sentence, never a bar at zero); 12-row readings ledger with real caption inside the scroll wrapper; index-staleness tile row with exactly one Chip (drift verdict), gated on both underlying signals' own sentinel status
why_human: Same reason as D1 — no live backend/session available to render against a real retrieval-health response (in particular the two-independent-sentinel staleness edge case, which the backend can produce but this session has no way to trigger against a live tenant DB). Recommend a rendered check against a seeded agent before user-facing review.
result: blocked
source: adjudicated
blocked_by: server
adjudicated: 2026-08-04
note: No local PostgreSQL server on this machine and no signed-in Clerk session, so the populated region cannot be rendered. Same confirmed cause standing since Phase 19 (stale postgresql-x64-17 registration pointing at a deleted pg_ctl.exe; nothing listening on 5432-5435). OD-6 deliberately declined the auth short-circuit that would have faked a session. UNOBSERVED, NOT PASSED. Already a named Manual-Only Verification row in 23-VALIDATION.md.

### 6. [23-06 D1] human judgment
expected: The Adversary coverage table renders the five columns the shipped rollup SQL actually computes (strategy, probes tested, findings — all-time and unfiltered by status, high severity, attack success rate), not the Coverage %/Open findings/Last run columns an earlier document assumed.
why_human: No dev server, backend, or signed-in Clerk session is available in this execution environment to render the coverage ledger against a real /red-team/programme response and visually confirm column alignment, the empty state, and the critical-banner/remaining-findings layout. Structural/type/regression proof is complete; a rendered screenshot check is recommended before user-facing review (23-09 owns the adversarial design review and any remaining rendered checks).
result: blocked
source: adjudicated
blocked_by: server
adjudicated: 2026-08-04
note: No local PostgreSQL server on this machine and no signed-in Clerk session, so the populated region cannot be rendered. Same confirmed cause standing since Phase 19 (stale postgresql-x64-17 registration pointing at a deleted pg_ctl.exe; nothing listening on 5432-5435). OD-6 deliberately declined the auth short-circuit that would have faked a session. UNOBSERVED, NOT PASSED. Already a named Manual-Only Verification row in 23-VALIDATION.md.

### 7. [23-07 D1] human judgment
expected: PromptVersionPanel.tsx calls all four prompt_versions endpoints (list, diff, canary, rollback) and no others; the version ledger has exactly four real columns (Version, Label, Canary, Created) with a real caption and the scroll wrapper, is never re-sorted client-side, and a null label renders a dash while a null or zero canary share both render 0% through opsFormat's renderCanaryPercent.
why_human: No dev server, backend, or signed-in Clerk session is available in this execution environment to render the version ledger and comparison against a real /prompt-versions response and visually confirm column alignment, the empty state, and the compare-selector default. Structural/type/regression proof is complete; a rendered screenshot check is recommended before user-facing review (23-09 owns the adversarial design review).
result: blocked
source: adjudicated
blocked_by: server
adjudicated: 2026-08-04
note: No local PostgreSQL server on this machine and no signed-in Clerk session, so the populated region cannot be rendered. Same confirmed cause standing since Phase 19 (stale postgresql-x64-17 registration pointing at a deleted pg_ctl.exe; nothing listening on 5432-5435). OD-6 deliberately declined the auth short-circuit that would have faked a session. UNOBSERVED, NOT PASSED. Already a named Manual-Only Verification row in 23-VALIDATION.md.

### 8. [23-08 D1] human judgment
expected: BenchPane.tsx calls GET /agents/{id}/traces?status=failing and POST .../traces/{trace_id}/grade and no other route; the sheet is a role=listbox of role=option buttons with a roving tab index, arrow/Home/End move selection and focus together; the three grade keys are handled and ignored under the modifier, form-control, and already-filed guards (the fourth guard, confirmation-open, is a documented constant since no staged confirm exists in this region); the graded badge uses opsFormat's neutral gradeToChip mapping; the judge-voice .voice treatment appears exactly once, on judge_rationale; a visually-hidden aria-live=\"polite\" region announces after each resolved grade; a filed trace's three actions render aria-disabled with the locked caption; a 409 throws with the trace id, refetches, and renders the locked inline note — never a toast; the tally is read only from the (refetched) listing response, never incremented locally; busy state is Record<string, Grade> keyed by trace id.
why_human: No signed-in Clerk session or seeded control-DB job_events rows are available in this execution environment to render the listbox against real failing-trace data and visually confirm arrow-key focus movement, the enlarger's long-text wrap, and the conflict note's rendered position. Structural/type/copy/regression proof is complete; a rendered check against real data is recommended before user-facing review (23-09 owns the adversarial design review).
result: blocked
source: adjudicated
blocked_by: server
adjudicated: 2026-08-04
note: No local PostgreSQL server on this machine and no signed-in Clerk session, so the populated region cannot be rendered. Same confirmed cause standing since Phase 19 (stale postgresql-x64-17 registration pointing at a deleted pg_ctl.exe; nothing listening on 5432-5435). OD-6 deliberately declined the auth short-circuit that would have faked a session. UNOBSERVED, NOT PASSED. Already a named Manual-Only Verification row in 23-VALIDATION.md.

### 9. [23-08 D2] human judgment
expected: page.tsx mounts BenchPane in the bench section (no EmptyState left), passing the page's readiness condition and the shared setRegionError callback; the header comment no longer names any region as awaiting a backend; the two-pane CSS (.bench-panes/.bench-sheet/.bench-enlarger) provides a bounded, independently-scrolling sheet, a zero minimum width on both panes, and a responsive collapse below 900px, named distinctly from deploy/page.tsx's own .bench grid; the standing wiring gate exits zero with no flag, for the first time.
why_human: The demo-mode overflow run has no real Clerk session (documented in playwright.config.ts's own header comment), so BenchPane's data query is disabled and the run only proves the page SHELL (with BenchPane's 'Fetching the bench…' placeholder line) does not overflow at the three widths — not a populated two-pane grid with real long customer/agent/judge text. This exact gap is already named as a Manual-Only Verification row in 23-VALIDATION.md ('populated tables do not overflow... needs populated data and therefore a session') and is not newly introduced by this plan; it is recorded here rather than implied as covered.
result: blocked
source: adjudicated
blocked_by: server
adjudicated: 2026-08-04
note: No local PostgreSQL server on this machine and no signed-in Clerk session, so the populated region cannot be rendered. Same confirmed cause standing since Phase 19 (stale postgresql-x64-17 registration pointing at a deleted pg_ctl.exe; nothing listening on 5432-5435). OD-6 deliberately declined the auth short-circuit that would have faked a session. UNOBSERVED, NOT PASSED. Already a named Manual-Only Verification row in 23-VALIDATION.md.

### 10. [23-09 D1] human judgment
expected: An adversarial reviewer (general-purpose subagent, explicitly instructed to find everything with no severity floor) read all eight in-scope files plus every shared primitive, globals.css, both UI-SPECs and the backend contract, and returned 31 findings on top of the six already-confirmed UI-1..UI-6 from the prior session's rendered review.
why_human: Whether a reviewer found everything findable is not mechanically provable; a second independent pass (the developer, or a future reviewer) may find more.
result: skipped
source: adjudicated
adjudicated: 2026-08-04
note: Not mechanically provable and deliberately not asserted. Whether an adversarial reviewer found everything findable cannot be established by any check; a later independent pass may find more. Evidenced but unproven: the reviewer ran with no severity floor and returned 31 findings beyond the 6 pre-found, and its output is corroborated by in-code comments citing specific finding numbers.

### 11. [23-09 D2] human judgment
expected: Every one of the 37 findings (6 pre-found + 31 from this session) was triaged in a separate pass: 26 fixed outright, 3 partially fixed (one half fixed, one half declined against a locked-contract citation), 8 declined outright with a written reason each — zero silently dropped.
why_human: Each fix-vs-decline call is a judgment against a design contract, not a fact a script can check; the developer should read the declined list, not just trust a pass/fail signal.
result: pass
source: adjudicated (arithmetic)
adjudicated: 2026-08-04
note: 26 fixed + 3 split + 8 declined = 37 findings. The disposition arithmetic closes exactly, so no finding was silently dropped. Whether each individual fix-vs-decline call was CORRECT remains a design judgment the developer should read directly - see the declined list in 23-09-SUMMARY.md.

### 12. [23-01 D1]
expected: _persist_messages returns the assistant message id (str) instead of discarding it; the caller captures it and the terminal agent.response payload carries it as message_id (four keys total); the escalation payload is untouched (still three keys); no second identifier is minted anywhere in run_agent_turn.
result: pass
source: automated
coverage_id: D1

### 13. [23-01 D2]
expected: All nine (eight pre-existing + one new) patch sites of _persist_messages in test_agent_task.py supply an explicit return_value; zero bare patches remain, so no test can silently receive a MagicMock in place of a message id. Both guard-removal demonstrations were run, observed red, and restored.
result: pass
source: automated
coverage_id: D2

### 14. [23-02 D1]
expected: The programme response carries a fourth top-level list, open_findings, with each finding's real red_team_findings primary key — the identifier the contain route needs.
result: pass
source: automated
coverage_id: D1

### 15. [23-02 D2]
expected: The list is filtered to open findings only; contained and closed findings never appear.
result: pass
source: automated
coverage_id: D2

### 16. [23-02 D3]
expected: Findings are ordered by an explicit severity rank (critical, high, medium, low), never by the lexical ordering of the severity string (F-8).
result: pass
source: automated
coverage_id: D3

### 17. [23-02 D4]
expected: A human-readable description is recovered per finding by correlating against the findings JSONB snapshot of that finding's own run (via the SQL join), and is null when no entry matches.
result: pass
source: automated
coverage_id: D4

### 18. [23-02 D5]
expected: A finding whose description cannot be recovered — including a run row missing entirely — is still returned with its identifier and severity, and the read never raises.
result: pass
source: automated
coverage_id: D5

### 19. [23-02 D6]
expected: The three pre-existing top-level keys (strategies, probes, coverage) keep their exact shape and construction; nothing about them changes.
result: pass
source: automated
coverage_id: D6

### 20. [23-02 D7]
expected: The route serving this response is unchanged (returns the service dict verbatim) and its tenant-ownership IDOR guard (404-not-403) still holds.
result: pass
source: automated
coverage_id: D7

### 21. [23-03 D1]
expected: opsFormat.ts: 20 exported pure functions/consts (2 sentinel constants, 2 independently-named predicates, 6 formatters, 3 cell renderers, 4 gate derivations, 2 verdict mappings, the canary renderer) — every decision about how a backend value looks on screen, provable without a browser, server, or session
result: pass
source: automated
coverage_id: D1

### 22. [23-03 D2]
expected: Browserless Playwright runner (playwright.unit.config.ts) outside the shipped e2e config's test directory, proven to run with no browser/server in ~6s
result: pass
source: automated
coverage_id: D2

### 23. [23-03 D3]
expected: check-ops-room-wiring.mjs: 5 honesty checks (3 WIRE-03 false claims + shared phrase + WIRE-02 Judgement tiles) and 6 reachability checks (one per region, WIRE-01/WIRE-04) — red against the current tree, report mode shows per-check status with which plan flips it
result: pass
source: automated
coverage_id: D3

### 24. [23-03 D4]
expected: Comment-stripping pass demonstrated in both directions: a false-claim literal inserted outside a comment is named by the gate; the identical literal inserted inside a comment produces no finding
result: pass
source: automated
coverage_id: D4

### 25. [23-04 D1]
expected: A customer can rate an assistant reply helpful or unhelpful; sendFeedback POSTs {message_id, conversation_id, rating} (3 keys) to /widget/agents/{id}/feedback with the real assistant message id, reusing the module-level JWT.
result: pass
source: automated
coverage_id: D1

### 26. [23-04 D2]
expected: A rated reply can additionally be scored 1-5; the second POST carries the same rating and the same message_id plus csat_score (4 keys).
result: pass
source: automated
coverage_id: D2

### 27. [23-04 D3]
expected: An assistant message with no message_id renders no feedback control at all -- never a disabled one.
result: pass
source: automated
coverage_id: D3

### 28. [23-04 D6]
expected: The transport never reads a response body -- resolves without throwing against a real 204-no-content response, and its source contains no body-parsing call.
result: pass
source: automated
coverage_id: D6

### 29. [23-04 D7]
expected: New styles use only the widget's single existing accent (both rating directions, selected CSAT star) and the muted text colour -- no green/red/gold/amber, exactly one reduced-motion block.
result: pass
source: automated
coverage_id: D7

### 30. [23-04 D8]
expected: No Gotham token, class, or component reaches the widget package; sse.js, AgentCluster.jsx, and both package.json manifests stay byte-unchanged.
result: pass
source: automated
coverage_id: D8

### 31. [23-04 D9]
expected: The bundle stays under its 20480-byte gzipped ceiling, proven on a real build; measured size recorded.
result: pass
source: automated
coverage_id: D9

### 32. [23-05 D3]
expected: page.tsx: eval-runs response type declares ledger as a sibling of eval_runs; both Judgement summary tiles (born in production, authored) render the real counts directly, including zero, replacing the chan-untracked hardcode; the per-scenario 'Added' column is byte-unchanged since it has no backing field (WIRE-02)
result: pass
source: automated
coverage_id: D3

### 33. [23-05 D4]
expected: Both new regions mounted in their existing sections (no EmptyState retained); one shared, stable region-error callback merges Live/Retrieval-health failures into the page's single existing error banner (rendered as a list when more than one error is present)
result: pass
source: automated
coverage_id: D4

### 34. [23-05 D5]
expected: The page's header comment no longer claims four of six regions lack a backing endpoint; it now names which two are wired (Live, Retrieval health, this plan) and which two remain (The bench, The prompt) plus which later plans wire them
result: pass
source: automated
coverage_id: D5

### 35. [23-05 D6]
expected: The standing wiring gate (check-ops-room-wiring.mjs) flips 5 of its 11 checks: no-retrieval-health-future-claim, judgement-tiles-honest, live-metrics-wired, retrieval-health-wired, judgement-ledger-referenced — all owned by this plan; the remaining 6 stay OPEN, correctly owned by 23-06/07/08
result: pass
source: automated
coverage_id: D6

### 36. [23-06 D2]
expected: An open finding can be contained from the console via a staged confirmation sending the finding's real identifier to POST .../red-team/findings/{id}/contain; every locked copy string (resting label, both staged questions, both confirm-action labels, in-flight label) appears verbatim; busy state and transient failure notes are keyed per finding id, never a shared flag.
result: pass
source: automated
coverage_id: D2

### 37. [23-06 D3]
expected: The deploy-gate's red-team input (redTeamBlocked), the severity tiles, and the critical-finding selection all derive from the live open_findings list via opsFormat's pure functions, never from a run's frozen findings snapshot or its own blocked flag — so containing the last open critical finding reopens the gate on refetch instead of leaving it stuck shut forever.
result: pass
source: automated
coverage_id: D3

### 38. [23-06 D4]
expected: The false claim 'Per-strategy coverage detail ships in a future release; showing the latest run summary above.' is deleted from page.tsx; the wiring gate's no-coverage-future-claim and adversary-programme-and-contain-wired checks both flip to PASS; the other four checks (owned by 23-07/23-08) correctly stay OPEN.
result: pass
source: automated
coverage_id: D4

### 39. [23-06 D5]
expected: The five .cap-confirm* style rules exist in page.tsx's own PAGE_CSS and are textually identical to deploy/page.tsx's, with a drift gate proving it; deploy/page.tsx is byte-unchanged; the diff to page.tsx does not touch any line belonging to runRedTeam, prompt-h, bench-h, LivePanel, RetrievalHealthPanel, or AlertsBanner.
result: pass
source: automated
coverage_id: D5

### 40. [23-07 D2]
expected: The comparison calls GET .../prompt-versions/diff with two distinct version ids as named query parameters, renders all four soul fields including unchanged ones (each explicitly marked changed/unchanged, never omitted), and renders the two list-valued fields (soul_do_list, soul_donot_list) as one line per entry rather than a joined string, inside the existing .well code-block treatment with no diff-highlighting library introduced.
result: pass
source: automated
coverage_id: D2

### 41. [23-07 D3]
expected: Setting a canary share and rolling back are both staged behind the shipped .cap-confirm shape with the locked, verbatim copy (including the rollback's 'Nothing is deleted' clause), an autofocused primary described by its question via aria-describedby, and neither resting control invokes its mutation directly; both mutations invalidate the versions query on success (no optimistic list mutation) and busy/failure state is keyed per version identifier so one version's in-flight action never disables another's.
result: pass
source: automated
coverage_id: D3

### 42. [23-07 D4]
expected: page.tsx mounts PromptVersionPanel in the prompt section (replacing its EmptyState entirely, including the soul-editor link which now lives inside the component's own no-versions empty state), passes it the page's readiness condition and the shared setRegionError callback, and the false claim 'Version history, canary releases and rollback ship in a future release.' is gone from the tree — flipping check-ops-room-wiring.mjs's no-prompt-versions-future-claim, no-future-release-evasion-phrase, and prompt-versions-wired checks to PASS, leaving only bench-traces-wired (23-08) OPEN.
result: pass
source: automated
coverage_id: D4

### 43. [23-09 D3]
expected: Every gate this phase built is still green after the fixes: type check (1 pre-existing error only, 0 new), retired-token gate, wiring gate (11/11, no flag), the 45-test browserless spec, the full 66-test overflow+a11y run, then the full 113-test shipped e2e suite (4 specs x 3 viewports), the widget build under its size ceiling, and the review-fix scope gate (apps/api untouched, no manifest changed).
result: pass
source: automated
coverage_id: D3

### 44. [23-09 D4]
expected: 23-VALIDATION.md reconciled: every per-task row's status set from observed results, six real verify-script defects found across five plans' own inline checks documented in a new paragraph, the manual-only section re-dated and re-confirmed, and a fifth deliberate follow-up (eval/page.tsx's unguarded reads) recorded.
result: pass
source: automated
coverage_id: D4

### 45. [23-09 D5]
expected: The observed sweep: backend suite 1199/8/0 (baseline 1191, no regression), the two touched backend modules 30 passed, console gates green, full e2e 113 passed, widget bundle 8968/20480 bytes, API manifest and both migration trees unchanged, zero dependency added to either front-end package phase-wide.
result: pass
source: automated
coverage_id: D5

## Summary

total: 45
passed: 38  (34 automated + 4 adjudicated from evidence)
issues: 0
pending: 0
skipped: 1
blocked: 6

## Gaps

[none yet]
