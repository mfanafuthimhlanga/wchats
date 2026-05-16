# 04-UI-REVIEW.md — Phase 4: Reasoning Engine + Widget v0

**Audit method:** Playwright live screenshots (12 captures across 3 surfaces)  
**Date:** 2026-05-16  
**Spec:** UI-SPEC.md (Parchment & Wine / Design G)  
**Surfaces audited:** Widget (`localhost:5173`), Admin Soul Editor (`localhost:3001`), Demo (`apps/demo/index.html`)

---

## Scores

| Pillar | Score | Verdict |
|--------|-------|---------|
| P1 · Visual Fidelity | 2/4 | NEEDS WORK |
| P2 · Accessibility | 2/4 | NEEDS WORK |
| P3 · Component Hierarchy | 2/4 | NEEDS WORK |
| P4 · State Coverage | 2/4 | NEEDS WORK |
| P5 · Performance | 3/4 | GOOD |
| P6 · Copy & Content | 3/4 | GOOD |
| **Overall** | **58/100** | **NEEDS WORK** |

---

## P1 · Visual Fidelity — 2/4

### What the screenshots confirm is working

The Parchment & Wine palette is correctly applied across all three surfaces. The token values match the spec (`--bg #FDF9F5`, `--accent #7B1C3A`, `--gold #B8860B`) and the warm cream atmosphere is immediately recognisable as Design G.

**Widget:** Agent bubble white card with 3px left wine border renders well (screenshot 02). User bubble wine fill + right-alignment correct. Disclosure bar shows "Powered by AI" left + "veridian v0" monospace right. Typing indicator dots render in wine. Tool-call label renders with dashed gold border, blinking dot, and `retrieve("latte sizes")` monospace text (screenshot 03).

**Demo:** Strong first impression — 48px bold headline, correct badge pill, two-column hero split, metrics strip 100%/<20kb/SSE with wine numerals, trust strip with correct three cards and footer copy (screenshots 06–08). The logo "Veri**dian**" wine accent is clean.

**Admin:** Two-column layout, traffic-light chrome bar, tab nav with wine active underline, live preview pane with partial syntax highlighting (YOU MUST / YOU MUST NOT keys in amber) all render correctly (screenshots 09–10).

### What the screenshots expose as broken

**Widget — missing AgentCluster wrapper (P1-W-01).**  
Every agent turn in the spec requires an `AgentNameLabel` (agent name in wine, 11px, weight 600) + "AGENT" badge pill *above* each agent bubble. Screenshots 02–05 show agent bubbles rendered directly with no header — a bare white card with a left rule. In a multi-agent or multi-turn conversation, messages are indistinguishable without this attribution row.

**Widget — no UserMeta row (P1-W-02).**  
The spec requires a "You · HH:MM" timestamp line in `--font-mono` below every user bubble. No such row appears in any screenshot. Screenshot 02 shows the user bubble followed immediately by typing dots with no timestamp.

**Widget empty state shows error banner, not greeting (P1-W-03).**  
Screenshot 01 shows the error message ("Something went wrong. Please try again.") displayed on first load because the `/widget/config` fetch fails with no backend. The spec's `EmptyState` ("Hi! I'm {agent_name}. Ask me anything.") is never shown. The widget should fall back gracefully with a greeting, not surface a network error as the first thing a user sees.

