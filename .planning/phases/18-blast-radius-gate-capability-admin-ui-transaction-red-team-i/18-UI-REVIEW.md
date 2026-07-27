---
phase: 18
plan: 10
review_type: adversarial-design
reviewer: frontend-architect (adversarial)
reviewed_commits: [cbf7d8e, 1d2a258]
target: apps/admin/app/agents/[id]/deploy/page.tsx
blockers: 5
majors: 15
minors: 12
pixel_only_items: 7
stack_started: false
date: 2026-07-27
---

> **Status:** Findings pending fix. **The operator has not seen this work.** Per the
> project's frontend quality gate, plan 18-10 does not reach the operator until every
> finding here is remediated. The "Settled only by pixels" items are deferred into
> 18-10's Task 3 human gate — they are NOT verified and must not be treated as such.
>
> Transcribed into the repo by the orchestrator after the reviewing agent was
> terminated by a 529 before it could persist its own report. Substance and
> structure are the reviewer's; nothing was softened or re-ranked.

# ADVERSARIAL REVIEW — plan 18-10

Reviewed: the three commits, the whole 1713-line current `page.tsx`, `DESIGN.md`,
`globals.css`, `18-UI-SPEC.md`, the shared Gotham primitives, and the backend
contracts the new code depends on (`capability_service.py`,
`capability_envelopes.py`, `enforcement.py`, `deployment_service.py`).
**The stack was not started.**

---

## BLOCKERS

### B1. The rate-limit unit select permanently bricks the skill, on one click, irreversibly
**Location** `page.tsx:701`, `:729-737`

`allowedUnits` filters only on window length, never on whether the resulting ceiling
is a *reachable* integer:

```ts
const allowedUnits = RATE_UNITS.filter((u) => RATE_UNIT_SECS[u] <= (parsedRate?.windowSecs ?? RATE_UNIT_SECS.day))
```

All six mutating skills seed with `rate_limit: "5/hour"`
(`apps/api/app/services/capability_service.py:99`). From that factory default:

- `currentRps = 5/3600 = 0.001389`
- `allowedUnits` → `['minute', 'hour']` — **minute is offered**
- select "minute" → `maxForUnit('minute') = Math.floor(0.001389 × 60) = 0`
- `nextNum = Math.min(5, 0) = 0`
- `onSave({ rate_limit: '0/minute' })`

The server accepts it. `_parse_rate_limit` has no `> 0` guard
(`enforcement.py:174-181`), and `validate_tighten_only` compares
`proposed_rps (0) > current_rps (0.001389)` → false → **saved**. The skill is now
rate-limited to zero calls, and because the route is tighten-only every nonzero
value is a loosen, so this is **unrecoverable from the admin UI, permanently**. The
number input then renders `min={1} max={0}`.

**Why it matters** The precise inverse of D1. D1 exists so the owner cannot express
something the system will reject; here the control writes, without a keystroke, a
value the owner never expressed, and tighten-only makes it permanent. Reachable from
the factory-default state of all six Zones.

**Fix** Filter on a reachable ceiling, not window length:
`RATE_UNITS.filter(u => RATE_UNIT_SECS[u] <= currentSecs && (maxForUnit(u) ?? 1) >= 1)`.
For `5/hour` that leaves `hour` only, and a single-option select should render as
static text. Separately add a hard `nextNum >= 1` guard in `handleUnitChange` so no
code path can emit `0/<unit>`.

### B2. A failed or in-flight capability GET renders a false attestation table with a live acknowledgement checkbox
**Location** `page.tsx:1008`, `:520`, `:555-558`, `:1227-1231`

```ts
const capabilityEnvelopes = capabilityEnvelopesQuery.data?.envelopes ?? []
```

`capabilityEnvelopesQuery.error` is absent from the `loadError` aggregate
(`:1227-1231` covers only `agentQuery`, `checklistListQuery`, `widgetConfigQuery`).
When that GET fails — or while it is in flight — `enabledEnvelopes` is `[]` and the
attestation table renders "No skill is enabled yet." with the fingerprint caption and
the acknowledgement checkbox **fully enabled**. The owner can tick "I've reviewed
these limits and approve deploying with them" and Approve, having attested to a table
that falsely asserts this agent has no enabled transactional capability. Same path
drives `:1501`.

