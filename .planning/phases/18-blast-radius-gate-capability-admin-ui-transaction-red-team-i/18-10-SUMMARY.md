---
phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i
plan: 10
subsystem: ui
tags: [nextjs, react-query, gotham, capability-envelope, blast-radius, tighten-only]

# Dependency graph
requires:
  - phase: 18-07
    provides: "envelope_hash / envelope_acknowledged_at / envelope_drift on the checklist-run payload"
  - phase: 18-08
    provides: "GET/PATCH /api/v1/agents/{id}/capability-envelopes — the 7-entry read and the tighten-only write gate"
  - phase: 18-05
    provides: "blast_radius signal on the checklist report (configured/observed cents figures, never coalesced to 0)"
provides:
  - "Blast-radius block inside the deploy page's gate section — configured ceiling and observed maximum as two labelled lines per figure (D3), honest not-tracked-yet vs no-ceiling verdict split (D4)"
  - "Envelope-acknowledgement Zone — the human-legible per-skill table the envelope hash covers, checkbox bound directly beneath it, drift state that chips twice without touching the room-wide gate (D5/D6)"
  - "Capabilities and limits section — six per-skill Zones derived from the mutating flag, every field's looser direction physically unreachable (D1/D2)"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Mutating-flag-only membership: capability Zones are derived exclusively from env.mutating === true, never a slice/length check/hard-coded skill-name list, so a future seventh mutating skill needs no second edit"
    - "Remount-on-updated_at for uncontrolled tighten-only inputs: CapabilityZone keys on `${skill}:${updated_at}` so a successful PATCH remounts the Zone with fresh server-authoritative defaults instead of hand-rolled sync effects"
    - "Physical-unreachability via clamped onChange + capped max/min attributes, never validate-after-submit — mirrors the server's validate_tighten_only comparator exactly (per-second rate comparison, actor-mode ordinal pairs)"

key-files:
  created: []
  modified:
    - "apps/admin/app/agents/[id]/deploy/page.tsx"

key-decisions:
  - "The envelope-acknowledgement summary table (D5) is filtered on enabled === true, not mutating === true — it mirrors the server's canonical_envelope_hash field set, which is not itself scoped to mutating skills, whereas the six capability Zones (D1/D2) are filtered on mutating === true per plan text"
  - "capabilityEnvelopesQuery (the GET) is defined once in Task 1 and reused by both the acknowledgement table and the Task 2 capability Zones — saveCapabilityEnvelope (the PATCH) is Task 2's own addition, so the two tasks share one query key without duplication"
  - "Rate-limit and max-amount inputs commit on blur (or on unit/select change), not on every keystroke — 'auto-save on change' is honored without hammering the PATCH endpoint on each digit typed; the value is still clamped live via onChange so the looser direction is never briefly visible before commit"
  - "The Approve-disabled inline reason (env drift vs unticked acknowledgement) only renders when baseApprovable is true, so it is never shown for the pre-existing block/warnings-unacknowledged cases those already have their own messaging for"
  - "Every UI-SPEC copy string that used an em dash was rewritten with a plain hyphen per the plan's own instruction ('the hyphen is a hyphen, never an em dash'); pre-existing code comments in this file already use em dashes as house style and were left untouched since the ban is scoped to string literals, not comments"

patterns-established:
  - "Pattern: server-mirrored client-side tightness comparator (parseRateLimit/rateToPerSecond/actorModeTier byte-match capability_service.py's _parse_rate_limit/parse_actor_mode) so a UI-offered value can never diverge from what the PATCH route accepts"

requirements-completed: [BLR-01, BLR-02]

