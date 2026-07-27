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
    - "Draft-follows-server-value for tighten-only inputs: CapabilityZone keys on the SKILL ALONE and syncs each draft from the server-authoritative field via an effect. It originally keyed on `${skill}:${updated_at}`, which remounted the Zone after every PATCH purely to reset three drafts and destroyed focus doing it (M10, fixed in f271373) — a remount is not a state-reset mechanism when the thing being remounted holds focus"
    - "Physical-unreachability via a capped max/min plus a filtered (never disabled) option list, and a clamp applied ONLY at commit time — never validate-after-submit, and never mid-typing (a keystroke landing on an already-clamped value is re-clamped: 600 against a R500 ceiling passed through 6, 500, 5000, 500). Mirrors the server's validate_tighten_only comparator exactly (integer calls/window rate comparison, actor-mode ordinal pairs)"
    - "Explicit old-to-new confirmation for an irreversible narrowing: both money fields stage the change and require a confirmation whose two button labels each name the amount they commit to, so the write is never a side effect of focus loss (M12 deviation from the UI-SPEC, see below)"

key-files:
  created: []
  modified:
    - "apps/admin/app/agents/[id]/deploy/page.tsx"

key-decisions:
  - "The envelope-acknowledgement summary table (D5) is filtered on enabled === true, not mutating === true — it mirrors the server's canonical_envelope_hash field set, which is not itself scoped to mutating skills, whereas the six capability Zones (D1/D2) are filtered on mutating === true per plan text"
  - "capabilityEnvelopesQuery (the GET) is defined once in Task 1 and reused by both the acknowledgement table and the Task 2 capability Zones — saveCapabilityEnvelope (the PATCH) is Task 2's own addition, so the two tasks share one query key without duplication"
  - "SUPERSEDED by the M12 deviation (commit 4a71fb0) — recorded here as the Task 1-2 decision it actually was, not as a description of the shipped code: 'Rate-limit and max-amount inputs commit on blur (or on unit/select change), not on every keystroke; the value is still clamped live via onChange.' Both halves were wrong. Blur-commit makes an irreversible money narrowing a side effect of focus loss, and the live clamp re-clamped each keystroke. Neither survives — see 'The M12 deviation' below."
  - "The Approve-disabled inline reason (env drift vs unticked acknowledgement) only renders when baseApprovable is true, so it is never shown for the pre-existing block/warnings-unacknowledged cases those already have their own messaging for"
  - "Every UI-SPEC copy string that used an em dash was rewritten with a plain hyphen per the plan's own instruction ('the hyphen is a hyphen, never an em dash'); pre-existing code comments in this file already use em dashes as house style and were left untouched since the ban is scoped to string literals, not comments"

patterns-established:
  - "Pattern: server-mirrored client-side tightness comparator (parseRateLimit/maxCallsForUnit/actorModeTier byte-match capability_service.py's _parse_rate_limit/parse_actor_mode) so a UI-offered value can never diverge from what the PATCH route accepts. maxCallsForUnit deliberately does integer arithmetic on the original calls/window pair rather than round-tripping through a per-second float, which could floor the current value below its own input max (m6)"
  - "Pattern: one vocabulary per field across every surface — actorModeLabel renders the actor_mode ordinal as 'Off' / 'Sampled at N in 100' / 'Always-on' for BOTH the attestation table and the capability Zone caption, so the machine token (sample_at_rate_25) never reaches an owner and the two surfaces can never drift apart"
  - "Pattern: aria-disabled + a guarded handler, never `disabled`, for a control that is inert only for the length of one in-flight write — a focused element that becomes `disabled` leaves the tab order and focus falls to <body>. Carries a contrast consequence: WCAG 1.4.3's inactive-component exemption does not cover an aria-disabled control, so its text steps up from --ink-3 to --ink-2"

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

## Adversarial design review remediation