**Why it matters** BLR-02 exists so a human takes explicit responsibility for a
money-moving configuration. A network failure must never render that configuration as
empty and still collect the signature. **This is the one finding that literally ships
a lie at the moment of financial attestation.**

**Fix** Three guards. (1) Add `capabilityEnvelopesQuery.error` to `loadError`.
(2) Gate `EnvelopeAcknowledgement`'s summary-and-checkbox branch on
`capabilityEnvelopesQuery.isSuccess`; render a third state for pending/error that
suppresses the checkbox. (3) Fold `capabilityEnvelopesQuery.isSuccess` into
`isApprovable` (`:1225`).

### B3. Ticking the acknowledgement, then editing a limit, keeps the tick and the stale fingerprint — and Approve stays enabled
**Location** `page.tsx:1034-1036`, `:987-989`

`saveCapabilityEnvelope.onSuccess` invalidates only `['capability-envelopes', id]` —
not `['checklist-runs', id]` — and nothing resets `envelopeAcknowledged`. The reset
effect keys on `latestRun?.id` / `latestRun?.envelope_hash`, neither of which changes
when a capability row is PATCHed, because the run is never refetched.

Flow: owner reads the summary, ticks the box, tightens `issue_refund`'s max amount,
scrolls back. Table shows the **new** value, caption shows the **old** hash,
`envelope_drift` is still `false`, box still ticked, Approve enabled. They deploy a
configuration they never attested to, and `config abc12345…ef01` is now a false
integrity claim about data on screen.

**Why it matters** Exactly what D5 was written to prevent. D5 frames the server's 422
(T-18-BLR-03) as a backstop for tampering and races, not the mechanism. Here it is the
only thing between a normal, unhurried, single-user flow and a mis-attested deploy.

**Fix** In `saveCapabilityEnvelope.onSuccess` also
`queryClient.invalidateQueries({ queryKey: ['checklist-runs', id] })` and
`setEnvelopeAcknowledged(false)`. The refetched run carries `envelope_drift: true`,
which drives the existing drift state correctly.

### B4. A green `pass` verdict is rendered when no threshold was ever measured against
**Location** `page.tsx:464-468`, `:484-488`

When `warn_threshold_single_cents` is `null`, the `warnSingle !== null` guard fails and
control falls through to the third branch, printing **"Within threshold"** in
`--pass` green. The UI asserts a ceiling cleared a threshold that does not exist.

**Why it matters** DESIGN.md's central law makes this the most expensive possible
error: "colour is a verdict... green means it held." D4's error transposed onto the
configured side — an absence dressed as a measurement. The house pattern is four rows
up in the same file: the four pre-existing signal rows render
`Chip verdict="mute">No data` when their input is missing (`:1330`, `:1346`, `:1362`,
`:1376`).

**Fix** Add the missing branch before the pass branch: `warnSingle === null` →
`<Chip verdict="mute">No threshold set</Chip>`. Same for `warnHourly`.

### B5. The captions narrate the tighten-only rule instead of the current fact
**Location** `page.tsx:674`, `:779-780`, `:807`, `:830-831`

| Line | Shipped copy | Rule it narrates |
|---|---|---|
| 674 | "…**A higher rate is tighter.**" | the tightness metaphor |
| 807 | "Currently 5/hour. **Only windows at or narrower than this one are offered.**" | the mechanism |
| 830 | "Currently R500.00. **Once set, a ceiling can only be tightened.**" | the rule |
| 831 | "No ceiling set yet. **Once you set one, it can only be tightened from here.**" | the rule |
| 779-780 | "Enabled. **Turning this off is always available.**" / "Disabled. **The platform default allows turning this on.**" | the mechanism |

Three contracts forbid this. DESIGN.md Voice: "**No UI copy explains the design
metaphor. The room does not narrate itself.**" UI-SPEC Copywriting Contract: "No copy
in this phase explains the tighten-only *mechanism*; it states the *current fact*
only." D2: "the metaphor is taught by the fixed spatial order and the
recessed-vs-live visual treatment."

`:777` and `:855`/`:877` are correct — verbatim from D1's table. The five above are not.

**Fix** Truncate each to the present fact: "Currently 5 per hour." / "Currently
R500.00. Already your tightest available limit." / "No ceiling set." / "Enabled." /
"Disabled." and for the sampled tier D2's own example: `Currently: Sampled at 25 in 100.`

---

## MAJORS