**Widget — citation row background wrong (P1-W-04).**  
Spec: `background: var(--surface-2)` (#F7F0EA — parchment). Screenshots 02–05 show citation rows with a near-white background consistent with `--surface-1` (#FFFCF9). The distinction is visible but subtle; the parchment tone is the intended visual anchor for source attribution.

**Widget — TypingIndicator is bare dots, no bubble wrapper (P1-W-05).**  
Spec: "Displayed inside a bubble with same left-rule style as MessageBubble.agent." Screenshots 02–03 show three animated dots floating directly in the scroll area with no container. The visual rhythm breaks — the dots look disconnected from the agent turn pattern.

**Escalation panel: missing 3px left gold border, wrong button colour (P1-W-06).**  
Spec requires `border-left: 3px solid var(--gold)` on the escalation panel and an `--accent` (wine) fill "Send my details" button. Screenshot 04 shows the panel with a uniform 1px soft border and no left gold rule. The submit button is filled with `--gold` (dark amber) rather than `--accent` (wine) — inconsistent with every other primary action.

**Admin — tab names deviate from spec (P1-A-01).**  
Spec tabs: Overview / **Soul** / Conversations / Retrieval / Settings.  
Rendered tabs (screenshot 09): Overview / **Documents** / Soul / Evals / Settings.  
"Documents" and "Evals" are not spec tabs; "Conversations" and "Retrieval" are missing.

**Admin — form labels not styled per spec (P1-A-02).**  
Spec: `FormLabel` = 11px, weight 600, uppercase, letter-spacing 0.08em, `--text-3`.  
Screenshots show labels at ~14px, sentence case, in `--text-2`. The labels are readable but not at the compact uppercase anatomy the spec defines.

**Admin — Role is a free-text input, not a select (P1-A-03).**  
Spec: `RoleSelect` = `<select>` with preset options (Customer Support / Sales Qualification / Internal Helpdesk / Custom…). Screenshots show a plain text input. The interaction model for a non-technical SMB owner differs significantly — the dropdown prevents blank or malformed roles.

**Admin — no responsive collapse at <1100px (P1-A-04).**  
Spec: "At viewport < 1100px, collapse to single column (preview hidden)." Screenshot 12 at 1099px shows the two-column layout fully intact — the live preview panel does not hide. A hiring manager on a laptop or a non-technical user on a 1080px display would see a cramped two-column view.

**Demo — widget card in hero is blank (P1-D-01).**  
The iframe in the hero renders as a blank white card (screenshots 06–07) because there is no backend. This is expected for M4 local dev but the `src` attribute contains `DEMO_AGENT_ID_PLACEHOLDER`. For the portfolio artifact, this is the first thing a hiring manager sees where interactivity was promised.

---

## P2 · Accessibility — 2/4

### Confirmed working

- `ScrollArea` has `role="log"` + `aria-live="polite"` (code; renders correctly).
- `TypingIndicator` has `aria-label="Agent is typing"` ✓.
- Send button has `aria-label="Send message"` ✓; 44×44px minimum met ✓.
- Admin: all visible form fields have `<label>` elements with `htmlFor` ✓.
- Remove item buttons have `aria-label="Remove do item N"` ✓.
- Agent Name asterisk (`*`) visible in red as required indicator ✓.

### Confirmed broken or unverifiable

**P2-01 · Disclosure bar contrast fails WCAG 4.5:1.**  
"Powered by AI" renders in `--text-3` (#8A6060) on `--surface-2` (#F7F0EA) at 11px. Estimated contrast ratio ≈ 4.1:1 — below the 4.5:1 WCAG AA threshold for body text at this size. The text is readable but non-compliant. Visible in screenshot 01.

**P2-02 · Escalation form inputs are placeholder-only — WCAG 1.3.1 fail.**  
Screenshot 04 shows "Your name" and "Your email" as placeholder text inside inputs with no associated `<label>`. Placeholders disappear on focus and are not announced by screen readers as persistent labels. This fails WCAG 1.3.1 (Info and Relationships) and 3.3.2 (Labels or Instructions).

**P2-03 · Error banner lacks `role="alert"`.**  
Screenshot 01 and 05 both show the error message rendered as a styled `div.error-msg`. Without `role="alert"`, screen readers will not announce it automatically on insertion. Spec explicitly requires this.

**P2-04 · Widget textarea missing `aria-label="Message"`.**  
The input has a placeholder ("Type a message...") but no explicit `aria-label`. Screen readers using placeholder as a label substitute is inconsistent across assistive technology. Spec requires `<input aria-label="Message">`.

**P2-05 · EscalationPanel has no `role="dialog"` or `aria-modal="false"`.**  
Screenshot 04 shows the panel rendered as a plain `div`. Screen reader users receive no signal that a contextual panel requiring their attention has appeared. Spec explicitly requires both attributes.

**P2-06 · Admin "Name is required" validation fires immediately on load.**  
Screenshot 09 shows the red border on the Agent Name field and "Name is required" error text on first render — before the user has touched anything. This is a false-positive validation state that creates unnecessary urgency. Validation should fire only after a blur event or save attempt. Also: the validation text uses `aria-live` implicitly but no `role="alert"` or `aria-describedby` connects the error to the input.

**P2-07 · Inactive admin tab buttons are `onClick={undefined}` — not semantically disabled.**  
Screenshots show four non-Soul tabs rendered as fully clickable buttons with pointer cursor that do nothing on click. They should use `disabled` or `aria-disabled="true"` with `tabIndex={-1}` so keyboard users aren't navigating dead controls.

---

## P3 · Component Hierarchy — 2/4

### What works

Widget's three-section stack (DisclosureBar / ScrollArea / InputBar) is structurally sound. SSE state flows correctly into presentational components. CitationRow, ToolCallLabel, and EscalationPanel are separate components rather than inline JSX. The admin's `buildSystemPromptPreview` TS function is cleanly separated from render.

### What's broken

**P3-01 · Missing AgentCluster wrapper.**  
`Widget.jsx` renders `<MessageBubble role="agent">` directly. There is no `AgentCluster` component that wraps each agent turn with a name label and badge. Multi-turn conversations in screenshots 02–05 show agent messages that could be from any source. Adding `AgentCluster` requires extracting a wrapper component and threading `agentName` from config down through the message model.

**P3-02 · EscalationPanel inside `role="log"` region.**  
The panel is rendered inside `.scroll-area` which has `role="log"` and `aria-live="polite"`. The spec positions it "between message area and input bar" — outside the scrolling message log. Placing a `role="dialog"` panel inside a `role="log"` creates a conflicted ARIA tree: screen readers may treat the form as a log entry rather than an action panel. This requires moving `EscalationPanel` to a sibling of `.scroll-area` in Widget.jsx.

**P3-03 · DoList and DoNotList are identical inline blocks — not a shared component.**  
Screenshots 09–11 show two structurally identical list sections (Do List, Do Not List) each with their own inline style objects and handler props. They are 80+ lines of duplicated JSX. A `DynamicList` component with `label`, `badge`, and `items`/`onAdd`/`onRemove`/`onChange` props would halve the size and allow the badge pill ("DO" / "DON'T") to be conditionally styled.

**P3-04 · No `EmptyState` component.**  
The spec defines a distinct empty state component with a greeting message. Widget.jsx has no conditional render for `messages.length === 0`. The absence means first-time users see either nothing (if config loads) or an error banner (if it doesn't) — neither is the intended warm greeting.

---

## P4 · State Coverage — 2/4

### States verified visually

| State | Screenshot | Result |
|-------|-----------|--------|
| `error` | 01, 05 | Renders — but fires on first load (no backend) |
| `thinking` | 02 | TypingIndicator renders ✓ |
| `tool_call` | 03 | ToolCallLabel renders ✓ |
| `escalated` | 04 | Panel renders ✓ |
| `idle` (empty) | 01 | Error shown instead of EmptyState ✗ |
| `submitting` | — | Cannot verify headless; code shows no spinner swap |

### Gaps

**P4-01 · `idle` empty state defaults to error.**  
First load without a backend sends the widget straight from `loading` to `error`. The spec's `idle` state requires `EmptyState` visible. There is no graceful degradation path — the widget should show the EmptyState regardless of whether config loading succeeded, and only show the error banner if the user *tries* to send a message and it fails.

**P4-02 · `submitting` state: send button shows no spinner.**  
Spec: "Send button shows spinner inline SVG" during submitting. Code disables the button (correct) but the SVG send-arrow stays visible — no spinner swap. This was not directly verifiable in screenshots but was confirmed in code review of `InputBar.jsx`.

**P4-03 · `tool_call` state: TypingIndicator disappears.**  
In Widget.jsx: `{status === 'thinking' && <TypingIndicator />}` and `{status === 'tool_call' && <ToolCallLabel />}`. The spec says "TypingIndicator *continues*. ToolCallLabel appears *below* it." The screenshot showing both (03) was achieved by injecting both manually — the actual state machine replaces TypingIndicator with ToolCallLabel rather than showing both simultaneously.

**P4-04 · No auto-scroll to bottom.**  
Screenshots 02–05 show messages appearing at the top of the scroll area. There is no `useEffect` that scrolls the `.scroll-area` container to its `scrollHeight` when messages or the typing indicator are added. Long conversations would require the user to manually scroll down to see new content.

**P4-05 · Admin validation fires before user interaction.**  
Screenshot 09 shows "Name is required" in red on the Agent Name field immediately on page load before the user has typed anything. Spec does not define an always-visible validation error on mount — it should appear on save attempt or after field blur.

---

## P5 · Performance — 3/4

### Confirmed working

- Widget uses Preact (3kb gzipped vs React 45kb) — correct framework choice ✓
- Font stack: `system-ui` in widget (no CDN import) ✓
- CSS `@keyframes` animations — no JS animation library ✓
- Inline SVGs for all icons (send arrow, doc icon, tool-call dot) ✓
- Demo page: pure static HTML, no JS, Google Fonts CDN (acceptable for demo) ✓
- Admin: Next.js with Tailwind — appropriate for admin surface ✓
- Live preview recomputes in TypeScript without any API round-trip ✓
- `check-size.mjs` script present and wired to `postbuild` ✓

### Gaps

**P5-01 · Bundle size not verified in this audit.**  
The `postbuild` check exists but was not run as part of this review (no production build was triggered). A screenshot-based audit cannot confirm gzipped size. Recommend running `npm run build` and confirming the `check-size.mjs` gate passes before marking P5 green.

**P5-02 · Shadow tokens missing from `widget.css`.**  
The CSS spec defines `--shadow-card`, `--shadow-lift`, and `--shadow-focus`. None are in `widget.css`. The send button has no `--shadow-focus` on keyboard focus, which matters for both aesthetics and accessibility. Minor byte-wise but a spec gap.

---

## P6 · Copy & Content — 3/4

### Confirmed correct

| Element | Expected | Rendered |
|---------|---------|---------|
| Widget disclosure | "Powered by AI" | ✓ |
| Widget tag | "veridian v0" | ✓ |
| Widget error | "Something went wrong. Please try again." | ✓ |
| Citation action | "VIEW" | ✓ |
| Escalation body | "I've flagged this for our team. Expect a reply within 24 hours." | ✓ |
| Escalation CTA | "Send my details" | ✓ |
| Admin H1 | "Agent Soul" | ✓ |
| Admin save CTA | "Save Soul" | ✓ |
| Demo headline | "Ask anything about Bella Vista Coffee" | ✓ |
| Demo subheadline | "Powered by Veridian — a grounded AI agent that cites its sources" | ✓ |
| Demo footer | "veridian/m4 · portfolio demo · Bantu Son" | ✓ |

### Deviations

**P6-01 · Escalation header copy wrong.**  
Rendered: "Escalated to Human". Spec: "Flagged for our team". The spec copy is less alarming and more action-oriented for an end user who may not know what "escalation" means.

**P6-02 · Admin voice warning copy deviates.**  
Rendered: "Voice & Tone is blank — a default will be used in the system prompt"  
Spec: "A voice description improves agent consistency."  
The rendered copy is technically informative but developer-facing (references "system prompt"). The spec copy is user-facing.

**P6-03 · Admin name validation copy deviates.**  
Rendered: "Name is required". Spec: "Agent name is required." — Minor but inconsistent with spec contract.

**P6-04 · Demo primary CTA is a no-op anchor.**  
"Try the demo →" points to `href="#hero"` (the current section). A hiring manager clicking it lands in place. Spec intends this as a call to interact with the widget below — either link to the widget position or scroll to it. Minor UX issue but consequential for the portfolio entry point.

---

## Finding Register

### P0 — Must fix (accessibility violations)

| ID | Finding | Location |
|----|---------|---------|
| F-01 | Escalation form inputs placeholder-only — no `<label>` — WCAG 1.3.1 fail | `EscalationPanel.jsx` |
| F-02 | Widget textarea missing `aria-label="Message"` | `InputBar.jsx` |
| F-03 | ErrorBanner missing `role="alert"` | `Widget.jsx` |
| F-04 | EscalationPanel missing `role="dialog"` + `aria-modal="false"` | `EscalationPanel.jsx` |
| F-05 | Disclosure bar text contrast ≈ 4.1:1 — below WCAG 4.5:1 at 11px | `widget.css` |

### P1 — High (visible spec gaps that affect first impression)

| ID | Finding | Location |
|----|---------|---------|
| F-06 | AgentCluster / AgentNameLabel / AGENT badge missing from every agent turn | `Widget.jsx`, missing component |
| F-07 | UserMeta timestamp row missing below user bubbles | `Widget.jsx`, missing component |
| F-08 | EmptyState missing — widget shows error on cold load instead of greeting | `Widget.jsx`, missing component |
| F-09 | TypingIndicator has no bubble wrapper (bare dots, no left-rule container) | `TypingIndicator.jsx` |
| F-10 | TypingIndicator replaced by ToolCallLabel during `tool_call` — should be both | `Widget.jsx` |
| F-11 | Escalation panel missing 3px left `--gold` border | `widget.css` |
| F-12 | Escalation "Send my details" button uses `--gold` fill, not `--accent` | `widget.css` |
| F-13 | Admin responsive collapse at <1100px not implemented — preview always visible | `page.tsx` |
| F-14 | Admin Role field is `<input>` not `<select>` with preset options | `page.tsx` |
| F-15 | Admin tab names wrong: "Documents/Evals" vs spec "Conversations/Retrieval" | `page.tsx` |
| F-16 | EscalationPanel inside `role="log"` scroll area — should be a sibling | `Widget.jsx` |

### P2 — Medium (spec deviations, UX degradation)

| ID | Finding | Location |
|----|---------|---------|
| F-17 | Citation row background is `--surface-1`, spec requires `--surface-2` | `widget.css` |
| F-18 | Admin name validation error fires on mount, not on blur/save | `page.tsx` |
| F-19 | `submitting` state: send button shows no spinner SVG swap | `InputBar.jsx` |
| F-20 | No auto-scroll to bottom on new message or typing indicator | `Widget.jsx` |
| F-21 | Admin form labels not uppercase 11px `--text-3` per spec | `page.tsx` |
| F-22 | Escalation header "Escalated to Human" → should be "Flagged for our team" | `EscalationPanel.jsx` |
| F-23 | Inactive admin tabs not `aria-disabled`/`disabled` | `page.tsx` |

### P3 — Low (minor copy or polish)

| ID | Finding | Location |
|----|---------|---------|
| F-24 | Admin voice warning copy differs from spec | `page.tsx` |
| F-25 | Admin name error "Name is required" → spec "Agent name is required." | `page.tsx` |
| F-26 | Demo primary CTA "Try the demo →" links to `#hero` (same page, no-op) | `demo/index.html` |
| F-27 | `--shadow-focus` and shadow tokens absent from `widget.css` | `widget.css` |
| F-28 | Bundle size gate not verified (no production build in this audit) | `scripts/check-size.mjs` |

---

## Fix Priority

Recommended order for a single remediation session:

1. **F-01–F-05** — accessibility P0s (30 min, focused changes)
2. **F-08** — EmptyState component (high user impact, one new component)
3. **F-06, F-07** — AgentCluster + UserMeta (one new wrapper component pattern)
4. **F-09, F-10** — TypingIndicator bubble wrapper + parallel tool_call display
5. **F-11, F-12** — Escalation CSS (two CSS lines)
6. **F-16** — EscalationPanel DOM position (Widget.jsx restructure)
7. **F-13** — Admin responsive collapse (one CSS media query + conditional render)
8. **F-18–F-20** — State machine fixes (validation timing, spinner, auto-scroll)
9. **F-14, F-15** — Role select + tab names (low complexity)
10. **F-24–F-27** — Copy + CSS polish

---

*Screenshots archived in `.planning/phases/04-reasoning-engine-widget/screenshots/` (12 files, Playwright headless Chromium).*