`18-UI-REVIEW.md` (5 blockers, 15 majors, 12 minors) was run against `cbf7d8e` +
`1d2a258` before the operator saw the work, per the global frontend quality gate.
**Every finding is now fixed except three deliberate carve-outs.** Five remediation
commits, grouped by concern:

| Commit | Concern | Findings |
|---|---|---|
| `2d97e59` | Blockers | B1, B2, B3, B4, B5, m6 |
| `f271373` | Accessibility + per-skill state scoping | M1, M7, M8, M9, M10, M11, M15, m9, m12 |
| `4a71fb0` | Explicit commit for the two money fields | **M12 (deviation, see below)**, m5 |
| `8b86f17` | Presentation and honesty | M2, M3, M4, M5, M6, M13, M14, m1, m2, m3, m4, m7 |
| `a039aa8` | Consequences of the above | contrast of the `aria-disabled` Approve label, live-region visibility, `ack-zone` naming |

### The M12 deviation from 18-UI-SPEC.md — recorded, not silent

The UI-SPEC's Colour table asserts *"No destructive actions exist in this phase's
UI surface — tightening a limit is not a destructive action"*, and its Copywriting
Contract registers auto-save on change (`"saving…" → "saved"`, no button) as the
capability-row interaction. Tasks 1-2 implemented that faithfully: both numeric
fields committed on blur.

**That premise is wrong as a UX claim, and it has been overridden.** "Not
destructive" is a statement about the tighten-only invariant (no write can loosen
a limit), not about whether the owner can recover from a typo. A one-way,
unrecoverable narrowing of a money ceiling, auto-committed on focus loss, is the
highest-consequence write on this screen: ceiling R500, owner wants R450, types
`4`, tabs away, and the ceiling is permanently R4.00 with no undo anywhere in the
product. The mid-typing clamp made it worse, not better: a keystroke landed on an
already-clamped value and was re-clamped, so `600` against a R500 ceiling passed
through 6, 500, 5000, 500.

Both fields now stage the change and require an explicit confirmation that names
the value being replaced and the value replacing it, with both buttons labelled by
the amount they commit to. The ceiling check moved to commit time so typing is not
interfered with. The window select no longer PATCHes on its own change event at
all.

What was **not** overridden, deliberately: the accent reservation (UI-SPEC S4
lists the four places `--live` may be spent, so both confirmation buttons are
ghost; hierarchy comes from order plus `--hairline-strong` on the confirming one),
the ban on `variant="seal"` here, and the no-new-hue rule (a client-side refusal
uses `--ink-3`, not `--fail`, since S4's five permitted red uses do not include
it). The UI-SPEC's own Experience-Design principle — confirmation proportional to
severity — is what this honors.

### Carve-outs (deliberately not changed)

- **m8** — the empty cell for a false `requires_confirmation` is ambiguous between
  "not required" and "unknown". **D6 locks the empty cell.** The reviewer recorded
  it for the record, not as a change request. Untouched.
- **m10** — `<Chip verdict="fail" className="warning-cat">{category}</Chip>` uses a
  chip as a category tag, DESIGN.md's named anti-pattern. Pre-existing code,
  outside this plan's `files_modified` intent. Real defect, **logged as a
  follow-up**, not fixed here.
- **m11** — a null `envelope_hash` rendering "config unavailable" above a tickable
  checkbox. Made unreachable as a side effect of B2 rather than by a second
  independent guard: the summary-and-checkbox branch is gated on
  `attestable = envelopesLoaded && hash !== null`, so the "config unavailable"
  string no longer exists in the file and the fingerprint caption is only reached
  when the hash is real. Verified.

### Judgement calls the review did not settle

1. **B5's suggested max-amount caption was not used verbatim.** The reviewer
   proposed `"Currently R500.00. Already your tightest available limit."` The
   second sentence would be false for a field that can still be tightened (R500 is
   the *loosest* available value, not the tightest), so the caption is truncated to
   the present fact alone: `"Currently R500.00."` The contract's
   "Already your tightest available limit" string belongs on a control where no
   tighter value is reachable, which is what the Always-on tile already says
   ("Nothing stricter exists for this skill").