# CAP-03 remains unchecked in ROADMAP.md — 18-VALIDATION.md's Manual-Only
# table names two visual/interaction judgements only a human can make
# (blast-radius honesty, tighten-only physical-unreachability), and this
# plan's Task 3 (the checkpoint) has not yet been discharged by the
# operator. Do not mark CAP-03 complete until the checkpoint is approved.
coverage:
  - id: D1
    description: "Blast-radius block renders configured ceiling and observed maximum as two labelled lines per figure, never merged, with a verdict chip only on configured values and quiet muted text for missing observations"
    requirement: "BLR-01"
    verification:
      - kind: other
        ref: "grep -c 'ceiling' (8) and grep -c 'observed' (15) on apps/admin/app/agents/[id]/deploy/page.tsx; grep -c 'R0.00' returns 0; every _cents reader inspected for ?? 0 / || 0 coalescing (none found)"
        status: pass
    human_judgment: true
    rationale: "Whether the rendered figures actually read as honest to a human owner (D3/D4's visual/interaction intent) is 18-VALIDATION.md's Manual-Only verification #1 — discharged in this plan's Task 3 checkpoint, not yet approved."
  - id: D2
    description: "Envelope-acknowledgement Zone shows the per-skill summary table directly above the checkbox, plain-text (not Chip) requirement labels, a short mono fingerprint, and a drift state that chips twice without setting the room-wide gate"
    requirement: "BLR-02"
    verification:
      - kind: other
        ref: "grep -c 'verdict=\"mute\"' unchanged at 0 (D6); grep -n 'const gateBlocked' shows unchanged two-term derivation (D5); grep -c 'setGate(' returns 1; 'Changed since approval' present at exactly two render sites"
        status: pass
    human_judgment: true
    rationale: "Whether the acknowledgement/drift UX reads correctly to an owner accepting financial risk is part of 18-VALIDATION.md's Manual-Only verifications, checked in Task 3 (section B), not yet approved."
  - id: D3
    description: "Six capability Zones render, one per mutating skill, derived from the mutating flag; confirm_action never renders a Zone; every field's looser direction is physically unreachable rather than validated after submit"
    requirement: "CAP-03"
    verification:
      - kind: other
        ref: "grep '.filter(...=>....mutating)' present at two call sites; no .slice(0,6)/length===6/hard-coded skill-name list found; rate-limit unit options built by filtering (not disabled); number inputs carry max, sampled stepper carries min; no <Btn seal>/chip-seal added; git diff clean on gotham/, package.json, pnpm-lock.yaml; components.json absent"
        status: pass
    human_judgment: true
    rationale: "18-VALIDATION.md's Manual-Only verification #2 (tighten-only affordance actually presents loosening as unavailable, not error-after-submit) and the exactly-six-Zones count against a live 7-entry API response are visual/interaction judgements — this is CAP-03's own gate: the requirement is deliberately left unchecked in ROADMAP.md until Task 3 is approved."

# Metrics
duration: ~35min
completed: 2026-07-27
status: in_progress
---

# Phase 18 Plan 10: Capability Admin UI + Blast-Radius Gate Summary (Tasks 1-2 of 3 — Task 3 checkpoint pending)

**Deploy page gains a two-figure blast-radius block, an envelope-acknowledgement Zone bound to a per-skill summary table, and a six-Zone "Capabilities and limits" section whose every tighten-only control is physically incapable of expressing a looser value — Task 3's human visual/interaction checkpoint has not yet run.**

## Performance

- **Duration:** ~35 min (Tasks 1-2 only; Task 3 not started)
- **Started:** 2026-07-27T~02:20Z
- **Completed:** N/A — plan not complete, stopped at the Task 3 checkpoint gate
- **Tasks:** 2 of 3 (Task 3 is `checkpoint:human-verify`, `gate="blocking"` — not executed)
- **Files modified:** 1

## Accomplishments
- **Blast-radius block** (BLR-01, inside "The gate", after the existing four-signal Ledger): "Max single action" and "Max hourly aggregate" each render as two lines — a configured ceiling with a `pass`/`fail`/"No ceiling" verdict chip, and an observed maximum that is quiet `--ink-3` text reading "Not tracked yet" (never `R0.00`) when no transactions have been observed. A zero-enabled-skill agent renders a single quiet line with no chip.
- **Envelope-acknowledgement Zone** (BLR-02, between the blast-radius block and the existing verdict-bar): a compact `Ledger` — one row per *enabled* skill (skill, rate limit, max amount, plain-text "Confirmation required"/"Verification required" cells present only when true, actor mode) — with a short `config {hash}` mono fingerprint beneath it and the acknowledgement checkbox bound directly under the table. Drift state replaces the table with the locked drift copy, chips "Changed since approval" both in the Zone's own head and beside the Shut/Open gate chip in `#gate-label`, and disables Approve with an inline reason without touching `gateBlocked`/`data-gate`. The checkbox resets to unchecked on any `latestRun.id`/`envelope_hash` change.
- **Capabilities and limits section** (CAP-03, after Appearance): the `/capability-envelopes` GET response is filtered on `mutating === true` before being mapped to Zones — exactly six render (`place_order`, `cancel_order`, `issue_refund`, `update_subscription`, `book_slot`, `update_customer_record`); the non-mutating `confirm_action` entry never renders. Each Zone exposes `enabled` (locked when both current and platform default are `false`), `rate_limit` (capped number input + unit select whose options are filtered to at-or-narrower windows, never disabled), `max_amount_cents` (currency input that can only tighten once set), `requires_confirmation`/`requires_identity_verification` (one-way toggles that become a locked `Chip verdict="live"` reading "On"), and `actor_mode` (a three-tile segmented control in fixed Off/Sampled/Always-on order with recessed-and-unreachable looser positions and the Off tile physically absent for every rendered — i.e. every mutating — skill). Auto-save mirrors the existing `saveWidgetConfig` saving/saved-stamp pattern; a server-side tighten-only rejection lands inline under the targeted field via a `fieldErrors` map, never a toast or alert.
- Both automatable gates pass: `check:no-dusk-tokens` exits 0, the scoped `tsc --noEmit` reports zero new errors (only the pre-existing `tests/reduced-motion.spec.ts` fixture-typing error remains), no GOTHAM primitive (`Chip.tsx`/`Zone.tsx`/`Ledger.tsx`) was edited, `apps/admin/package.json`/`pnpm-lock.yaml` are unchanged, and `components.json` still does not exist.

