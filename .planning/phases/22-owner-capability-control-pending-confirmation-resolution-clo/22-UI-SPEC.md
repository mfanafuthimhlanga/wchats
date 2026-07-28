---
phase: 22
slug: owner-capability-control-pending-confirmation-resolution-close-ver-01-s-two-structural-blockers
status: draft
shadcn_initialized: false
preset: none
created: 2026-07-28
---

# Phase 22 — UI Design Contract

> Visual and interaction contract for CAP-05 (unlock the `enabled` control) and ACT-07 (the
> approver's pending-confirmation queue). Both surfaces live on the existing Deploy page
> (`apps/admin/app/agents/[id]/deploy/page.tsx`). This phase adds **no new design system** — every
> token, component, and interaction pattern below is a reuse of what GOTHAM "Bone on Graphite"
> already ships. `DESIGN.md` and `apps/admin/app/globals.css` remain the source of truth; nothing
> here overrides them.

---

## Design System

| Property | Value |
|----------|-------|
| Tool | none — GOTHAM is a hand-built token system in `globals.css`, not shadcn. No `components.json` exists in `apps/admin`, confirmed this session. |
| Preset | not applicable |
| Component library | Gotham component set: `Zone`, `Chip`, `Ledger`/`LedgerColHead`/`LedgerRowHead`, `Btn`, `EmptyState` (`apps/admin/app/components/gotham/`) — reused verbatim, zero new primitives |
| Icon library | none new — this phase adds no icon |
| Font | `--display` (Space Grotesk 500, headings), `--sans` (Inter, body/controls, 14px/1.55), `--mono` (JetBrains Mono, every number/id/timestamp), `--voice` (Newsreader italic, reserved for judge/verdict prose — **not used by this phase**, since neither surface is a machine-reasoning verdict) |

---

## Surface 1 — CAP-05: the enable control

### The decision the research left open, resolved

**Locked decision: `enabled: False → True` gets staged-confirm treatment (mirroring `rate_limit`
/ `max_amount_cents`'s `requestX` / `pendingX` / `confirmX` pattern) *only when the agent is
already live* (`agent.is_deployed === true`). For an agent that has never been approved, the
checkbox stays a plain, immediate, unstaged write — identical in shape to `requires_confirmation`
/ `requires_identity_verification` today.**

**Reasoning (grounded in code read this session, not in the research's "highest consequence flip"
framing, which this spec found underspecified):**

The research posed the question as "is this the single highest-consequence flip on the panel."
That framing does not resolve to an answer by itself — `requires_confirmation` and
`requires_identity_verification` are also high-consequence booleans on the same panel and neither
stages. The question that actually resolves it is: **does this write take effect against a real
customer the instant it is saved, with no further owner action in between?**

Read directly from `deploy/page.tsx` this session: `envelope_drift` / `envelope_acknowledged_at`
gate only `POST /approve-deployment` — they are re-checked when the owner next clicks Approve, not
continuously. `capability_service.check_capability_access` (per `22-RESEARCH.md` § ACT-07 Design,
step 2) reads the **live** `capability_envelopes` row on every tool dispatch, for every turn,
independent of whether a checklist has ever re-run. **For an agent that is already deployed
(`agent.is_deployed === true`), a bare `enabled: true` PATCH is live in production on the agent's
very next customer turn — nothing else in this codebase interposes another gate.** That is not true
of any other boolean on this panel:

- `requires_confirmation` / `requires_identity_verification` (`False → True`, the only direction
  either allows): strictly *adds* a safety gate. A mistaken tick cannot be exploited — it can only
  make an action harder to complete, never easier.
- `rate_limit` / `max_amount_cents` loosening: already blocked outright (tighten-only). Their
  staged-confirm exists to catch a **typo in a typed numeric value** (`600` vs `60`), a risk class
  that does not exist for a checkbox — there is nothing to mistype, and the box already previews its
  own destination state the instant it is clicked.
- `enabled: False → True`: the one field on this panel whose flip *removes* a fail-closed default
  and is live-effective immediately, with no numeric typo risk to catch and no downstream gate to
  catch a mistake before it reaches a customer — **unless the agent has never been approved yet**,
  in which case nothing is reachable by a customer regardless of what this checkbox does, and the
  full checklist + envelope-acknowledgement + Approve gate still stands fully between this change
  and any live effect.

This is why the fix is conditional on `agent.is_deployed`, not a blanket rule. Staging the box on a
pre-first-deploy agent would add friction that catches nothing (nothing is live yet); *not* staging
it on an already-live agent would silently skip the one moment this codebase has to let an owner
say "yes, right now, for real" before a live agent's blast radius changes.

**What would change this recommendation:** evidence that `capability_envelopes` PATCHes do NOT take
immediate dispatch-time effect on a deployed agent (i.e., if a future change made runtime
enforcement read a deploy-time snapshot instead of the live row) — that would remove the asymmetry
this decision rests on and the checkbox should drop back to unstaged, matching the other two
booleans. Also: if telemetry after ship shows owners routinely landing in the staged-confirm state
by accident and finding it more confusing than protective — but note that outcome could only be
observed with real usage, and misclicking a *checkbox* (a binary, self-previewing control) is a
categorically rarer failure mode than mistyping a number, so this is not expected to be revisited
absent that evidence.

### Behavior contract

| State | `agent.is_deployed` | Click | Result |
|---|---|---|---|
| Off → On | `false` (or agent record not yet loaded) | tick the box | Immediate `onSave(skill, { enabled: true })` — no staging, matches `requires_confirmation` today |
| Off → On | `true` | tick the box | Box visually previews checked; a `cap-confirm` block mounts (see copy below); nothing is written until the owner confirms |
| On → Off | either | untick the box | Immediate `onSave(skill, { enabled: false })` — **unchanged by this phase**, this direction is already unconditional and unstaged today and stays that way (disabling is always safe) |

The checkbox's staged state does **not** need an intermediate "click a button to stage" step the
way the numeric fields do (`cap-commit` → `Set rate limit` ghost button). A checkbox click is
already the discrete, deliberate action — the numeric fields need that extra step because typing is
continuous and "dirty" is not the same event as "decided." Go straight from click to the
`cap-confirm` block, matching how this file already treats the rate-unit `<select>`'s `onChange` as
a discrete action needing no separate stage button.

### Exact copy (replaces `deploy/page.tsx:1147-1151`)

| State | Copy |
|---|---|
| Off, resting | `Off. Turn this on to let the agent use this skill.` |
| On, resting (any path) | `On. The agent can use this skill.` |
| Staged confirm question (only reachable when `is_deployed === true`) | `Let this live agent use {skillLabel} now? Customers can trigger it on their next turn.` |
| Staged confirm primary button (`autoFocus`, mirrors `Set {n} per {unit}`) | `Turn on {skillLabel}` |
| Staged confirm secondary button (mirrors `Keep {n} per {unit}` / `Cancel`) | `Keep it off` |

**Never reintroduce "platform default" / "cannot re-enable" language in any of these strings** — the
platform-default gate this phase removes is the exact defect being fixed; the copy must not imply
it still exists in either direction.

Field-level PATCH errors (`fieldErrors['{skill}.enabled']`) keep the exact existing mechanism
unchanged — inline `cap-error` under the row, verbatim backend `detail` string. No new translation
layer is needed here: CAP-05's fix removes the one denial mode (`loosen_enabled`) this field used to
have; any error surfacing after this phase ships is a generic HTTP/validation failure already
handled correctly by the shipped pattern.

### Cross-cutting dependency (not a UI-SPEC deliverable, but must not be dropped)

`docs/guides/owner-capability-guide.md` currently narrates the OLD locked-checkbox behavior
verbatim and is the ONLY document the non-technical tester is handed for VER-01 SC2's manual
verification (`22-VALIDATION.md` § Manual-Only Verifications, row 1). The guide's rewrite must use
these exact copy strings, not a paraphrase — a guide that says something plausible-but-different
from what the UI actually renders would itself fail the "non-technical, unaided" bar this phase
exists to clear.

---

## Surface 2 — ACT-07: the approver's confirmation queue

### Placement — confirmed, not overturned

**Confirmed: the Deploy page, as a new `<section className="section">` placed immediately after
"Capabilities and limits" and before `<WidgetPreview>`** (i.e. the last section in the left bench
column of `deploy/page.tsx`, right after the `mutatingCapabilityEnvelopes.map` block closes around
line 2164). Reasoning: the ops-room six-region layout is a fixed, parity-tested contract (UI-SPEC
S6.4) that this phase has no reason to reopen; the Deploy page already hosts the money-moving
capability panel and the blast-radius/envelope-acknowledgement blocks, and a pending confirmation is
downstream of exactly that configuration — an owner reviewing what a skill CAN do and an approver
reviewing what it's ABOUT to do belong on the same page, in that order (configure, then resolve
what the configuration produced).

Section heading: `Pending confirmations` (mirrors the `Capabilities and limits` `h2.label`
pattern — no subtitle, matching that section's own unadorned heading).

### Data model gap found — flag for the planner (contradicts the research's implicit scope)

The research's UI Considerations bullet says the queue must "surface *why* an approval failed"
using the backend's specific denial reason (e.g. `capability.denial:max_amount_cents`) — but this
conflates two different things that the research's own route design (`GET` list + `POST .../resolve`
returning only `pending_confirmations` columns: `resolution`, `resolved_at`) does not actually
distinguish:

1. **`resolution = 'rejected'`** — an owner's own decision. There is no "reason" here at all; a
   reject is not a failure, it has no denial code.
2. **`resolution = 'approved'`, but the async re-check inside `resolve_approved_confirmation`
   subsequently denies execution** (capability disabled, ceiling tightened, rate limit hit — per
   `22-RESEARCH.md`'s step table, this is exactly SC3's re-evaluation-against-the-live-envelope
   case). This is the scenario that actually produces a `capability.denial:*` code, and it happens
   **after** the row's `resolution` is already stamped `'approved'` — the row itself carries no
   field today that would tell the UI this happened.

**This is a genuine gap between what the research scoped for the backend and what this phase's own
audience requirement demands.** Without a field distinguishing "approved and executed" from
"approved but denied at execution," the queue cannot honestly show a customer-facing owner whether
their approved refund actually happened — which is precisely the "comprehensible to a non-developer"
bar `22-VALIDATION.md`'s manual-only row exists to check.

**Classified `unresolved`** (see table below) — the planner must close this by extending the
resolve/list response with an execution-outcome field for `resolution = 'approved'` rows (e.g. a
lookup against `tool_calls_audit` keyed by `(agent_id, skill, idempotency_key)` at read time, or a
denormalized column written by the Celery task). Until that field exists, the UI contract below is
explicit about the honest, non-overclaiming fallback state to render instead of inventing a false
"success."

### Per-row data contract

Every row must show, per the task brief: skill (human label), proposed arguments in readable form,
requested-at, expires-at, and — once resolved — the resolution and when.

**Skill label:** `SKILL_LABELS[skill]` — reused verbatim from `deploy/page.tsx`, no new map.

**Readable arguments — headline + secondary details, never raw JSON.** Field names below are
read directly from `apps/api/app/services/transactional/schemas.py` this session (all six mutating
Input models), not invented:

| Skill | Headline template | Secondary details (`.help`, only if non-empty) |
|---|---|---|
| `place_order` | `Place an order for {quantity} × {product_id} — {formatCents(amount_cents)}` | `Customer: {customer_email} · Ship to {shipping_address}` |
| `cancel_order` | `Cancel order #{order_id}` | `Reason: {reason}` |
| `issue_refund` | `Refund {formatCents(refund_amount_cents)} for order #{order_id}` | `Reason: {reason}` |
| `update_subscription` | `Change subscription #{subscription_id} to the {new_plan} plan, effective {effective_date}` | — |
| `book_slot` | `Book {service_type} for {customer_name} on {preferred_date} at {preferred_time}` | — |
| `update_customer_record` | `Update {field_name, Title Cased} to "{new_value}"` | — |

Formatting rules (apply generically, so a future seventh mutating skill degrades gracefully rather
than rendering raw JSON):

- Any key ending `_cents` → `formatCents()` (existing helper, reused verbatim) — **never print raw
  cents to an owner**, per the task brief.
- Any key ending `_id` → prefixed `#` inline.
- `idempotency_key` is **never** shown anywhere in the readable render — it is a replay-protection
  token, not business-meaningful to an owner, and showing it would put a UUID in the one place this
  spec is working hardest to keep in plain business language.
- Any argument not covered by a skill's headline template renders as a generic "Details" fallback
  list below the secondary line: Title-Cased key, raw value, one per line — this is the defensive
  backstop for any skill this table does not yet name, not the primary path for the six shipped
  skills above.

**Timing — relative primary, absolute secondary (evidence over adjectives, per `DESIGN.md`):**

- `Requested {relative}` (e.g. "10 minutes ago") as the primary line, with the absolute mono
  timestamp (`formatDateTime`, reused verbatim) alongside it: `Requested 10 minutes ago ·
  2026-07-28 14:02`.
- `Expires {relative}` (e.g. "in 2 hours") / `Expired {relative}` (e.g. "2 hours ago") the same way:
  `Expires in 2 hours · 2026-07-28 16:12`.
- Once resolved: `{Resolution label} {relative}` with the same absolute pairing, e.g. `Approved 3
  minutes ago · 2026-07-28 14:05`.
- A relative-time formatter does not exist yet in this file (`formatDateTime` is absolute-only) —
  this is a small, net-new helper the plan must add. Classified `covered` below (the *behavior* is
  fully specified; only the helper function is new code, not an open design question).

### Status — verdict chips, held to the "colour is a verdict" law

This is the one place this spec found real tension with `DESIGN.md`'s hard constraint, and resolves
it conservatively:

| Row state | Chip | Label | Reasoning |
|---|---|---|---|
| Unresolved, not yet expired | `mute` | `Pending` | Not yet a verdict — nothing has held or failed |
| Unresolved, `expires_at` has passed (client-side check; server enforces lazily at resolve time) | `mute` | `Expired` | Neutral outcome, not a judgement; Approve/Reject go `aria-disabled` with caption `This request expired before anyone acted on it. It cannot execute.` |
| Resolved, `resolution = 'rejected'` | `mute` | `Rejected` | An owner's decision, not the system asserting pass/fail |
| Resolved, `resolution = 'expired'` | `mute` | `Expired` | Same as above |
| Resolved, `resolution = 'approved'`, **execution outcome unknown** (current data model, per the gap above) | `mute` | `Approved` with `.help` text `Awaiting execution.` | **Must NOT render `pass` here.** `resolution = 'approved'` is not proof of execution — asserting `pass` (green) on a row whose actual outcome is unknown would be exactly the false verdict `DESIGN.md` forbids ("a colour on this bench is a claim about whether an agent can be trusted with a customer") |
| Resolved, `resolution = 'approved'`, execution **succeeded** (once the data-model gap is closed) | `pass` | `Executed` | It held |
| Resolved, `resolution = 'approved'`, execution **denied** (once the data-model gap is closed) | `fail` | `Not executed` | The gate stopped it — use `fail`, not `seal`: `seal` is `DESIGN.md`'s reserved token for the whole-room deployment-blocking gate (`data-gate="blocked"`), a different concept from a single capability re-check denial. **This queue must never write `data-gate`.** |

### Denial-reason translation (once the data-model gap is closed)

Per the task brief: never a generic "failed" toast. Translate the backend's own code, shown under
the `fail` chip as `.help` text, always keeping the raw code visible as evidence even in the
fallback case (mirrors `DESIGN.md`'s "evidence over adjectives: show the number"):

| Backend code (per `22-RESEARCH.md`'s step table) | Owner-facing copy |
|---|---|
| `capability.denial:enabled` (or equivalent "disabled") | `This skill was turned off after the request was made, so it could not run.` |
| `capability.denial:max_amount_cents` | `The maximum amount allowed for this skill was lowered after the request was made. This amount is now over that limit.` |
| `capability.denial:rate_limit` | `This skill had already reached its rate limit when this request tried to run.` |
| any other / unrecognised code | `This could not run. ({raw backend code})` — never a bare "Failed." with no reason |

### Approve / Reject — staged-confirm, mirroring the numeric-field pattern

**Locked decision: both Approve and Reject stage through a `cap-confirm`-shaped sub-block** before
writing anything — this is the single most consequential click this phase adds (it moves real
money, immediately, per the CAP-05 analysis above), arguably higher-stakes than the numeric
ceiling edits that already earn this treatment. Reject stages too, for symmetry and because a
mis-click on the wrong row denies a legitimate customer action with no automatic undo.

| Action | Confirm question | Sub-line | Primary button (`autoFocus`) | Secondary button |
|---|---|---|---|---|
| Approve | `Approve: {headline}?` | `This executes immediately once approved.` | `Yes, approve` | `Cancel` |
| Reject | `Reject: {headline}?` | `The agent will not be able to complete this action for the customer.` | `Yes, reject` | `Cancel` |

In-flight labels (reuses the exact `Approving…` shape already established for
`approveDeployment.isPending`): `Approving…` / `Rejecting…`.

**Must prevent double-submit (per task brief):** reuse the page's established
`aria-disabled`-during-save convention **per row**, not globally — only the row whose request is
in flight goes `aria-disabled`; the other rows in the queue stay fully interactive. This matches
`isSaving`/`savingSkills` being keyed per-skill in the capability panel, not a single shared flag.

**Concurrency (race with a second click, or a second browser tab):** the backend's atomic claim
(per `22-RESEARCH.md` § ACT-07 Design) returns 409 on an already-resolved row. On a 409, do **not**
show an error toast or treat it as a failure — refetch the row (invalidate the query) and show a
transient inline note: `Someone already resolved this request.` This is a benign, expected outcome
of the atomic-claim design, not a defect, and must not read as one.

### Accessibility — row naming (the defect class this page has already fixed once)

Each row is a real `<li>` inside a `<ul className="pcq-list">` (an `<li>` already carries an
implicit accessibility role, unlike the bare `<div>` this codebase's own comments describe having
fixed for the six capability Zones — no `as="section"` workaround is needed here, but the naming
principle is identical). The row's headline paragraph is dual-purpose — it is both the visible copy
and the `aria-labelledby` target, exactly mirroring `cap-${skill}-label`:

```
<li aria-labelledby={`pending-${row.id}-label`}>
  <p id={`pending-${row.id}-label`} className="pcq-headline">{headline}</p>
  ...
</li>
```

Because two rows can legitimately share the same skill (two different refunds pending at once), the
row's accessible name must never be the skill alone. Approve/Reject buttons get their own
`aria-label` built from the same headline string the confirm question uses (`Approve: {headline}`
/ `Reject: {headline}`), so a screen reader reading six "Approve, button"s in a row hears six
different, fully distinguishing names — the exact regression this page's own code comments record
having fixed once already for the capability panel.

**Contrast pairs reused (no new pairing introduced):** `Chip verdict="mute"` (`--ink-2` on
`--surface-2`) and `Chip verdict="fail"` (`--fail` on `--fail-dim`, documented in `globals.css` at
4.58:1, WCAG AA) and `Chip verdict="pass"` (`--pass` on `--pass-dim`) are all pre-existing, already
shipped on this same page, and already covered by the project's axe parity suite (Phase 20). This
spec introduces no new foreground/background combination — if the executor introduces one anyway
(it should not need to), it must clear WCAG AA (4.5:1 text) and be verified via the existing axe
gate before shipping.

### Ordering

**Default view:** unresolved rows sorted `expires_at` ascending (most urgent first — this is a
triage queue, not a history log), followed by resolved rows from the last 24 hours sorted
`resolved_at` descending (so an owner sees the outcome of what they just acted on without leaving
the page). Rows older than 24h and already resolved drop off the default view — this is a queue,
not an audit trail; a full history view is out of scope for this phase (research's Open Question 2,
now closed this way). The exact `GET` query-param shape (`?status=unresolved` plus a resolved-since
window, vs. a single `?status=all&limit=N` filtered client-side) is the planner's implementation
choice — the **behavior** contract above is what's locked.

### Empty state

Reuse `EmptyState` verbatim, matching the voice already established for "nothing to report" states
on this same page (e.g. the blast-radius block's "No transactional skill is enabled..."):

```
heading: "No confirmations are waiting."
body:    "When a mutating action needs your approval, it will appear here with what it wants
          to do, when it was requested, and when it expires."
```

---

## Spacing Scale

This project's shipped design system (GOTHAM) does not use a 4pt grid — its actual values
(`.zone` padding 20px, `.field` margin-bottom 18px, `.section` padding-top 26px / margin-top 30px,
`.cap-commit`/`.cap-confirm` gaps 10px, `.page` padding 34px/40px) are fixed by existing CSS classes
this phase reuses unmodified. **No new spacing value is introduced by either surface** — the queue's
new markup (`pcq-list`, `pcq-row`, `pcq-headline`) should be styled with the same `.field`/`.cap-row`
-family spacing already in `PAGE_CSS`, not a new scale.

Exceptions: none — this phase adds zero new spacing tokens.

---

## Typography

Reused exactly as shipped, no new size/weight introduced:

| Role | Size | Weight | Line Height | Source |
|------|------|--------|-------------|--------|
| Body / row copy | 13.5–14px | 400 (Inter) | 1.5–1.55 | matches `.ledger td` / `.help` / body |
| Label (row heading, section heading) | 10px mono uppercase (section labels) or 16px `--display` (skill headline) | 700 (mono label) / 500 (`--display`) | 1.2–1.25 | matches `.label` / `.card-name` |
| Secondary/help text | 12.5–13px | 400 | 1.55 | matches `.help` |
| Numbers, ids, timestamps | mono, tabular | 400–700 | 1.55–1.75 | matches `.num`/`.mono` — every cents figure, every `#id`, every timestamp in the queue must be mono, per `DESIGN.md`: "Numbers are always mono" |

`--voice` (Newsreader italic) is explicitly **not used** anywhere in this phase — it is reserved for
judge/verdict prose (Gatekeeper/Auditor/Strategist), and neither surface here is a machine
verdict.

---

## Color

| Role | Value | Usage |
|------|-------|-------|
| Dominant (bench, ~60%) | `--bg` `#0E1012` / `--surface` `#15181B` | page ground, Zone/row backgrounds — unchanged, no new surface tone |
| Secondary (~30%) | `--surface-2` `#1E2327` | raised elements — inputs, `chip-mute` background |
| Accent — reserved for | `--live` `#E7E5E1` / `--live-hot` `#FFFFFF` | **only**: focus rings (`:focus-visible`), the primary confirm button's autofocus state (inherited from `.btn` default styling, not a new usage), and any `data-live="true"` Zone glow — **this phase adds no new `--live` usage beyond what `Btn`/`:focus-visible` already provide by default** |
| Verdict red | `--fail` `#E5484D` on `--fail-dim` | `Chip verdict="fail"` — "Not executed" only, never decorative |
| Verdict green | `--pass` `#4CC38A` on `--pass-dim` | `Chip verdict="pass"` — "Executed" only, gated on the data-model fix above; **never applied to a merely-`approved` row without confirmed execution** |
| Neutral/no-verdict | `--ink-2` on `--surface-2` | `Chip verdict="mute"` — "Pending" / "Expired" / "Rejected" / "Approved (awaiting execution)" |

Accent reserved for: focus rings and default `Btn` styling only. **No new element on either surface
claims `--live` as a decorative accent** — the enable checkbox's staged-confirm state uses the same
neutral `cap-confirm` treatment the numeric fields already use (a hairline-bordered block, no color
claim), not a `--live` highlight, because a staged-but-unconfirmed state is not yet "live" in this
system's sense.

Destructive: this phase has no `--seal`-class destructive action. Reject is a business decision, not
a destructive/gate action, and is deliberately **not** styled with `--seal`/`btn-seal` — that token
is reserved for irreversible platform-level actions (per `Btn.tsx`'s own comment: "reserved for
actions like 'Delete permanently'"). Reject uses the same neutral `ghost` button treatment as
Approve's `Cancel` — both are business decisions of comparable weight, not one destructive and one
safe.

---

## Copywriting Contract

| Element | Copy |
|---------|------|
| CAP-05 primary action | Checkbox — no button copy; staged-confirm primary button `Turn on {skillLabel}` |
| ACT-07 primary CTA | `Yes, approve` (staged, see Surface 2) |
| Empty state heading (queue) | `No confirmations are waiting.` |
| Empty state body (queue) | `When a mutating action needs your approval, it will appear here with what it wants to do, when it was requested, and when it expires.` |
| Error state (denial, once data-model gap closed) | Translated per-code table above, e.g. `The maximum amount allowed for this skill was lowered after the request was made. This amount is now over that limit.` — never a bare "Failed." |
| Destructive confirmation | Not applicable — neither surface has a `--seal`-class destructive action; both confirmations use the neutral `cap-confirm` shape |
| CAP-05 off caption | `Off. Turn this on to let the agent use this skill.` |
| CAP-05 on caption | `On. The agent can use this skill.` |
| CAP-05 staged question | `Let this live agent use {skillLabel} now? Customers can trigger it on their next turn.` |
| ACT-07 approve question | `Approve: {headline}?` |
| ACT-07 reject question | `Reject: {headline}?` |
| ACT-07 concurrent-resolve note | `Someone already resolved this request.` |
| ACT-07 expired-row caption | `This request expired before anyone acted on it. It cannot execute.` |

All copy above must land verbatim in `docs/guides/owner-capability-guide.md`'s CAP-05 correction
(cross-cutting dependency noted under Surface 1) — the manual UAT hands the tester only that guide,
so a paraphrase there is a real risk to the phase's own pass bar, not a cosmetic nit.

---

## UI Considerations

Applicable state considerations resolved: 11 covered, 2 backstop, 2 unresolved.

| Category | Element(s) | Status | Resolution / Reason |
|----------|------------|--------|---------------------|
| empty | pending-confirmation queue | ✅ covered | Renders `EmptyState` with the exact heading/body locked above when the unresolved+recent-resolved set is empty |
| loading | pending-confirmation queue | ✅ covered | Reuse the page's existing `loadError`/query-pending pattern; add `pendingConfirmationsQuery.error` into the existing `loadError` aggregation, no new loading UI pattern |
| error (GET failure) | pending-confirmation queue | ✅ covered | Folds into the existing page-level `loadError` banner already rendered for every other query on this page — no new error surface |
| error (denial reason) | resolved-and-approved row | ⚠ unresolved | Depends on the data-model gap (execution-outcome field) flagged in Surface 2 — the COPY contract is fully specified (translation table above); the DATA to drive it does not exist in the research's scoped route design yet. Planner must close this, not silently drop it. |
| partial | resolved-and-approved row before execution-outcome exists | ✅ covered | Explicit honest fallback locked: `mute` chip, `Approved`, `.help` "Awaiting execution." — never a false `pass` |
| populated | queue with N rows | ✅ covered | `pcq-list` of staged-confirm rows, ordering locked (unresolved by urgency, then recent-resolved) |
| zero-one-many | queue rows, same skill appearing more than once | ✅ covered | Row accessible name is the full headline (order id / amount / date included), never the skill alone — two `issue_refund` rows for different orders are already distinguishable by content |
| long-text | `reason` / `new_value` / `shipping_address` free-text fields | 🧪 backstop | No length cap specified for these owner-facing strings; the row should wrap, not truncate silently (truncating a refund reason or a shipping address is a data-loss risk in a review UI). Needs a rendered check that long values wrap within the row rather than overflow the page — held-out visual verification |
| overflow | narrow viewport (900px breakpoint already established by `.cap-grid`) | 🧪 backstop | The queue should reuse the existing 900px single-column collapse already proven for `.cap-grid`; needs a rendered check at 900/1280/1440px per the project's existing three-viewport parity suite, not a new assumption |
| in-flight / double-submit | Approve / Reject buttons | ✅ covered | Per-row `aria-disabled`, reusing the established page-wide convention exactly (not global, not a new pattern) |
| concurrent-resolve (race) | Approve / Reject on an already-claimed row | ✅ covered | 409 → inline `Someone already resolved this request.` + refetch, never a toast, never treated as a failure |
| expired-but-unresolved | queue row past `expires_at` with no sweep run | ✅ covered | Client-side `expires_at` check disables Approve/Reject and shows the `Expired` mute chip ahead of any server-side sweep — degrades gracefully whether or not the optional sweep (research OD-b) ships |
| staged-confirm scope | CAP-05 enable checkbox | ✅ covered | Conditional on `agent.is_deployed` — the single most consequential design decision in this document, fully reasoned above, with a stated falsification condition |
| accent-color misuse | any control on either surface | ✅ covered | Explicit color table above enumerates every `--live`/`--pass`/`--fail`/`--mute` usage; nothing decorative, no new accent surface |
| execution-outcome data model | `pending_confirmations` GET/resolve response shape | ⚠ unresolved | Flagged prominently in Surface 2 — this is a backend/route-shape gap the research did not scope, not a UI ambiguity. The planner must either extend the response (recommended: join/lookup against `tool_calls_audit` for `resolution='approved'` rows) or explicitly accept, with the operator's sign-off, that this phase ships with the honest "Awaiting execution" fallback and no pass/fail resolution ever appears — the latter is a real product gap against the task brief's "must surface why a rejected approval failed" instruction and should not be silently accepted without that sign-off being recorded |

---

## Registry Safety

Not applicable — GOTHAM is a hand-built token system; `apps/admin` has no `components.json` and
uses no shadcn registry, official or third-party. No registry vetting gate applies to this phase.

| Registry | Blocks Used | Safety Gate |
|----------|-------------|--------------|
| — | — | not applicable |

---

## Contradictions / corrections found against `22-RESEARCH.md`

1. **The "surface why a rejected approval failed" framing conflates two states.** A `resolution =
   'rejected'` row (an owner's own decision) has no denial code and needs no translation — the
   denial-reason translation table applies only to `resolution = 'approved'` rows whose *execution*
   was later denied, which the research's own route design does not yet expose as a distinct field.
   See § Surface 2, "Data model gap found."
2. **CAP-05's open question is resolved, not left as a UI-SPEC-pass judgment call the research
   anticipated staying open.** The research framed it as "arguably the highest-consequence flip" —
   this spec found a sharper, evidence-grounded criterion (immediate live-production effect vs. a
   downstream approval gate) that the research did not identify, and used it to produce a
   conditional (not blanket) staging rule. See § Surface 1.
3. **Placement is confirmed, not merely inherited as a default.** The research flagged Deploy-page
   placement as "pending UI-SPEC confirmation" — this spec confirms it with its own reasoning
   (configuration and its consequences belong on one page, in that order), not by default inertia.

---

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: PASS
- [ ] Dimension 2 Visuals: PASS
- [ ] Dimension 3 Color: PASS
- [ ] Dimension 4 Typography: PASS
- [ ] Dimension 5 Spacing: PASS
- [ ] Dimension 6 Registry Safety: PASS

**Approval:** pending