2. **M13 took the "render all rows recessed" option, not the count-line option.**
   The count line would name the hash's scope without making a specific drift
   explicable; rendering every covered row with the not-enabled ones recessed in
   `--ink-3` means drift always has a visible cause. The `Ledger` caption was
   updated to match.
3. **M14's absence treatment uses `num blast-note`** (the combination the
   blast-radius block already uses), so `.ledger .num` keeps the 13px numeric size
   at higher specificity while `.blast-note` supplies `--ink-3`. No new class, no
   font-size fork inside one column.
4. **M8 kept `<label>` in the locked branch, without `htmlFor`.** A `<span
   className="label">` would have forked the label element's typography (10px /
   0.2em vs 11px / 0.08em), which is m1's own complaint one control over. Dropping
   the dangling `for` is the whole defect.
5. **M9 used the reviewer's `as="section"`**, which makes each skill a named
   `region` landmark. `role="group"` would have given the same accessible name
   without adding six landmarks; `section` was kept as prescribed, and is
   defensible because each Zone is a distinct money-moving configuration an owner
   may want to jump between.
6. **The sample-rate stepper still commits on blur.** M12 named the two money
   fields; the sampled tier is unreachable from every state this UI can produce
   (all seven platform defaults seed `actor_mode: "always-on"`, the strictest tier,
   and tighten-only forbids moving down to `sampled`), so the stepper only renders
   for an envelope set to `sample_at_rate_N` out of band. Noted rather than
   extended.

### Gates re-run after remediation

- `cd apps/admin && node scripts/check-no-dusk-tokens.mjs` — **exit 0**.

  Command corrected (round-two finding NEW-7a): this was previously recorded as
  `pnpm --filter wchats-admin check:no-dusk-tokens`, which **cannot run as
  written**. There is no root `package.json` and no root `pnpm-workspace.yaml`
  in this repo, so pnpm exits `ERR_PNPM_NO_PKG_MANIFEST` before it reaches the
  filter. `apps/admin` carries its own `pnpm-workspace.yaml` and is not a member
  of any parent workspace. The invocation above is the one that reproduces.
- `cd apps/admin && npx tsc --noEmit -p tsconfig.json` — **no new error**. The one
  reported error is the pre-existing `tests/reduced-motion.spec.ts` Playwright
  fixture typing (`reducedMotion` not in `Fixtures<>`), identical before and after.
- `git diff --exit-code -- apps/admin/package.json apps/admin/pnpm-lock.yaml` —
  **clean**. No new dependencies.
- All 13 of the review's clean checks re-verified against the remediated file: the
  `mutating === true` filter is the sole membership test (no `.slice`, no
  `length === 6`, no name list); no blast-radius cents field is coalesced to 0;
  the four configured/observed fields stay separate and underived; `setGate` still
  has exactly one call site and `gateBlocked` is byte-identical; no new hue, no new
  `ChipVerdict`, no file under `components/gotham/` and no `globals.css` touched, no
  literal hex or `rgba()` in the added CSS, no new radius value; `--ink-3` and
  `--fail-dim` unchanged; zero em or en dashes in any rendered string (all six hits
  on added lines are inside comments); no new `animation` / `transition` /
  `@keyframes`; `.cap-grid` still collapses at exactly 900px; D6 intact (the two
  new `Chip verdict="mute"` are in the blast-radius block, matching the four signal
  rows' missing-input treatment, and the acknowledgement table still uses plain
  `.help` text).

## Second-round verification remediation

An independent verifier re-checked the five blockers (**all confirmed fixed**)
and re-ran the review's 13 clean checks (**all HELD**), then found **7 new
findings** — three of them introduced by round one's own fixes. All 7 are now
fixed, in six commits grouped by concern:

| Commit | Finding | Concern |
|---|---|---|
| `6973e66` | NEW-6 | Three pieces of dead code; `#blast-label` wired to a real `aria-labelledby` consumer |
| `5e34d2a` | NEW-4 | The M15 scroll container was an unnamed, role-less generic in the tab order |
| `2bd6b47` | NEW-5 | The M13 caption over-claimed the envelope hash's scope on a first deploy |
| `f6ed0b2` | NEW-2 | Three of six capability fields had no inline error site at all |
| `72f562a` | NEW-3 | Round one's own status regions were conditionally mounted |
| `456645f` | NEW-1 | Focus fell to `<body>` on every capability write, by two routes |
| this commit | NEW-7 | Two summary claims that did not reproduce |

**Introduced by round one, not pre-existing:** NEW-1 route B and NEW-3 are both
consequences of the M12 confirmation flow (`4a71fb0`); NEW-2 is a consequence of
Task 2's own `fieldErrors` map (`1d2a258`).

### Deviations from the prescribed fixes, recorded

1. **NEW-1 used `autoFocus`, not the prescribed `useRef` + `useEffect` on
   `pendingX !== null`.** React implements `autoFocus` as a `focus()` call on
   mount, so it fires for a block that appears mid-session and fires exactly
   once; an effect keyed on the `pendingRate` object would re-steal focus from
   the input on every re-render while staged. `Btn` also lives under
   `components/gotham/` and cannot be edited to forward a ref, which rules out
   the ref form for a `Btn` regardless. The refs that were added point at the
   two number inputs, for the return trip.
2. **NEW-2's `actionError` backstop is conditional.** Adding
   `saveCapabilityEnvelope.error` outright would double-report every rejection
   that already has an inline home: a page-level alert plus the inline message,
   two claims on screen for one fact, which is what D1 exists to prevent. The
   aggregate admits a capability error only when it carries no `skill`/`patch`
   context — `getToken()` refusing or `fetch` rejecting before a response
   exists, both of which throw before the `mutationFn` can attach context, and
   both of which previously vanished silently.
3. **NEW-3 dropped `role="status"` from `.cap-confirm-q` rather than trying to
   persist it.** The block containing it must mount (it holds the buttons), so a
   live region inside it can never be persistent. The question is now the
   confirm button's `aria-describedby`, and the button is focused on mount, so
   the question is announced with the button's own name: a guarantee instead of
   an unreliable promise.
4. **NEW-4 gave the scroll region its own name, not `aria-labelledby="ack-label"`.**
   The Zone already carries "Capability envelope"; two identically-named regions
   are indistinguishable in a landmark list. `Ledger` renders a `<caption>` but
   exposes no id for it and is off limits to edit, so the name is set on the
   wrapper as `aria-label="Capability limits"`.
5. **NEW-5 landed BOTH options, not either/or.** The `Ledger` caption is
   visually hidden, so a caption-only fix leaves a sighted owner with no scope
   information at all. The count line beneath the fingerprint is where they
   actually learn it, which is M13's own argument for the option it passed over.
   The all-rows rendering is unchanged, per the finding.
6. **NEW-6 rewrote the tier-0 caption instead of deleting the branch.** Deleting
   it would fold tier 0 into the "Always-on. Nothing stricter exists" arm, which
   is a false claim rather than a B5 landmine. The caption now reads its label
   from `actorModeLabel(envelope.actor_mode)` and appends the "nothing stricter"
   sentence only at tier 2, so it states the present fact for every value and
   narrates no mechanism. Removing `ACTOR_MODE_ORDER`'s tier-0 entry also made
   the `envelope.mutating` tile filter vacuous, so the pair was removed together;
   the grid still derives its column count from the tier count, so m12's
   property survives, and `actorModeTier`/`actorModeLabel` keep tier 0 because
   the attestation table must still be able to name an out-of-band `off`.

### Residuals, reported not hidden