## Task Commits

1. **Task 1: Blast-radius block and envelope-acknowledgement Zone inside the gate section** - `cbf7d8e` (feat)
2. **Task 2: Capabilities and limits section — six per-skill Zones with unreachable-loosening controls** - `1d2a258` (feat)
3. **Task 3: Human verify — blast-radius honesty, acknowledgement binding, drift restraint, tighten-only affordance** - NOT EXECUTED (`checkpoint:human-verify`, `gate="blocking"`)

**Plan metadata:** this commit (docs: partial-completion summary, Task 3 pending)

## Files Created/Modified
- `apps/admin/app/agents/[id]/deploy/page.tsx` - `BlastRadiusSignal`/`ActorMode`/`CapabilityEnvelope`/`CapabilityEnvelopeList` types extending `ChecklistRun`/`DeploymentReport`; `formatCents`/`centsOrNotTracked`/`parseRateLimit`/`rateToPerSecond`/`actorModeTier`/`isActorModeReachable` helpers; `SKILL_LABELS`/`RATE_UNITS`/`ACTOR_MODE_ORDER` constants; `BlastRadiusBlock`/`EnvelopeAcknowledgement`/`ActorModeTiles`/`CapabilityZone` components; `capabilityEnvelopesQuery`/`saveCapabilityEnvelope` react-query hooks; `envelopeAcknowledged`/`fieldErrors` state; the "Capabilities and limits" section; `.blast-*`/`.ack-*`/`.cap-*`/`.gate-chips`/`.tile-recessed` `PAGE_CSS` rules

## Decisions Made
See `key-decisions` in the frontmatter above (envelope-acknowledgement table filtered on `enabled`, not `mutating`; shared `capabilityEnvelopesQuery` across Task 1/Task 2; blur-commit for numeric fields; scoped inline-reason rendering; em-dash-to-hyphen rewrite of every locked copy string while leaving the file's own pre-existing comment style untouched).

## Deviations from Plan

None - plan executed exactly as written for Tasks 1 and 2. Task 3 was intentionally not executed per this execution's explicit instruction (`checkpoint:human-verify`, `gate="blocking"` — requires the operator's own visual/interaction sign-off, not an executor self-approval).

## Issues Encountered

One self-correction during Task 1: an initial draft used the HTML entity `&middot;` for the observed-figure window annotation, which JSX does not decode (it would have rendered the literal text "&middot;" to the user). Replaced with the literal `·` character before running any gate, matching the file's own existing convention (e.g. the evals-pass-rate row's `· {failingScenarios} failing`). Caught and fixed before the Task 1 commit — not a deviation from the plan's intent, just a JSX-entity correction.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**This plan is NOT complete.** Task 3 is a `checkpoint:human-verify` gate with `gate="blocking"` covering 18-VALIDATION.md's two Manual-Only verifications (blast-radius honesty, tighten-only physical-unreachability) plus two UI-Considerations backstops (no awkward wrapping at 1280/900px, the acknowledgement table correct at 1 and at 6 enabled skills). Per this execution's explicit instructions, Task 3 was not executed, self-approved, or marked complete.

- **STATE.md, ROADMAP.md, and REQUIREMENTS.md have NOT been updated by this executor run** — that update belongs to the plan's final completion, which has not occurred. CAP-03 in particular must stay unchecked until Task 3 is discharged (see the `# CAP-03 remains unchecked` note above the `coverage` block).
- The orchestrator should run an adversarial design review against the diff, then a human operator must start the local stack (Redis, PostgreSQL, `uvicorn`, the Celery worker, `pnpm dev` — no Docker, CLAUDE.md rule 9) and walk sections A through E of Task 3's `<how-to-verify>` against a real agent with real checklist-run and capability-envelope data.
- If the operator reports a defect, it must be fixed against the named UI-SPEC decision (D1-D6) in `apps/admin/app/agents/[id]/deploy/page.tsx` and re-presented, not argued.
- Once Task 3 is approved, the plan's final steps (SUMMARY status flip to `complete`, STATE.md/ROADMAP.md/REQUIREMENTS.md updates, the metadata commit) still need to run — they are deliberately not run by this executor pass.

---
*Phase: 18-blast-radius-gate-capability-admin-ui-transaction-red-team-i*
*Status: Tasks 1-2 complete, Task 3 (human checkpoint) pending*

## Self-Check: PASSED

- FOUND: apps/admin/app/agents/[id]/deploy/page.tsx
- FOUND: commit cbf7d8e
- FOUND: commit 1d2a258
- FOUND: .planning/phases/18-blast-radius-gate-capability-admin-ui-transaction-red-team-i/18-10-SUMMARY.md