**M1. The three new checkboxes inherit `width: 100%` and the browser's default blue accent** — `page.tsx:768-774`, `:843-851`, `:865-873`; CSS `:1602-1606`. `globals.css:276-281` applies `width: 100%; padding: 9px 12px` to all inputs; `.cap-row` styles only `:disabled`. So 18 checkboxes (3 fields × 6 Zones) render full-Zone-width, and with no `accent-color` the checkmark paints in the UA accent — system blue on Windows Chrome. The file proves the authors know the reset is needed: `.ack-checkbox input` (`:1591`) and `.warning-row input` (`:1623`) both set `width: 16px; height: 16px; accent-color: var(--live)`. An unmanaged hue on a chroma-zero bench is DESIGN.md's first-listed anti-pattern. **Fix** `.cap-row input[type="checkbox"] { width:16px; height:16px; flex:none; accent-color: var(--live); padding:0 }` and lay out label-inline-with-control like `.ack-checkbox`.

**M2. The observed figure has no visible label and no indent, so the ceiling/observed pair loses its alignment** — `page.tsx:471`, `:491`; CSS `:1574-1578`. The observed label is `.vh` (`position:absolute; clip-path: inset(50%)`), so the 168px label column collapses on that line and the figure lands at x=0, directly above the next row's label. An owner cannot tell whether `R1,200.00 observed · 7d` closes the row above or opens the one below. D3's mock indents it under its ceiling. **Fix** keep the `.vh` span for SRs, restore the visual indent (`.blast-line--observed { padding-left: 180px }`, dropped under 900px), or make each figure a two-line stack inside one `.blast-line`.

**M3. "No ceiling" is printed twice on the same line** — `page.tsx:461-463`, `:481-483`. D4.2: "the chip is the claim", no separate body copy. The literal string appears twice, 12px apart, one `--ink` one `--fail`. Halves the chip's signal value. **Fix** when null, render the chip only.

**M4. The blast-radius block has no heading; the words "Blast radius" never appear in the product** — `page.tsx:432-500`, mounted `:1384`. A bare `<div className="blast-block">` after the four-signal Ledger, no `.label`, no `.section-head`, no `Zone`. D3 permitted a 5th Ledger row **or** a labelled sub-block; this is neither. Every other block on the page carries a `.section-head` with an `h2.label`. **Fix** wrap in the existing section-head grammar or promote to a fifth Ledger row.

**M5. The page still says four signals; approval now depends on six** — `page.tsx:1251-1252`, `:1310` (untouched by the diff). "Four signals stand between this agent and a paying customer. The gate opens only while all four hold." Meanwhile `isApprovable` now also requires `envelope_drift === false` and `envelopeAcknowledged === true`, and a fifth signal renders below. An owner who cannot find why Approve is greyed has been told only four things can block it. **Fix** rewrite both to the real count or a count-free formulation.

**M6. Recessed actor-mode tiles still brighten on hover — the CSS loses the cascade** — CSS `:1613` vs `:1649`. `.tile-recessed:hover` and `.tile:hover` are both (0,2,0); source order decides and `.tile:hover` is 36 lines later. Same tie for `cursor`: `.tile-recessed` (0,1,0) vs `.tile` (0,1,0) — **so recessed tiles also still show a pointer**. D2 requires no hover state and `cursor: not-allowed`, because recessed-vs-live is the only thing teaching tightness direction without a legend. **Fix** `.tile.tile-recessed:hover`, or move the rules after `.tile:hover`.

**M7. The actor-mode radio group has no accessible group name, and `<label>Actor mode</label>` is an orphan** — `page.tsx:626-628`. No `htmlFor`, wraps no control; radios sit in a plain `div`. SR announces "Sampled, radio button, 1 of 2" with no field or skill. The correct pattern is 150 lines below: `<fieldset><legend className="vh">…` (`:1477-1478`). **Fix** `<fieldset><legend className="label">Actor mode for {skillLabel}</legend>`, drop the orphan label.

**M8. Dangling `htmlFor` on Confirmation and Verification whenever the state is "On"** — `page.tsx:839`, `:861`. In the locked state the input is not rendered, so `for` points at a nonexistent id and the `Chip` reading "On" has no programmatic association to "Confirmation". **Fix** drop `htmlFor` in the locked branch.