- **The six `Btn` elements in `.cap-commit` / `.cap-confirm-actions` keep
  `disabled={isSaving}`.** NEW-1 named eight form controls, not these. The only
  route that focuses one of them *as* a save begins is the sampled stepper's
  `onBlur`, which is unreachable from every state this UI can produce (see
  judgement call 6 in the round-one list). Converting them would need
  `.btn.is-disabled` treatment replicated at two more call sites for a state
  nothing can reach.
- **Nothing was verified by looking.** The stack was not started and no
  screenshot was taken in this pass either. NEW-1's focus behaviour, NEW-3's
  announcement behaviour and NEW-5's count line all want the operator's eyes and
  a screen reader; NEW-1 in particular wants a Tab-only walkthrough of one
  capability Zone through stage → confirm → cancel → refusal.

### Known follow-up, deliberately not built in this pass

**Unchecking `enabled` commits `enabled: false` immediately, with no
confirmation.** That is a permanent kill of the skill and a larger consequence
than the ceiling narrowings M12 wrapped in a confirmation. It is unreachable
today: all seven platform defaults ship `enabled: False`, and
`validate_tighten_only` rejects `false → true` unless the platform default is
itself `true`, so no agent can reach a state where the box is ticked and
therefore untickable. **Extend M12's confirmation to the Enabled toggle before
re-enabling ever becomes reachable** — building the guard now would be a guard
for an unreachable state on an already-large diff.

### Still owed to the operator

The review's "Settled only by pixels" list (7 items) is **not** discharged by this
remediation. The stack was never started, no screenshot was taken, and Task 3
remains an unexecuted `checkpoint:human-verify`. Item 6 of that list is the one to
carry forward deliberately: **B2 and B4 are not reachable by looking** — a failed
capability GET must be forced in devtools and the warn thresholds must be nulled in
the DB, and a clean happy-path walkthrough will show neither.

## Files Created/Modified
- `apps/admin/app/agents/[id]/deploy/page.tsx` - `BlastRadiusSignal`/`ActorMode`/`CapabilityEnvelope`/`CapabilityEnvelopeList` types extending `ChecklistRun`/`DeploymentReport`; `formatCents`/`centsOrNotTracked`/`parseRateLimit`/`maxCallsForUnit`/`actorModeTier`/`isActorModeReachable`/`actorModeLabel` helpers; `SKILL_LABELS`/`RATE_UNITS`/`RATE_UNIT_SECS`/`ACTOR_MODE_ORDER` constants; `BlastRadiusBlock`/`EnvelopeAcknowledgement`/`ActorModeTiles`/`CapabilityZone` components; `capabilityEnvelopesQuery`/`saveCapabilityEnvelope` react-query hooks; `envelopeAcknowledged`/`fieldErrors`/`savingSkills`/`savedSkills` state; the "Capabilities and limits" section; `.blast-*`/`.ack-*`/`.cap-*`/`.gate-chips`/`.tile-recessed` `PAGE_CSS` rules

  Helper-name correction (round-two finding NEW-7b): the interim summary named
  `rateToPerSecond`, which commit `2d97e59` deleted while closing m6 — a
  per-second float round-trip can floor `5/hour` to `4/hour` and set a number
  input's `max` below its own current value. Its replacements are
  `maxCallsForUnit` (integer arithmetic on the original calls/window pair) and
  `actorModeLabel` (added in `8b86f17` for M14). `grep -c rateToPerSecond` on
  the file returns 0.

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
- The adversarial design review has been run (`18-UI-REVIEW.md`) and every finding remediated except three recorded carve-outs — see **Adversarial design review remediation** above. A human operator must still start the local stack (Redis, PostgreSQL, `uvicorn`, the Celery worker, `pnpm dev` — no Docker, CLAUDE.md rule 9) and walk sections A through E of Task 3's `<how-to-verify>` against a real agent with real checklist-run and capability-envelope data, plus the review's 7 pixel-only items.
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
