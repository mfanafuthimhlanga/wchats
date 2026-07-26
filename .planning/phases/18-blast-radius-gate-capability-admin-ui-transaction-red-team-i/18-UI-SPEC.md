---
phase: 18
slug: blast-radius-gate-capability-admin-ui-transaction-red-team-injection-defense-extensions
status: draft
shadcn_initialized: false
preset: none
created: 2026-07-26
---

# Phase 18 — UI Design Contract

> Visual and interaction contract for the two Phase 18 requirements that have a UI surface:
> **CAP-03** (capability admin UI) and **BLR-01/BLR-02** (blast-radius panel in the existing
> M8 Pre-Deploy checklist). RTX-01..04 and SEC-01..03 are backend-only — no screens are
> specified or implied for them anywhere in this document.
>
> **This is a brownfield re-skin target, not a greenfield design.** The Gotham "Bone on
> Graphite" system shipped and was verified in Phase 20/21. Every token, primitive, and law
> below is READ from `DESIGN.md` and `apps/admin/app/globals.css`, not invented here. Where
> this phase needs a genuinely new interaction (the tighten-only controls), the shape is
> composed from existing primitives (`Zone`, `Chip`, `Ledger`, `Btn`, native `.field`/`label`/
> `.help`/`input`/`select`) — no new visual language, no new hue, no new primitive component
> family.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none — Gotham is a hand-built token system, not shadcn. No `components.json` exists anywhere in `apps/admin`; do not initialize shadcn for this phase. |
| Preset | not applicable |
| Component library | none (Radix/base-ui absent) — bespoke primitives in `apps/admin/app/components/gotham/` |
| Icon library | inline bespoke stroke SVGs, page-local (see `deploy/page.tsx`'s `FloatIcon`/`PanelIcon`/`ModalIcon` for the existing convention) |
| Font | `--display` Space Grotesk 500 (headings) · `--sans` Inter (body, 14px/1.55) · `--voice` Newsreader italic (judge/consequence copy only) · `--mono` JetBrains Mono (every number, id, timestamp) |

**Existing primitives this phase MUST reuse, not re-invent:** `Zone`, `Chip` (verdict-only:
`live`/`pass`/`fail`/`seal`/`mute`), `Ledger` + `LedgerColHead`/`LedgerRowHead`/`LedgerCell`,
`Btn` (`primary`/`ghost`/`seal`), `EmptyState`, `GateProvider`/`useGate` (`data-gate` shutter).
Native form grammar already defined in `globals.css` — `.field`, `label`, `.help`, `input`,
`select`, `input[type="checkbox"]`, `input[type="range"]` — is the only form vocabulary
available; do not import a component library to get sliders/switches. The `AppearanceTile`
pattern in `deploy/page.tsx` (`Zone as="label"` wrapping a hidden radio + `data-live`) is the
precedent for any segmented/tile-style choice control this phase needs (the actor-mode
selector, §Locked Interaction Decisions D3).

---

## Spacing Scale

Not re-decided in this phase — brownfield. Gotham does not use a generic 4px multiple
system; it uses the literal values already shipped in `globals.css` and must be matched
exactly, not approximated to the nearest "clean" 8pt number:

| Context | Value | Source |
|---------|-------|--------|
| `.zone` padding | 20px | `globals.css:222` |
| `.page` padding | `34px 40px 72px` | `globals.css:202` |
| `.section` top padding / margin | `26px` / `30px` | `globals.css:210` |
| `.field` bottom margin | 18px | `globals.css:285` |
| `.tiles` grid gap | 12px | `globals.css:855` |
| Ledger cell padding | `12px 14px` | `globals.css:330` |
| Row/control gap (`.warning-row`, `.verdict-bar`) | 12–14px | `globals.css:832,842` |

Exceptions for this phase: none. The new "Capabilities and limits" section and the blast-radius
panel are new `<section className="section">` blocks inside the existing `deploy/page.tsx`
`.bench` column — they inherit `.section`/`.zone`/`.field` spacing verbatim.

---

## Typography

Not re-decided — inherited verbatim from `globals.css`. No new size, weight, or family is
introduced.

| Role | Size | Weight | Family | Line Height |
|------|------|--------|--------|-------------|
| Section label (`.label`) | 10px | 700 | `--mono`, uppercase, 0.2em tracking | 1 |
| Body / field help (`.help`) | 12.5px | 400 | `--sans` | 1.4 |
| Field value / input text | 13.5px | 400 | `--sans` | 1.4 |
| Ledger cell | 13.5px | 400 | `--sans` (`.num` cells: `--mono`) | 1.4 |
| Chip label | 10.5px | 700 | `--mono`, uppercase, 0.08em tracking | 1 |
| Consequence / judge copy (`.voice`) | 15–16px | 400 italic | `--voice` | 1.62 |
| Section heading (`h2.label`) | 10px | 700 | `--mono` | — (same as label) |

Numbers are always `--mono` and right-aligned inside a `Ledger` (`.ledger .num`). This applies
to every new figure this phase introduces: cents amounts, rate-limit counts, the envelope-hash
short string, and the observed-maximum figures.

---

## Color

Not re-decided — the palette is locked by `DESIGN.md`'s central law: **colour is a verdict,
never a decoration.** No new hue is introduced anywhere in this phase.

| Role | Token | Usage in Phase 18 |
|------|-------|--------------------|
| Ground (dominant) | `--bg` `#0E1012` | Page background (unchanged) |
| Secondary surface | `--surface` `#15181B` / `--surface-2` `#1E2327` | `.zone` panels for the capability rows and blast-radius block; `--surface-2` for disabled/locked field backgrounds |
| "Accent" (LIVE = brightness, not a hue) | `--live` `#E7E5E1` / `--live-hot` `#FFFFFF` / `--live-dim` | Reserved for: the "Approve deploy" primary button, focus rings on every new input/select/checkbox, the checked state of the envelope-acknowledgement checkbox, the selected tile in the actor-mode segmented control |
| Verdict green | `--pass` `#4CC38A` / `--pass-dim` | Blast-radius signal chip when configured + observed values are within tenant-configured thresholds (BLR-02) |
| Verdict red | `--fail` `#E5484D` / `--fail-dim` (alpha `0.08`, locked) | Reserved for, and ONLY for: (1) blast-radius chip when a threshold is exceeded, (2) the "No ceiling configured" chip when `max_amount_cents` is null across all enabled skills, (3) the envelope-drift chip ("Changed since approval"), (4) inline server-rejection copy under a field if a tighten-only bypass is rejected by the API, (5) `chip-seal` inherited unchanged for the room-wide gate |
| Destructive | none new | No destructive actions exist in this phase's UI surface — tightening a limit is not a destructive action and must never use `Btn variant="seal"` or `.chip-seal`. `Btn variant="seal"` stays reserved for its existing uses elsewhere in the console. |

Accent reserved for (explicit, no "all interactive elements" catch-all): the Approve-deploy
button fill, focus-visible outlines, the acknowledgement checkbox's checked mark, and the
selected state of the actor-mode tile. Nothing else in this phase's surface goes bone-white.

---

## Copywriting Contract

Sentence case, plain verbs, no em/en dashes (hyphen only), no emoji, evidence over adjectives —
per `DESIGN.md` Voice section. No copy in this phase explains the tighten-only *mechanism*; it
states the *current fact* only (see §Locked Interaction Decisions D1 rationale).

| Element | Copy |
|---------|------|
| Primary CTA (unchanged, pre-existing) | "Approve deploy" / "Re-approve deploy" (`deploy/page.tsx` — not modified by this phase) |
| Envelope acknowledgement checkbox label | "I've reviewed these limits and approve deploying with them" |
| Envelope-drift blocking copy (replaces the ability to approve) | Heading: "Capability limits changed since this checklist ran." Body: "Re-run the checklist to review and acknowledge the new configuration before deploying." |
| Blast-radius: no configured ceiling | Chip: "No ceiling" (`verdict="fail"`) next to the figure; no separate body copy needed, the chip is the claim |
| Blast-radius: no observed transactions yet | "Not tracked yet — no transactions in the last 7 days." (`--ink-3`, never a bare "R0" or "0") |
| Tighten-only field, loosening physically unavailable | Inline caption under the disabled option/control, e.g. "Already your tightest available limit." / "Verification is on — it cannot be turned off from here." / "Always-on is already required — nothing stricter exists." (control-specific, never a generic "loosening is disabled" legend) |
| Tighten-only field, server-side rejection (bypass fallback) | "That would loosen an existing limit. Limits can only be tightened once configured." (`--fail`, inline under the field, not a toast) |
| Capability row saving state (mirrors existing Appearance-section pattern) | "saving…" → "saved" (`.mono .stamp`, no button — auto-save on change) |
| Empty state: agent has zero capability envelopes seeded (defensive case, see §UI Considerations) | Heading: "No capabilities configured yet." Body: "This agent cannot take any action on a customer's behalf until you enable a skill below." |
| Destructive confirmation | Not applicable — this phase introduces no destructive action |

---

## Locked Interaction Decisions

> Running under `--auto`. Every decision below was made by this researcher (no CONTEXT.md, no
> live user to ask) and is locked with its rationale and the rejected alternative. The planner
> and executor treat these as contract, not suggestion.

### D1 — Tighten-only affordance: the control physically cannot express a looser value

**Decision:** Every capability field in CAP-03 is built so the looser direction is **not a
reachable input state**, not a value that gets validated and rejected after the fact. This is
Option 1 of the three named in the task brief, and it is the one `18-VALIDATION.md`'s
Manual-Only table already asserts ("the UI *presents* loosening as unavailable rather than
erroring after submit").

**Rejected alternatives:**
- *Refuse at input time with an inline reason after the value is entered* — rejected because it
  still lets the owner type/select a looser value and only tells them "no" after the fact; that
  is a worse version of the same mistake CAP-03 exists to prevent (a human momentarily believing
  they set a looser limit).
- *Submit and surface a server error* — rejected outright; `18-VALIDATION.md` names this
  explicitly as the failure mode NOT to build, and it would make the manual verification step
  fail.

**How this resolves per field shape** (see D2 for the ordinal/enum case specifically):

| Field | Physical mechanism |
|---|---|
| `enabled` (bool) | A toggle. If current is `false` and the platform default for that skill is `enabled: false`, the toggle's "on" position does not respond to interaction — it renders in `--surface-2`/`--ink-3` (the existing `.btn[disabled]` visual language, applied to a toggle) with the inline caption "Cannot re-enable — the platform default is off for this skill." If current is `false` and platform default is `enabled: true`, the toggle is fully interactive both ways (re-enabling to the platform default is not a loosen). If current is `true`, "off" is always reachable (turning off is always tighter). |
| `rate_limit` (`"N/unit"`) | A number stepper plus a unit `<select>`. The stepper's `max` attribute is set to the current `N` for the currently-selected unit. The unit `<select>` only lists units at or narrower than the current window (e.g. current `"10/day"` offers `hour`/`day`, never `week`/`month` — those options are absent from the list, not present-and-disabled). Switching to a narrower unit resets `max` to a freshly computed ceiling for that unit (derived from the current rate expressed per-second) so a unit switch can never smuggle in a looser effective rate. |
| `constraints.max_amount_cents` | A currency input with `max` set to the current cents value. Once any `max_amount_cents` has ever been configured (current value is non-null), the "no limit" state is not offered again — there is no checkbox or control that can clear the field back to null. A brand-new envelope with no ceiling yet configured is the only state that can start at null. |
| `requires_confirmation` / `requires_identity_verification` | A one-way toggle. Once `true`, the control stops being an interactive switch and renders as a locked, non-interactive `Chip verdict="live"` reading "On" with the caption "Verification is on — it cannot be turned off from here." Turning it on is always reachable while `false`. |
| `actor_mode` | See D2. |

**Server-side backstop (defense in depth, not the primary UX path):** `capability_service.py`'s
`validate_tighten_only()` still rejects a direct API bypass identically (per `18-RESEARCH.md`
Open Decision 3). If that path is ever hit (devtools tampering, a race with another admin
session), the PATCH mutation surfaces the inline `--fail` copy from the Copywriting Contract
under the field it targeted — never a toast, never a page-level alert, so the error stays
attached to the control the owner was looking at.

### D2 — Actor-mode ordering, made legible without a legend

**Decision:** `actor_mode` (`off` / `sample_at_rate_N` / `always-on`) is rendered as a
three-position segmented control built from the existing `AppearanceTile` pattern (`Zone
as="label"` wrapping a hidden radio, `data-live` marking the selected tile) — reused verbatim
from `deploy/page.tsx`'s Appearance section, laid out left-to-right in **fixed tightness
order**: Off → Sampled → Always-on. Positions to the LEFT of the current selection (i.e.
looser than current) render recessed (`--ink-3`, no hover state, `cursor: not-allowed`, no
`data-live`) — they are visually present so the ordering itself is legible, but not
interactive. Positions at or to the right of current are fully interactive.

For the `sample_at_rate_N` tile specifically, selecting it reveals a nested stepper for `N`
whose `min` is the current `N` (higher sampling rate = tighter, per `18-RESEARCH.md`'s
comparator table) — same physical-ceiling mechanism as D1's rate-limit stepper, mirrored.

**Legibility without a legend:** instead of a generic key explaining "left is looser, right is
tighter," each control carries exactly one control-specific status line beneath it stating the
CURRENT fact, e.g. "Currently: Always-on. Nothing stricter exists for this skill." or
"Currently: Off. Turning this on adds Actor review before every call." This satisfies the task
brief's requirement to make tighter legible for a numeric field, a boolean field, and an enum
field "without a legend explaining the metaphor" — the metaphor (left-to-right = looser-to-
tighter) is taught by the fixed spatial order and the recessed-vs-live visual treatment, and
the copy only ever states the present fact, never the rule.

**Rejected alternative:** a numeric "strictness score" badge (e.g. "Strictness: 2/3") — rejected
because it introduces a synthetic unit of measure nowhere else in the product and does not by
itself explain direction; it would need its own legend to be meaningful, which is exactly what
this decision avoids.

**Additional constraint carried from research:** `off` is only ever offered as a tile for
`mutating: false` skills (per PRD §4.5). For a `mutating: true` skill, the `off` tile is not
rendered at all — physically absent, matching D1's "cannot express" philosophy rather than
present-and-disabled, because for a mutating skill "off" is not a valid state at any tightness
level, not merely a state the owner isn't allowed to reach right now.

### D3 — A configured limit and an observed maximum are never the same number

**Decision:** BLR-01's two collector outputs — `configured_max_single_action_cents` /
`observed_max_single_action_cents` and their hourly-aggregate counterparts — are always
rendered as two visually distinct lines inside the same `Ledger` row, never merged into one
figure and never with the observed value implicitly qualifying the configured one (or vice
versa). Layout inside the "Blast radius" `Ledger` (a 5th row added to the existing four-signal
table in `deploy/page.tsx`, OR a dedicated `Zone` immediately below it — planner's structural
choice; the two-line-per-figure rule applies either way):

```
Max single action     R5,000.00 ceiling         Chip: within limits (pass) / exceeds (fail)
                       R1,200.00 observed · 7d
Max hourly aggregate   R20,000.00 ceiling
                       Not tracked yet · 7d      (ink-3, no chip — see D4)
```

The word "ceiling" and "observed" (plus the explicit `· 7d` window annotation on every observed
figure) are load-bearing microcopy — never drop them in favor of a bare number, because a bare
number is exactly the conflation this decision exists to prevent (the risk-picture difference
between "R500 configured, never hit" and "R500 configured, hit six months ago on a since-
tightened agent" is invisible without both numbers and their labels present at once).

**Rejected alternative:** a single "current exposure" figure computed as
`min(configured, observed)` or `max(configured, observed)` — rejected because both directions
lie by omission: `min()` hides that the owner authorized a higher ceiling than has ever been
used (understates configured risk), `max()` hides that the actual ceiling the owner is
attesting to right now is lower than a stale historical spike (overstates current exposure).
The task brief itself names this exact failure mode ("presenting them as one number would be a
lie to the owner at exactly the moment they are accepting financial risk").

### D4 — Honest empty and honest zero states (house pattern, extended)

**Decision:** Follows the `not_tracked` sentinel convention Phase 21 established in
`metrics_service.py` (`NOT_TRACKED = "not_tracked"`, returned instead of a fabricated `0`/`0.0`/
`null` whenever the underlying row count is zero). This phase extends the same house pattern to
two new places:

1. **Observed blast-radius figures with zero qualifying `tool_calls_audit` rows** render "Not
   tracked yet · 7d" in `--ink-3`, no `Chip`, no currency symbol, no "R0.00" — a rendered zero
   would read as a measurement ("this agent's largest transaction was nothing"), which is a
   false claim when the true state is "we have never observed one."
2. **No configured ceiling** (`configured_max_single_action_cents` is `null` because no enabled
   skill sets `constraints.max_amount_cents`) renders "No ceiling" with `Chip verdict="fail"` —
   this is the one case in this phase where an absence legitimately IS a verdict (an
   unconfigured ceiling is real exposure, not a benign gap), so unlike the observed-value case,
   this DOES use the verdict chip grammar rather than quiet `--ink-3` text.

**Rejected alternative:** treating both "never observed" and "no ceiling configured" the same
way (either both quiet-grey or both red-chip) — rejected because they are different claims: one
is "we don't have data" (epistemic honesty, D4.1), the other is "the configuration itself is
exposed" (a verdict, D4.2). Collapsing them would either mute a genuine risk signal or dramatize
an absence of data into a false alarm.

### D5 — The envelope-hash acknowledgement means something, not a digest

**Decision:** The 64-character `envelope_hash` string is never presented to the owner as the
primary object of attestation. Instead, the acknowledgement UI (a new `Zone` inserted between
the four/five-signal `Ledger` and the existing `verdict-bar` in `deploy/page.tsx`) renders:

1. A compact per-skill summary `Ledger` — one row per enabled skill, columns: skill name, rate
   limit, max amount, "Confirmation required" / "Verification required" as plain `.help`-weight
   label text where true (empty cell where false — see D6 for why this is text, not a `Chip`),
   actor mode. This is literally the human-legible deserialization of the exact fields BLR-02's
   canonical hash is computed over (`18-RESEARCH.md` Open Decision 2's field list) — the owner
   attests to THIS table, not to a hex string.
2. Beneath the summary, one mono caption line: "config `{hash[:8]}…{hash[-4:]}`" in `--ink-3` —
   present as an integrity fingerprint an engineer or support agent could cross-reference later
   (mirrors the short-commit-hash convention already used across this console's mono stamps),
   never as something the owner is asked to read or verify themselves.
3. The acknowledgement checkbox (Copywriting Contract row above) sits directly under the
   summary table, not floating elsewhere on the page — the visual binding between "the table you
   just read" and "the checkbox you're about to tick" is the whole point.

**Drift state (what happens once the acknowledged hash goes stale):** When
`checklist_runs.envelope_hash` (the hash the last approved run acknowledged) no longer matches
the live envelope hash, the acknowledgement `Zone` re-renders in its drift state: the per-skill
summary table is replaced with the drift copy (Copywriting Contract row), a `Chip verdict="fail"`
reading "Changed since approval" appears in the section head next to "Capability envelope," and
the "Approve deploy" button becomes disabled with the same inline-reason pattern as D1 (a short
caption under the button: "Re-run the checklist to acknowledge the new configuration.") rather
than being left clickable to hit the server's 422. The 422 (`18-VALIDATION.md` T-18-BLR-03)
remains the enforced backstop — this UI decision is about the primary path only, exactly like
D1's server-side backstop framing.

**This drift state deliberately does NOT set `data-gate="blocked"`** (the room-wide shutter
`GateProvider` drives from `checklistBlocked || redTeamBlockedSignal` in `deploy/page.tsx`).

**Rejected alternative:** wiring envelope drift into the same `useGate()` call as the existing
red-team/checklist block signals, so a stale acknowledgement repaints the whole console red like
an open critical finding. Rejected because `data-gate` is a room-wide claim reserved for "an
open blocking finding exists right now" (per `DESIGN.md`: "the gate is not a badge, it is the
room"); envelope drift is a narrower, calmer claim — "your last approval no longer describes the
live configuration, re-verify before shipping" — and is resolved by re-running the checklist, not
by fixing a defect. Repainting the entire console for it would train operators to associate the
full shutter with the wrong severity class over time, diluting the signal `data-gate` exists to
protect (per `18-RESEARCH.md`'s own Anti-Pattern: "Flipping `agent.is_deployed = False`
automatically on envelope drift... use the advisory drift flag... instead" — the same restraint
applies to the room-wide shutter, not just the deployed flag).

### D6 — No `Chip` for presence labels in the acknowledgement summary table

**Decision:** In D5's per-skill summary table, "Confirmation required" and "Verification
required" render as plain label text (`.help`-weight, `--ink-2`, present only when true, the
cell left empty when false) — never as `Chip verdict="mute"`. This table is the object of a
financial-risk attestation (BLR-02: the owner is signing off on a configuration before money
can move). In this design system a `Chip` carries verdict weight by construction (`ChipVerdict`
is a closed union — `live`/`pass`/`fail`/`seal`/`mute` — enforced by construction in
`Chip.tsx`); spending chips on presence labels on THIS specific surface dilutes exactly the
signal the owner most needs to read correctly at the moment they accept risk. `DESIGN.md`'s
own anti-pattern list names this precisely: "a chip used as a category tag rather than a
verdict."

**Rejected alternative:** keep `Chip verdict="mute"` for these two fields, for consistency with
`apps/admin/app/ingest/page.tsx`, which already uses `Chip verdict="mute"` as a category tag for
`doc.source_type` values ("PDF"/"URL"). Rejected — consistency with an existing anti-pattern is
not a reason to extend it, and the ingest page's document-type badges are not attached to a
financial-risk attestation, so the two surfaces do not carry the same stakes even though they
share a visual precedent. This decision does not overturn or schedule a fix for the `ingest`
usage — that is a pre-existing inconsistency, out of scope for Phase 18, and no change to
`ingest/page.tsx` is implied or planned here.

---

## Structural placement (non-visual, for the planner/executor)

- **File to extend:** `apps/admin/app/agents/[id]/deploy/page.tsx`. This is an addition to a
  working, already-verified screen — not a new route, not a new page shell.
- **New section 1 — "Capabilities and limits" (CAP-03/CAP-04):** a new `<section
  className="section">` following the existing pattern (`.section-head` with `h2.label` +
  optional mono stamp), placed after the existing "Appearance" section (or before it — planner's
  call; it does not gate the existing flow). Contains one `Zone` per transactional skill
  (six total), each `Zone` holding the field grammar from D1/D2.
- **New section 2 — Blast radius + envelope acknowledgement (BLR-01/02):** inserted inside the
  existing "The gate" section, either as a 5th `Ledger` row alongside the existing four signals
  or as its own sub-block immediately following that `Ledger` — see D3 for the two-line-per-
  figure rule that applies regardless of exact placement. The acknowledgement `Zone` (D5) sits
  between that block and the existing `verdict-bar`, and its checked/drift state is a hard
  precondition folded into the existing `isApprovable` boolean alongside the current
  `recommendation`/`all_warnings_acknowledged` checks.
- **Do not touch:** the widget preview column, the Embed section, the existing four-signal
  Ledger's content, or `GateProvider`'s existing `gateBlocked` derivation (D5's rejected
  alternative).
- **Visual hierarchy is state-dependent, not fixed.** The existing gate Chip ("Shut"/"Open")
  and the existing four-signal `Ledger` remain the page's overall primary anchor, unchanged, in
  both states below. Among the NEW content this phase adds, the render-priority order differs
  by whether the envelope has drifted:
  - **Clean run (no drift):** (1) the blast-radius ceiling/observed figures (D3) — risk data the
    owner must read before acknowledging anything, (2) the acknowledgement summary table +
    checkbox (D5), (3) the six per-skill capability `Zone`s (D1/D2) — a configuration task the
    owner returns to occasionally, not something re-read on every deploy, so it ranks last among
    the new elements.
  - **Drifted run:** the envelope-drift `Chip verdict="fail"` ("Changed since approval") out-
    ranks every other new element, including the blast-radius figures. Render a second instance
    of that chip directly beside the existing gate Chip in the `#gate-label` section head — not
    only inside its own lower acknowledgement `Zone` — so an owner returning to a
    previously-approved agent sees "drifted" before anything else new on the page. The
    blast-radius figures and per-skill `Zone`s stay reachable below it but do not compete for
    first-glance attention while a drift exists.

---

## UI Considerations

Applicable state considerations resolved: 8 covered, 2 backstop, 0 unresolved.

| Category | Element(s) | Status | Resolution / Reason |
|----------|------------|--------|---------------------|
| empty | Observed blast-radius figures, zero `tool_calls_audit` rows | ✅ covered | Renders "Not tracked yet · 7d" in `--ink-3`, never a bare "R0" (D4.1) |
| empty | No configured ceiling across enabled skills | ✅ covered | Renders `Chip verdict="fail"` "No ceiling" — an absence that is itself a verdict (D4.2) |
| empty | Zero capability-envelope rows for a brand-new agent | ✅ covered | `EmptyState` component (heading/body in Copywriting Contract) if the six skill rows are genuinely absent server-side; planner must confirm at plan time whether rows are always seeded at provisioning (research assumption) or truly absent — either way this state is specified, not invented at execution time |
| loading | Capability panel GET in flight | ✅ covered | Same `isPending` mono-stamp pattern already used for `saveWidgetConfig` ("saving…"/"saved") — no skeleton, no spinner beyond the existing gate spinner convention |
| error | Tighten-only server-side rejection (bypass fallback) | ✅ covered | Inline `--fail` copy under the specific field (D1 backstop), never a toast/alert |
| error | Envelope-drift blocks approval | ✅ covered | Drift `Zone` state + disabled Approve button with inline reason (D5) |
| partial | Some skills enabled, some not, mixed actor modes | ✅ covered | Each skill `Zone` is independent; no cross-skill layout assumption |
| overflow | Six skill `Zone`s on narrow viewport (900px, no horizontal scroll allowed) | ✅ covered | Stack vertically (single column) below 900px, matching the existing `.tiles`/`.agents` grid collapse convention already in `globals.css` (`@media (max-width: 700px) { .tiles { grid-template-columns: 1fr } }`) — no new breakpoint invented |
| long-text | Skill constraint values (currency, rate strings) | 🧪 backstop | Currency/rate values are always short mono tokens by construction (typed Pydantic ints/enums, never free text) — held-out visual check that no field wraps awkwardly at 1280/900px, verification: backstop |
| zero-one-many | Number of enabled skills acknowledged in the D5 summary table | 🧪 backstop | Summary table must render correctly with 1 enabled skill and with all 6 — held-out visual check, verification: backstop |

---

## Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|--------------|
| shadcn official | none — shadcn is not used in this project | not applicable |
| third-party | none | not applicable |

No registry vetting gate was triggered — this phase introduces zero new UI dependencies of any
kind (no shadcn, no npm component package). Every control is composed from primitives that
already shipped and passed `check:no-dusk-tokens` in Phase 20.

---

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: PASS
- [ ] Dimension 2 Visuals: PASS
- [ ] Dimension 3 Color: PASS
- [ ] Dimension 4 Typography: PASS
- [ ] Dimension 5 Spacing: PASS
- [ ] Dimension 6 Registry Safety: PASS

**Approval:** pending