**M9. Six near-identical Zones expose no group name; 30+ controls share identical labels** — `page.tsx:763-764`. `Zone` renders a `<div>`; `aria-labelledby` on a `div` with no `role` is inert, so the accessible name is discarded. Every Zone's labels are textually identical. An SR user hears "Max amount, spin button" six times with nothing distinguishing `place_order` from `issue_refund` — on a panel setting irreversible money ceilings. **Fix** `<Zone as="section" aria-labelledby={…}>` and `aria-label` each control with the skill name.

**M10. One shared `isPending` disables all six Zones, and the remount `key` destroys focus on every change** — `page.tsx:1510-1513`, `:1055`. Saving one field disables ~36 controls; `key={skill:updated_at}` then forces a full remount. A focused element that becomes `disabled` is dropped from the tab order and focus falls to `<body>`; the remount loses it again. No focus restoration anywhere. **Fix** scope pending state per skill; drop `updated_at` from the key and sync local input state from props via `useEffect`.

**M11. A disabled Approve does not tell a screen reader why, and the live region contradicts it** — `page.tsx:1414-1416`, `:1428-1434`, `:1436-1440`. The new reason paragraph has no id, is not in `aria-describedby`, and is not in a live region. The `.vh` `aria-live` status announces "**The gate is open.**" whenever `gateBlocked` is false — including while Approve is disabled by an unticked acknowledgement or by drift. And `disabled` (not `aria-disabled`) removes the button from the tab order, so a Tab user never encounters any of it. **Fix** give the reason an id, append to `aria-describedby`, wrap in `role="status"`, extend the announcement to cover the envelope preconditions; consider `aria-disabled` + no-op handler.

**M12. Irreversible tightening is committed on blur, with no confirmation and no undo** — `page.tsx:722-727`, `:753-760`. Ceiling R500, owner wants R450, types `4`, tabs away → permanently **R4.00**. The mid-typing clamp (`:739-751`) compounds it: keystrokes append onto the clamped value and re-clamp. The UI-SPEC's claim that "no destructive action exists in this phase" is wrong as a UX premise — a one-way unrecoverable narrowing of a money limit, auto-committed on focus loss, is the highest-consequence write on this screen. **Fix** explicit commit (Enter or a per-field Set) plus old→new confirmation for the two numeric fields; move the clamp to commit time.

**M13. The attestation table is a filtered subset of what the hash actually covers** — `page.tsx:520` vs `deployment_service.py:563-576`. `filter((env) => env.enabled)`, but the hash SELECT has no `WHERE enabled` and `HASHED_ENVELOPE_FIELDS` includes `enabled` itself. Tighten a disabled skill's ceiling → hash changes → drift chip appears → re-running the checklist shows an identical table, because the changed skill is filtered out. Drift with no visible cause is precisely the "false drift warnings that desensitise the owner" that `capability_service.py:29-31` says the field list was designed to avoid. **Fix** render all seven rows with disabled ones recessed (`--ink-3`), or add a count line naming the hash's true scope.

**M14. The attestation table shows a raw machine enum and inconsistent absence language** — `page.tsx:563-571`. (1) `{env.actor_mode}` prints `sample_at_rate_25` / `always-on` to a non-technical owner at the moment of attestation, while the Zone 400 lines below renders the same field as "Sampled"/"Always-on" — two vocabularies for one field, machine token at the highest-stakes moment. (2) `'not set'` renders in `--ink` at `.num`, indistinguishable from a real figure; the house treatment for absence is `--ink-3`. (3) "Max amount: not set" is the same fact the blast-radius block 40px above renders as `Chip verdict="fail">No ceiling` — two claim strengths for one fact. **Fix** add an actor-mode label helper mirroring `SKILL_LABELS`; render absences with `.blast-note`; reconcile against D4.2.

**M15. The six-column attestation table has no `overflow-x: auto` container** — `page.tsx:540-576`; CSS `:1585`. `.ack-table` gets only `margin-bottom`. DESIGN.md: "Wide content scrolls inside its own container." A six-column table whose narrowest column holds the unbreakable `sample_at_rate_25` has substantial min-content width, and `.bench` keeps its 320px sidebar + 44px gap until 1100px — so the left column is ~600px in the 1101–1280 band, which is **not one of the three asserted widths**, so the parity suite will not catch it. **Fix** wrap the `Ledger` in `overflow-x: auto` regardless.

---

## MINORS

