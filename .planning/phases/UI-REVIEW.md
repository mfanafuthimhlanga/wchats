# Veridian Admin — M1-M4 Retroactive UI Audit

**Audited:** 2026-05-20
**Baseline:** Abstract 6-pillar standards + project design token contract (globals.css)
**Screenshots:** Not captured (no dev server detected — code-only audit)
**Scope:** apps/admin — landing page, agents list, create agent, agent journey, soul editor, ingest, deploy page, TopNav, HeroSteps, JourneyStepper, AgentCard, StepSubtaskCard

---

## Pillar Scores

| Pillar | Score | Key Finding |
|--------|-------|-------------|
| 1. Visual Hierarchy & Typography | 2/4 | 13 distinct font sizes in use; page h1s span 20–26px with no consistent scale |
| 2. Color & Contrast | 2/4 | `outline: none` on all inputs kills focus ring; surface-1 (#FFFCF9) inputs on bg (#F0E8E0) create jarring white boxes |
| 3. Spacing & Layout Rhythm | 3/4 | Core page rhythm consistent at 32/40px; micro-spacing uses 6/8/10/12/14/16px non-grid values |
| 4. Component Consistency | 2/4 | Input styling, label style, and tab components are duplicated across pages rather than extracted |
| 5. Brand & Identity Coherence | 2/4 | Three "Veridian" strings survive in TopNav, deploy preview, and landing body copy (fix in progress) |
| 6. Interaction States & Feedback | 3/4 | Loading, error, empty states are present and correct; focus states are the critical gap |

**Overall: 14/24**

---

## Priority Fixes (sorted by visual impact)

1. **BLOCKER — Remove `outline: 'none'` from all input/textarea/select without adding a focus replacement** — affects 7 occurrences across soul/page.tsx (5×), ingest/page.tsx (1×), new/page.tsx (1×). Any keyboard or screen-reader user gets zero focus indication on the most-used controls in the app. Fix: replace `outline: 'none'` with `outline: 'none', boxShadow: 'var(--shadow-focus)'` on all form controls using the already-defined `--shadow-focus: 0 0 0 3px rgba(123,28,58,0.18)` token.

2. **WARNING — Input surface colour clash** — `background: 'var(--surface-1)'` (#FFFCF9, near-white) on form inputs renders inside page sections whose background is `var(--bg)` (#F0E8E0, warm beige). This is confirmed across soul editor, new agent form, and ingest URL tab. The contrast jump produces unintentional "white box" artefacts that break the warm editorial palette. Fix: change input backgrounds to `var(--surface-2)` (#F7F0EA) so they sit one step lighter than --bg, not three. This matches the stripe/OTP fields already used in HeroSteps correctly at surface-2.

3. **BLOCKER — TopNav wordmark still reads "Veridian"** (TopNav.tsx:40) and uses a plain wine-coloured rectangle as logo rather than the letterman image and "Chats" wordmark used on the landing page nav. The admin shell is the primary authenticated UI — the rename inconsistency is on the highest-visibility persistent chrome. Fix: mirror the landing page pattern: `<img src="/chats-letterman.png" />` + `<span style={{ fontFamily: 'var(--font-pixelify)' }}>Chats</span>`. (Marked fix in progress per audit brief.)

4. **WARNING — Typography scale has 13 distinct font sizes with off-scale outliers** — sizes 9px (receipt), 10px (ingest badge), 13.5px (deploy card), and 17px (landing hero body) fall outside any defined step. The core body text is 14px but 13px appears 18 times making it a de-facto second base size. Fix: normalise to a 5-step scale: 11/13/14/16/22px matching the most-used values. Retire 9, 10, 13.5, 17, 20, 26px.

5. **WARNING — Spinning logo on landing page nav is unconditionally active** — `animation: 'spin-cw 4s linear infinite'` on the logo image runs perpetually with no pause-on-reduced-motion guard. This is a WCAG 2.3 (seizures) concern and creates visual noise that competes with the hero animation. Fix: wrap in `@media (prefers-reduced-motion: no-preference) { .logo-spin { animation: ... } }`.

6. **WARNING — Deploy preview still says "Veridian assistant"** (deploy/page.tsx:714) inside the live widget mockup. This is a user-facing string seen by any tenant configuring their embed. Fix: change to "Your assistant" or a dynamic `{agent?.name || 'Your assistant'}`. (Marked fix in progress per audit brief.)

7. **WARNING — Component duplication erodes design consistency** — `labelStyle`, `inputStyle`, and error alert markup are copy-pasted across at least 3 pages (soul/page.tsx, new/page.tsx, ingest/page.tsx). The deploy page typography label (`fontSize: '12px'`) diverges from the LABEL_STYLE constant (`fontSize: '11px'`, uppercase, letterspacing). StepSubtaskCard idle CTA renders `color: 'var(--text-3)'` on `background: 'transparent'` — text-3 (#8A6060) on white-ish surface may fail WCAG AA (estimated ~3.5:1). Fix: extract `<FormLabel>`, `<FormInput>`, `<AlertBanner>` components from the duplicated inline styles.

8. **WARNING — AgentCard uses --bg as card background** (AgentCard.tsx:52) rather than --surface-1. Cards sit inside a --bg page background, making them invisible without the border. A card component should float above the page, not blend in. Fix: change `background: 'var(--bg)'` to `background: 'var(--surface-1)'`.

---

## Detailed Findings

### Pillar 1: Visual Hierarchy & Typography (2/4)

**Font size audit — 13 distinct sizes in use:**

| Size | Count | Location |
|------|-------|----------|
| 14px | 52 | Body text, form inputs, CTAs |
| 13px | 18 | Secondary text, chat bubbles |
| 12px | 18 | Helper text, footer, meta |
| 11px | 16 | Uppercase labels, section heads |
| 16px | 6 | Card titles, sub-heads |
| 15px | 6 | Primary CTAs |
| 22px | 5 | Page h1 (inner pages) |
| 18px | 4 | Section h2 |
| 20px | 3 | Soul editor h1 (inconsistent — agents list uses 24px for same level) |
| 24px | 2 | Agents list h1, create agent h1 |
| 26px | 1 | Landing hero h1 (should be the largest, but only appears once) |
| 17px | 1 | Landing hero body copy (off-scale; body copy uses 14px everywhere else) |
| 9/10/13.5px | 3 | Receipt text (9px), ingest badge (10px), deploy card label (13.5px) |

**Off-scale sizes requiring fix:**
- `9px` at HeroSteps.tsx:555 (receipt "Order Receipt" label) — below legible minimum
- `10px` at ingest/page.tsx:557 (source type badge) — borderline, renders as all-caps but still small
- `13.5px` at deploy/page.tsx:167 — fractional pixel non-value; round to 13 or 14
- `17px` at page.tsx:111 — one-off landing copy size; should be 16 to match scale

**H1 inconsistency:** landing hero is 26px, inner pages use 24px (agents list, new agent), 22px (ingest, deploy, eval, settings), and 20px (soul editor). Four distinct h1 sizes produce hierarchy that shifts across the app with no semantic pattern.

**Font weight:** Only two weights in active use (600 semibold, 700 bold) plus 400 normal. This is correct and consistent.

**FungkyBrow/Pixelify font** is used only on the landing nav wordmark. This is the right restraint — the brand display font is properly contained.

---

### Pillar 2: Color & Contrast (2/4)

**Focus state — BLOCKER:**
`--shadow-focus` is defined in globals.css but is never applied to any interactive element. All 7 `outline: 'none'` instances have no compensating focus style:
- `apps/admin/app/agents/new/page.tsx:182` — `inputStyle` object applied to both text input and select
- `apps/admin/app/agents/[id]/soul/page.tsx:378, 404, 431, 481, 565` — 5 separate inputs/textareas/selects
- `apps/admin/app/agents/[id]/ingest/page.tsx:483` — URL input

Keyboard navigation on the soul and ingest pages is visually unusable.

**Input background clash:**
All form inputs use `background: 'var(--surface-1)'` (#FFFCF9). The page background is `var(--bg)` (#F0E8E0). Contrast ratio between these two:
- #FFFCF9 vs #F0E8E0: approximately 1.15:1 — visually these look like a near-white input on a warm beige page, creating a jarring "white box" effect confirmed in the user's own callout.
- HeroSteps stripe/OTP fields correctly use `var(--surface-2)` (#F7F0EA) which blends much more naturally.

**Hardcoded hex in deploy/page.tsx:**
DEFAULT_CONFIG widget colors use raw hex strings (`#FDF9F5`, `#7B1C3A`, `#4A2030`, `#F7F0EA`) — these are aliases for design tokens but bypass the token system. If a token is updated, these defaults silently diverge.

**StepSubtaskCard idle CTA text:**
Idle CTAs render `color: 'var(--text-3)'` (#8A6060) on `background: 'transparent'` which resolves visually to approximately surface-2 (#F7F0EA). Estimated contrast ratio ~3.1:1 — fails WCAG AA 4.5:1 for normal text.

**Good:** Status chips (ready/pending/error), error alerts, and accent usage on primary actions are all correct and token-consistent. The wine accent (#7B1C3A) is used appropriately for primary buttons and active tab underlines without over-application.

---

### Pillar 3: Spacing & Layout Rhythm (3/4)

**Page-level rhythm is good:** All pages use `padding: '32px 40px'` or `padding: '40px 32px'` consistently. MaxWidth is `1180px` on the landing and agents list. The two-column layout (320px sidebar + flex-1) is applied consistently across create/soul/journey pages.

**Gap values — fragmented micro-spacing:**

| Value | Count | Notes |
|-------|-------|-------|
| 12px | 7 | Most common gap |
| 8px | 5 | — |
| 10px | 3 | Off-grid between 8 and 12 |
| 16px | 3 | — |
| 6px | 2 | Icon/chip gaps — acceptable |
| 4px | 2 | Nav link gap — tight but intentional |
| 64px | 1 | Hero two-column gap — fine |
| 20px, 24px | 1 each | Section-level gaps |

The 10px gap (`HeroSteps.tsx:630` messages, `AgentCard marginBottom: '6px'`) sits between scale steps. The most common value is 12px; 10px is used where 12px or 8px should apply.

**Inline margin inconsistency:**
- `AgentCard` role text: `margin: '0 0 12px 0'` but footer uses `margin: 0`
- Soul page label: `marginBottom: '6px'` in LABEL_STYLE vs `marginBottom: '8px'` in do/do-not list labels (same semantic level, two values)
- Deploy page section headers use `margin: '0 0 12px'` and `margin: '0 0 8px'` for identical-looking section separators

**Landing hero padding:** `padding: '80px 32px'` for the hero section — this value (80px) is the only use of that spacing value in the app. 80px is fine but consider using a multiple of the 8px grid (it is: 80 = 10×8).

---

### Pillar 4: Component Consistency (2/4)

**Input styling is duplicated, not shared:**
Three separate objects define identical-looking input styles across pages:
- `inputStyle` in `new/page.tsx:173-184` (also used for select)
- Inline style at `soul/page.tsx:368-378` (same values)
- Inline style at `ingest/page.tsx:474-484` (same values)

All three define `padding: '10px 12px'`, `border: '1px solid var(--border)'`, `borderRadius: 'var(--radius-xs)'`, `fontSize: '14px'`, `background: 'var(--surface-1)'`, `color: 'var(--text-1)'`, `outline: 'none'`. They are not imported from a shared component. When the surface-1 → surface-2 fix is applied, it must be made in three places.

**LABEL_STYLE divergence:**
- `LABEL_STYLE` constant in `soul/page.tsx:74-82`: `fontSize: '11px'`, uppercase, `letterSpacing: '0.08em'`, `color: 'var(--text-3)'`
- Do/Do-Not section labels in same file: `fontWeight: 600, fontSize: '14px', color: 'var(--text-2)'` — same visual level, completely different style
- Deploy page section labels: `fontSize: '11px'`, uppercase — correct — but applied as inline style, not imported from the constant
- Ingest labels: defined inline at `ingest/page.tsx:398-408` matching soul's LABEL_STYLE values but independently

**Tab component repeated:**
Tab nav is implemented independently in soul/page.tsx, ingest/page.tsx, and deploy/page.tsx with the same `borderBottom: 2px solid` active indicator pattern. No shared `<TabNav>` component exists. The three implementations are structurally identical but not DRY.

**AgentCard background token mismatch:**
`background: 'var(--bg)'` on the card — cards should float above the page background. Using the same token as the page means the card is visually defined only by its `border-soft` border, which is very low contrast (#EDE3D8 on #F0E8E0, approximately 1.07:1). Low-contrast cards are difficult to parse as discrete entities on screens with poor gamma calibration.

**StepSubtaskCard left-border state indicator:**
State `active` = `3px solid var(--accent)`, `completed` = `3px solid var(--green)`. But state `idle` shifts to `1px solid var(--border-soft)` — the border weight change causes layout shift when state transitions from idle to active, shifting card content left by 2px. Fix: always use a 3px left border, setting the idle colour to `transparent` or `var(--border-soft)` at full width.

**JourneyStepper vertical connector positioning:**
Connector line is positioned `left: '25px'` which is the horizontal center of the 28px circle. This is correct. However `top: '52px'` is hardcoded — if a step row is shorter than expected, the line will not connect cleanly to the next circle. This is a fragile layout assumption.

---

### Pillar 5: Brand & Identity Coherence (2/4)

**Known issues (marked "fix in progress" per audit brief):**

1. `TopNav.tsx:40` — wordmark literal string "Veridian" and logo is a plain wine rectangle, not the letterman image.
2. `page.tsx:119` — hero body copy "Veridian wires a Claude Agent SDK reasoning engine..."
3. `deploy/page.tsx:714` — widget preview shows "Veridian assistant"

**Assessment of #1 severity:** The TopNav is the persistent chrome on every authenticated page. It is the highest-frequency brand touchpoint in the app. Every tenant who logs in and sees "Veridian" in the top-left while the product is now "Chats" will register cognitive dissonance. This is the highest-priority brand fix.

**Positive brand signals:**
- Landing page nav correctly uses FungkyBrow "Chats" with the letterman spinning logo
- Metadata (`layout.tsx:12`) already reads `title: 'Chats'`, `description: 'Chats agent management'`
- Tab title and favicon are already updated
- Wine/beige/gold palette is visually distinctive and applied consistently
- HeroSteps animation is sophisticated and demonstrates the product narrative effectively

**deploy/page.tsx:99 — Embed snippet CDN URL:**
`https://widget.veridian.app/widget.js` — still references the old product name in a user-visible string. A tenant who copies this snippet and reads the URL will see "veridian." The CDN placeholder note below (line 441) partially covers this, but the URL itself should reference the product's current naming if changed.

---

### Pillar 6: Interaction States & Feedback (3/4)

**Loading states — present:**
- `agents/page.tsx:154-156`: loading guard with "Loading agents…" text paragraph
- `agents/new/page.tsx` provisioning phase: dedicated UI panel with status polling display
- `agents/[id]/ingest/page.tsx` SSE progress: progress label updated through parsing/chunking/embedding/done states with contextual `EVENT_LABELS` map

**Error states — present and consistent:**
Error alerts use a shared visual pattern (`role="alert"`, `var(--red-bg)` background, red border) across all pages. The pattern is copy-pasted but visually correct. Error messages are contextual ("Cannot reach the API server. Make sure the backend is running on...") rather than generic.

**Empty states:**
- Agents list empty state: `"No agents yet."` + Create CTA — adequate but the copy is minimal. A slightly more motivating line would help ("You haven't created an agent yet. Build your first one in under 5 minutes.")
- Document list: no empty state component — when `documents.length === 0` the document list section simply doesn't render. No "No documents yet" message is shown to tell the user what to do next.
- Soul editor: no empty state for when the API call fails to return data — the form just shows blank inputs with no indication that something is wrong (separate from the `loadError` banner).

**Disabled states — correct:**
- Save Soul button disabled when `name.trim()` is empty: correct
- Upload file button disabled when no file selected: correct  
- All buttons meet 44px minHeight where `minHeight: '44px'` is declared (16 occurrences confirmed)

**Focus states — BLOCKER:**
As documented under Pillar 2: zero focus styles on any form input, textarea, or select. The `--shadow-focus` token exists but is unused. The send button in HeroSteps (30×30px) is below the 44px touch target minimum but it is a decorative demo element, not a real interactive control.

**Hover states — missing on interactive cards:**
`AgentCard` is a full-card `<Link>` with no hover style. Users have no visual confirmation the card is interactive until cursor changes. Fix: add `transition: 'box-shadow 0.15s, border-color 0.15s'` and on hover apply `boxShadow: 'var(--shadow-lift)'` and `borderColor: 'var(--border-hard)'`. (No `onMouseEnter`/`onMouseLeave` is currently present.)

**Confirmation for destructive actions:**
The "Remove do item" and "Remove do-not item" buttons in soul/page.tsx fire immediately with no undo or confirmation. For a short list this is acceptable, but if a user accidentally deletes a carefully worded instruction there is no recovery path within the session (though the change is only committed on Save). This is a minor UX gap, not a blocker.

**Provisioning timeout UX:**
`new/page.tsx` has a 120s polling timeout with a `pre-line` formatted error message including the Celery command. This is appropriate for a developer-facing tool and the error message is genuinely actionable.

---

## Registry Safety

No `components.json` found. shadcn is not initialised in this project. Registry audit skipped.

---

## Files Audited

- `apps/admin/app/globals.css`
- `apps/admin/app/layout.tsx`
- `apps/admin/app/page.tsx`
- `apps/admin/app/components/HeroSteps.tsx`
- `apps/admin/app/components/TopNav.tsx`
- `apps/admin/app/components/AgentCard.tsx`
- `apps/admin/app/components/JourneyStepper.tsx`
- `apps/admin/app/components/StepSubtaskCard.tsx`
- `apps/admin/app/agents/layout.tsx`
- `apps/admin/app/agents/page.tsx`
- `apps/admin/app/agents/new/page.tsx`
- `apps/admin/app/agents/[id]/page.tsx`
- `apps/admin/app/agents/[id]/soul/page.tsx`
- `apps/admin/app/agents/[id]/ingest/page.tsx`
- `apps/admin/app/agents/[id]/deploy/page.tsx`