- **m1** `page.tsx:1574-1578` — `.blast-label` forks `.label` with `letter-spacing: 0.16em` vs `0.2em`, so blast labels don't match surrounding section labels. Use a layout-only modifier on `.label`.
- **m2** `:475`, `:495` — a full prose sentence rendered in `--mono` with tabular-nums. DESIGN.md scopes mono to numbers/ids/timestamps/keycaps/logs. Drop `.num` from the not-tracked branch.
- **m3** `:461`, `:481` — non-numeric `'No ceiling'` inside `<span className="num">`. Moot once M3 is fixed.
- **m4** `:549-550` vs `:569-570` — header "Confirmation" + cell "Confirmation required"; word duplicated. Cell should read "Required".
- **m5** `:700` — `?? 'hour'` shows "hour" in the select while the caption says "not set"; the control displays a value the system does not hold.
- **m6** `:709-710` — `Math.floor(currentRps * secs)` round-trips through a float and can floor to `N-1` for the current unit, making the current value exceed its own `max`. Use integer arithmetic: `Math.floor(calls * newSecs / currentSecs)`.
- **m7** `:1502-1505` — EmptyState body says "…until you enable a skill below" but renders *instead of* the grid, so there is nothing below. Spec-authored; fix in both places.
- **m8** `:569-570` — an empty cell for false `requires_confirmation` is ambiguous between "not required" and "unknown". D6 locks the empty cell, so recorded rather than changed.
- **m9** `:1496-1499` — `saveCapabilityEnvelope.isSuccess` never resets, so "saved" persists indefinitely and is section-global (a save in the sixth Zone reports success three rows away). No `aria-live`, so an auto-save gives SR users no confirmation an irreversible write happened.
- **m10** `:1399` — `<Chip verdict="fail" className="warning-cat">{category}</Chip>` is a chip as a category tag, DESIGN.md's named anti-pattern. Pre-existing and out of scope, but Phase 18's `derive_blast_radius_warnings` output now feeds this list.
- **m11** `:577` — when `envelope_hash` is `null` the caption reads "config unavailable" and the checkbox stays tickable. An attestation with no fingerprint should not be collectable.
- **m12** CSS `:1610` — `.cap-actor-tiles { grid-template-columns: repeat(2, 1fr) }` hardcodes two columns for a variable tile count. Exactly 2 today only because the Zone renders for `mutating === true` and the Off tile is filtered. If a non-mutating envelope ever reaches it, the third tile wraps and "Always-on" lands visually *left* of "Sampled", inverting the tightness ordering D2 relies on as its only legend. Use `repeat(${tiles.length}, 1fr)`.

---

## Checks run that came back clean

Reported as passes because they were run, not because they were skipped.

| # | Check | Result |
|---|---|---|
| 1 | A seventh Zone | **PASS.** `:1013-1016` filters `env.mutating === true` via `useMemo`. No `.slice`, no `length === 6`, no name list. `SKILL_LABELS` is label-only. Verified against backend: 7 entries, `confirm_action` alone `mutating: False`, GET returns all 7, `_envelope_to_dict` emits top-level `mutating`. A future seventh mutating skill appears with no edit. |
| 2 | `null` coalesced with `0` | **PASS for blast radius.** Grepped `?? 0`, `\|\| 0`, `Number(x) \|\|`; the five hits are pre-existing non-blast fields. `formatCents` takes non-nullable `number`; `centsOrNotTracked` branches on `cents === null` first. No arithmetic turns a blast null into a number. Related defects found instead: B4, B1. |
| 3 | Configured vs observed conflated | **PASS on the data model.** Four separately-named nullable fields, four separate lines, no min/max/merge/derivation. Fails on *presentation* only — M2, M3. |
| 4 | Tighten-only unreachable vs validated after submit | **PARTIAL.** Correct: `max` on both numeric inputs, no control that clears a ceiling to null, one-way toggles becoming locked chips, Off tile absent for mutating skills, `disabled` on unreachable tiers, server rejection landing inline under the field. Fails on the unit list — B1. |
| 5 | Actor-mode ordering legible without a legend | **FAIL, but not on ordering.** `ACTOR_MODE_ORDER` is a fixed Off → Sampled → Always-on and renders in that order. Failures are the copy (B5), the recessed treatment not applying (M6), and m12. |
| 6 | `Chip` as category tag / D6 | **PASS on D6, precisely.** `:569-570` render `<span className="help">Confirmation required</span>` — plain `.help` text, present only when true, empty when false. No `Chip verdict="mute"` in the acknowledgement table. **The corrected UI-SPEC finding did not regress.** One pre-existing category chip elsewhere — m10. |
| 7 | Envelope drift setting room-wide `data-gate` | **PASS.** `setGate` has exactly one call site; `gateBlocked` derivation byte-identical; `git diff HEAD~3 \| grep "^+.*data-gate"` empty. Drift only disables Approve with an inline reason and renders a second chip beside the gate chip, exactly as D5 requires. |
| 8 | New hue / new `ChipVerdict` / edited primitive / bad radius | **PASS on three, FAIL on one.** `ChipVerdict` union untouched; diffstat shows no file under `components/gotham/` and no `globals.css`; added CSS introduces no literal hex, no `rgba()`, no radius value. The hue failure is the UA checkbox accent — M1. |
| 9 | The two locked contrast values | **PASS.** `globals.css` untouched. `--ink-3: #7E8588` with its 20-15 rationale comment intact; `--fail-dim: rgba(229,72,77,0.08)`. No tidy-up toward `#6B7275` or `0.13`. The one new coloured-text rule, `.cap-error { color: var(--fail) }` at 12.5px on `--surface`, computes to 4.58:1 — the same margin the locked `.chip-fail` fix was tuned to. |
| 10 | Em/en dashes in UI strings | **PASS.** 19 hits of `[—–]` in the added lines, all inside `//` comments, zero in any rendered string. New copy uses spaced hyphens. Middle-dot at most once per line. No three-equal-card layout. |
| 11 | `prefers-reduced-motion` | **PASS.** The diff adds no `animation`, `transition`, or `@keyframes`. The pre-existing `.spinner` override and the global reduce block remain intact, so the UI2-08 parity assertion is unaffected. |
| 12 | Focus visibility | **PASS at code level.** New inputs inherit `:focus-visible { outline: 2px solid var(--live) }`. Actor tiles hide their radio but restore the ring via the pre-existing `.tile:has(input:focus-visible)`. Focus *retention* fails separately — M10. |
| 13 | `.cap-grid` collapse | **PASS.** `@media (max-width: 900px) { .cap-grid { grid-template-columns: 1fr } }` matches at exactly 900px. |

---

## Settled only by pixels

The stack was not started. These need the operator's eyes during the Task 3 walkthrough.
**Do not treat any as verified.**

1. **M1 — checkbox rendering.** Code-level facts are certain. Look for: in each Zone, do Enabled / Confirmation / Verification render as a small square or a wide bordered box spanning the Zone? Tick one — **is the checkmark bone-white or system blue?**
2. **M2 — blast-radius alignment.** Does `R1,200.00 observed · 7d` sit indented under its ceiling, or flush left in the label column? If flush, can you tell at a glance which ceiling each observed figure belongs to?
3. **M6 — recessed tile hover.** Hover a greyed actor-mode tile. Does the border brighten? Does the cursor become a pointer? Both should be inert.
4. **M15 — table overflow.** Resize to ~1150px (sidebar still 320px, left column narrowest). Does the acknowledgement table push the page horizontally? Re-check 1440/1280/900, and whether "Confirmation required" wraps to two or three lines and throws row heights out.
5. **M4 / M14 — first-glance legibility.** With fresh eyes read only the region between the four-signal Ledger and Approve. Can you tell what the money figures are, that they are the fifth signal, and what `sample_at_rate_25` means, without the spec?
6. **B4 and B2 are not reachable by looking.** A null warn threshold and a failed capability GET must be forced (block `/capability-envelopes` in devtools; null the thresholds in the DB). They are the two most consequential findings, and **a clean happy-path walkthrough will show neither.**
7. **No prototype to compare against.** `prototypes/gotham/deploy.html` contains zero occurrences of blast, envelope, capability, or acknowledge. Both new blocks are genuinely without a reference prototype; the only contract is the D3 ASCII mock in the UI-SPEC.

---

**ADVERSARIAL REVIEW: 5 blockers, 15 majors, 12 minors**

Fix first, if only two: **B2** (a network failure can collect a financial signature on
an empty configuration) and **B1** (one dropdown click permanently zeroes a skill's
rate limit from its factory default). **B3** is a close third — reachable by a patient,
careful, well-intentioned owner doing exactly what the screen invites.
