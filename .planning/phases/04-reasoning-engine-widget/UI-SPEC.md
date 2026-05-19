# UI-SPEC — Veridian M4: Reasoning Engine + Widget v0

**Status:** locked
**Phase:** 04 — Reasoning Engine + Widget v0
**Date:** 2026-05-16
**Sources:** 04-CONTEXT.md (decisions locked), REQUIREMENTS.md (AGT-01–AGT-11), CLAUDE.md (bundle/framework constraints), design-g.html (visual reference — Parchment & Wine)

---

## Design Contract Summary

M4 delivers two new UI surfaces: a Preact iframe chat widget (≤20kb gzipped, no external CSS frameworks) and a Next.js Agent Soul Editor page for non-technical SMB owners. Both surfaces operate on the same design principles — grounded, approachable, and trustworthy — while serving completely different execution contexts (anonymous end-user iframe vs. authenticated admin form). The design system must enable a widget that renders cleanly on any third-party background, a form that feels friendly rather than developer-facing, and a static demo page that impresses an AI/ML hiring manager within ten seconds.

**Selected Design: Parchment & Wine (Design G).** Enterprise-clean IBM-Carbon structure (Design D) in a warm burgundy-and-alabaster palette (Design F). `design-g.html` is the visual reference — all implementations must match its layout, palette, and component patterns exactly.

---

## Surface 1: Chat Widget

### Layout and Structure

Container: 380px wide × 600px tall (fixed, iframe). No responsive breakpoints inside the iframe — the host page controls widget placement.

Vertical stack (top to bottom, all heights are flex children):
- **Disclosure bar**: 32px tall, fixed top. "Powered by AI" label centered or left-aligned. Always visible. (California SB-1001 / EU AI Act compliance.)
- **Message scroll area**: fills remaining height between disclosure bar and input bar. Overflows vertically with scroll. Padding: 12px horizontal, 8px vertical from edges.
- **Input bar**: 64px tall, fixed bottom. Text input (grows to max 3 lines) + send button. Separated from scroll area by a 1px divider.

Z-index allocation:
- Base widget layer: z-index 1
- Disclosure bar overlay (if sticky): z-index 10
- Escalation panel overlay: z-index 20
- Typing indicator (inline in scroll): z-index 1

No absolute positioning for message bubbles — all bubbles are flow layout in the scroll area.

### Component Inventory

| Component | Description |
|-----------|-------------|
| `DisclosureBar` | Fixed top bar. 32px height. Left side: gold rule (10×1px) + "Powered by AI" text (`--text-2`, 11px, weight 500). Right side: "veridian v0" in `--font-mono`, 10px, `--text-3`. Background: `--surface-2`. Bottom border: 1px `--border`. Always visible. |
| `AgentCluster` | Wraps every agent turn: `AgentNameLabel` above the bubble. Max-width 88%. |
| `AgentNameLabel` | Row above each agent bubble. Agent name in `--accent`, 11px, weight 600. "AGENT" badge pill: 9px, `--text-3`, border 1px `--border-soft`, background `--surface-2`. |
| `MessageBubble.agent` | White card (`--surface-1`), 1px `--border-soft` border, **3px left `--accent` border** (the left-rule). `border-radius: var(--radius-sm)`. Padding 12px 14px. 14px/1.6 text. Always followed by `CitationRow`. |
| `MessageBubble.user` | Right-aligned. Background `--accent`, white text, `border-radius: var(--radius-sm)`. Padding 10px 14px. Max-width 80%. 14px/1.5, weight 500. Box-shadow: `0 1px 2px rgba(123,28,58,0.2)`. Followed by `UserMeta`. |
| `UserMeta` | Below user bubble. Text: "You · HH:MM". 10px, `--text-4`, `--font-mono`, right-aligned. |
| `CitationRow` | Below every agent bubble (separate element). Background `--surface-2`, border 1px `--border`, **3px left `--accent` border**, `border-radius: var(--radius-sm)`. Contains: inline SVG doc icon (`--accent`) + "Based on: **[Document Name, Section]**" text (12px `--text-2`) + "VIEW" action link (11px `--accent`, weight 600, uppercase). Rendered from `citations` field in `agent.response` SSE payload. |
| `TypingIndicator` | Three animated dots (6px, `--accent`, `border-radius: 50%`). CSS `@keyframes pulse` — opacity 0.25→1→0.25, scale 0.85→1→0.85, 1.4s, staggered 0.2s. Displayed inside a bubble with same left-rule style as `MessageBubble.agent`. |
| `ToolCallLabel` | Appears below `TypingIndicator` during `tool_call` state. Dashed border box (`--surface-2`, 1px dashed `--border`, `--radius-xs`). Contains blinking gold dot (6px, `--gold`, CSS blink animation 1.2s) + retrieval query text in `--font-mono`, 11px, `--text-3`. Example: `retrieve("latte sizes available")`. |
| `EscalationPanel` | Appears after `agent.escalated` SSE event. Positioned between message area and input bar. Background `--gold-light`, border 1px `--gold-soft`, **3px left `--gold` border**, `--radius-sm`. Contains: header row (gold icon box "!", "Flagged for our team" in `--amber`, "Pending" status pill) + message text + name/email form + "Send my details" button (`--accent` fill). `role="dialog"` `aria-modal="false"`. Contact form is no-op in M4. |
| `InputBar` | 64px height. Top border: 1px `--border`. Background `--surface-1`. Padding 0 14px. Contains: text input (flex 1, `--surface-2` background, `--radius-sm`, 13px) + send button. Send button: **44×44px minimum** (WCAG 2.5.5), `--accent` fill, `--radius-sm`, inline SVG send arrow. Disabled while streaming; input clears on send. |
| `ScrollArea` | `role="log"` `aria-live="polite"`. Flex column, gap 18px. Padding 20px 18px. Background `--surface-1`. Auto-scrolls to bottom on new message or typing indicator. |
| `ErrorBanner` | Inline banner above input bar. Background `--red-bg`, text `--red`. `role="alert"`. Text: "Something went wrong. Please try again." Dismissible. |
| `EmptyState` | Shown when no messages. First message is an `AgentCluster` with greeting: "Hi! I'm **{agent_name}**. Ask me anything about {business_name}." |

### States

| State | Trigger | UI Behavior |
|-------|---------|-------------|
| `idle` | Page load, no messages yet | EmptyState visible. Input enabled. DisclosureBar visible. |
| `thinking` | `agent.thinking` SSE event received | TypingIndicator appears in agent position. Input bar send button disabled. |
| `tool_call` | `agent.tool_call` SSE event received | TypingIndicator continues. `ToolCallLabel` appears below it showing the retrieval query (e.g., `retrieve("latte sizes available")`). |
| `streaming` | `agent.response` SSE event received | TypingIndicator replaced by agent MessageBubble. CitationFooter appended. Input re-enabled. |
| `escalated` | `agent.escalated` SSE event received | EscalationPanel appears. Input bar may remain enabled for follow-up. |
| `error` | Network failure, SSE disconnect, non-2xx from API | ErrorBanner shown above input bar. Input re-enabled. |
| `submitting` | User pressed send | Input value locked. Send button shows spinner inline SVG. |

### Token System (CSS Custom Properties)

The widget uses a flat set of CSS custom properties matching Design G's token names. The component code references only these variables — theming config from `/widget/{agent_id}/config` overrides them at runtime.

**Note on font:** Widget bundle must use `system-ui` only (no Google Fonts CDN — bundle size constraint). The admin and demo pages may load Inter via Google Fonts.

```css
:root {
  /* Surfaces — Parchment & Wine palette */
  --bg:              #FDF9F5;          /* widget background */
  --surface-1:       #FFFCF9;          /* agent bubble background, input background */
  --surface-2:       #F7F0EA;          /* disclosure bar, input field bg */
  --surface-3:       #EDE3D8;          /* subtle hover surfaces */
  --border:          #D9CCBE;          /* input bar divider, borders */
  --border-soft:     #EDE3D8;          /* soft borders on bubbles */
  --border-hard:     #B8906A;          /* strong border on hover */

  /* Primary accent — wine */
  --accent:          #7B1C3A;          /* send button, user bubble, agent left-rule */
  --accent-hover:    #5E1229;          /* hover state */
  --accent-deep:     #3D0B1F;          /* citation strong text */
  --accent-dim:      rgba(123,28,58,0.08); /* badge backgrounds, icon box fills */

  /* Secondary — antique gold */
  --gold:            #B8860B;          /* escalation border, disclosure rule, tool-call dot */
  --gold-light:      #FEF9E7;          /* escalation panel background */
  --gold-soft:       #E8D8A6;          /* escalation panel border */

  /* Status */
  --amber:           #92400E;          /* escalation title text */
  --amber-bg:        #FEF3C7;
  --red:             #B91C1C;          /* list remove hover, error */
  --red-bg:          #FEF2F2;
  --green:           #166534;

  /* Text */
  --text-1:          #1A0A0F;          /* primary message text */
  --text-2:          #4A2030;          /* secondary text, subheadings */
  --text-3:          #8A6060;          /* muted text, labels, citation */
  --text-4:          #C4A0A0;          /* placeholder, timestamps, very muted */

  /* Radius */
  --radius-xs:       8px;              /* buttons, small elements */
  --radius-sm:       14px;             /* bubbles, cards, input fields */
  --radius-md:       20px;             /* large cards */

  /* Shadows */
  --shadow-card:     0 1px 2px rgba(74,32,48,0.04), 0 4px 12px rgba(74,32,48,0.06);
  --shadow-lift:     0 4px 8px rgba(74,32,48,0.04), 0 16px 32px rgba(74,32,48,0.08);
  --shadow-focus:    0 0 0 3px rgba(123,28,58,0.18);

  /* Typography — widget uses system-ui only */
  --font-sans:       system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono:       ui-monospace, "SF Mono", Menlo, monospace;

  /* Spacing (8pt scale) */
  --space-xs:        4px;
  --space-sm:        8px;
  --space-md:        16px;
  --space-lg:        24px;
}
```

### Accessibility

- **Color contrast**: All text on background must meet WCAG 2.1 AA (4.5:1 for body text, 3:1 for large text and UI components).
- **Focus management**: After EscalationPanel appears, focus moves to the first form field in the panel. After dismissal, focus returns to input bar.
- **ARIA roles**: ScrollArea has `role="log"` and `aria-live="polite"`. TypingIndicator has `aria-label="Agent is typing"`. EscalationPanel has `role="dialog"` and `aria-modal="false"` (it does not trap focus — user may still type).
- **Keyboard navigation**: Send button reachable by Tab. Form in EscalationPanel fully keyboard-navigable. No keyboard traps.
- **Input**: `<input type="text">` with `aria-label="Message"`. Send `<button>` with `aria-label="Send message"`.
- **Error announcements**: ErrorBanner injected in a `role="alert"` region so screen readers announce it immediately.
- **Disclosure bar**: Rendered as a visible `<p>` or `<div>` — not hidden or aria-hidden. Required by law.

### Bundle Constraints

Target: ≤20,480 bytes gzipped (20kb hard gate — build fails if exceeded).

Prohibited CSS patterns:
- No `@import url()` from external CDNs in production bundle
- No CSS animation libraries (Animate.css, etc.)
- No CSS custom property fallback chains beyond two levels
- No CSS grid with auto-placement generating large layout code paths

Mandated CSS patterns:
- All styles inline in the Preact component or in a single minimal CSS file bundled by Vite
- CSS custom properties for all themeable values (enables config-driven theming from `/widget/{agent_id}/config` response)
- Flexbox layout only (no CSS Grid in widget — saves byte count vs. Grid)
- Typing indicator animation: CSS `@keyframes` with three `opacity` keyframes (minimal bytes)
- SVG icons: inline SVG strings only (no external icon library, no `<img>` references)

---

## Surface 2: Soul Editor

### Layout and Structure

Single Next.js page at `/agents/[id]/soul`.
Layout: full-width editor card with browser chrome mockup (macOS traffic-light dots + breadcrumb). Two-column body: form panel (flex 1) + live preview panel (400px). At viewport < 1100px, collapse to single column (preview hidden).

Page sections (top to bottom):
1. **Browser chrome bar** (`--surface-2` background, 48px): traffic-light dots + breadcrumb "Veridian Admin / Agents / {agent_name} / Soul"
2. **Horizontal tab nav** (`--surface-1` background, 44px tabs): Overview / **Soul (active)** / Conversations / Retrieval / Settings. Active tab: `--accent` color + 2px bottom border `--accent`. For M4, only the Soul tab is functional; others render but are inactive.
3. **Two-column body**: form panel (left, `border-right: 1px solid --border-soft`) + preview panel (right, `--bg` background)
4. **Save section**: flex row at bottom of form panel — "Save Soul" button (`--accent`, `--radius-xs`) + hint text "Changes apply on the next conversation turn."

### Component Inventory

| Component | Description |
|-----------|-------------|
| `BrowserChrome` | `--surface-2` bar, 48px. Traffic-light dots (11px circles: #FF5F57 / #FFBD2E / #28C840) + breadcrumb text "Veridian Admin / Agents / **{agent_name}** / Soul" (13px, `--text-2`, bold `--accent` for current page). |
| `TabNav` | Horizontal tabs, 44px height, 0 24px padding. Tabs: Overview / Soul / Conversations / Retrieval / Settings. Active: `--accent` color + 2px `--accent` bottom border, weight 600. Inactive: `--text-3`. Only Soul tab is functional in M4. |
| `FormPanel` | Left column. Padding 32px 36px. Contains all form fields + save section. |
| `SectionHeader` | H1 "Agent Soul" (22px, weight 700, `--text-1`) + description paragraph (13.5px, `--text-3`) with agent name in `--accent`. |
| `FormLabel` | 11px, weight 600, letter-spacing 0.08em, uppercase, `--text-3`. Explicit `<label>` — no placeholder-only labeling. |
| `NameInput` | `<input type="text">` labeled "Agent Name". Background `--bg`, border 1px `--border`, `--radius-xs`. Focus: `border-color: --accent`, `box-shadow: --shadow-focus`. Required. Max 60 characters. |
| `RoleSelect` | `<select>` with options: "Customer Support", "Sales Qualification", "Internal Helpdesk", "Custom...". Same styling as NameInput + custom chevron SVG. On "Custom" selection: text input appears below (CSS transition). |
| `VoiceToneTextarea` | `<textarea>` labeled "Voice & Tone". Same border/radius/focus as inputs. `resize: vertical`, `min-height: 80px`, `line-height: 1.6`. Placeholder: "e.g. warm and empathetic, uses plain language". |
| `DoList` | Dynamic list with header row: "Do" label + "DO" badge (green tint) + "+ Add item" button (`--accent-dim` background, `--accent` text). Each row: numbered index (11px `--font-mono` `--text-4`) + text input + remove (×) button (28×28px, hover red). On "Add item": appends blank row and focuses new input. |
| `DoNotList` | Same as DoList. Badge shows "DON'T" in red tint. Remove button same styling. |
| `SaveSection` | `border-top: 1px solid --border-soft`, padding-top 20px. Flex row: "Save Soul" button (`--accent` fill, 10px 28px padding, `--radius-xs`, 14px weight 600) + hint text "Changes apply on the next conversation turn." (12px, `--text-3`). |
| `SaveButton` | Inside SaveSection. Label: "Save Soul". On click: PATCH /agents/{id}. States: default / loading (`aria-busy="true"`, spinner) / success ("Saved" for 2s) / error. |
| `LivePreviewPanel` | Right column (400px). Background `--bg`. Top bar (`--surface-2`, 10px 18px padding): green status dot + "system_prompt_preview.txt" in `--font-mono` 12px `--text-3`. Content area: `--font-mono` 12px, `line-height: 1.75`, `white-space: pre-wrap`, syntax-highlighted (keys in `--accent`, strings in `--amber`, comments in `--text-4`). Updates on every field change. `aria-live="polite"`. |
| `FormValidation` | Inline errors below invalid fields. Name: "Agent name is required." Voice: warning (non-blocking) "A voice description improves agent consistency." Lists: warning if both empty "Add at least one Do or Do-Not item for best results." |

### States

| State | Description |
|-------|-------------|
| `empty` | New agent with no soul fields. All inputs blank. DoList and DoNotList show one placeholder row each. |
| `populated` | Editing an existing agent. Fields pre-filled from PATCH response or GET /agents/{id}. |
| `saving` | PATCH in flight. SaveButton shows spinner. Fields remain editable (optimistic). |
| `saved` | PATCH 200. SaveButton shows "Saved" with checkmark for 2 seconds, then resets to "Save Soul". |
| `error` | PATCH non-2xx. Error banner above SaveButton: "Save failed. Check your connection and try again." |

### Validation Rules

- **Name**: Required. Block save if empty. Error: "Agent name is required."
- **Role**: Required (has a default — first select option). No block.
- **Voice & Tone**: Not required. Warning (non-blocking) if blank: "A voice description improves agent consistency."
- **Do list**: Not required. Warning (non-blocking) if both Do and Do-Not lists are empty.
- **Do-Not list**: Not required. Same warning as Do list.
- **List items**: Individual items cannot be blank strings — empty item rows are stripped on save before PATCH.

### Accessibility

- All `<input>`, `<select>`, `<textarea>` elements have explicit `<label>` elements (not placeholder-only labeling).
- DoList and DoNotList: when "Add item" is clicked, focus moves to the newly created `<input>` immediately.
- When a list item is removed, focus moves to the "Add item" button for that list.
- SaveButton in loading state: `aria-busy="true"` and `aria-label="Saving..."`.
- Live preview panel (if implemented): `aria-live="polite"` so screen readers announce updates without interruption.
- Form submission feedback announced via `role="status"` region (saved / error message).

---

## Surface 3: Demo Site

### Purpose

The demo site is the hireable artifact entry point. An AI/ML hiring manager lands on this page and within 10 seconds must understand: what Veridian is, that it is working live, and that they can interact with it. It is a static HTML file (`apps/demo/index.html` or `scripts/demo_m4.html`) with no backend.

### Layout

```
[Nav Bar]
  Logo "Veri[dian]" + nav links (Demo / Docs / Blog) + "View on GitHub ↗" CTA

[Hero Section — split grid: left text | right widget]
  Left: badge + headline + subheadline + CTA buttons
  Right: widget embed (380×520px card)

[Metrics Strip]
  3-column bordered grid: "100%" Grounded | "<20kb" Bundle | "SSE" Streaming

[Trust Strip]
  3-column grid with icon boxes + text

[Footer]
  "veridian/m4 · portfolio demo · {author}" + GitHub / UI-SPEC / Contact links
```

Nav bar: `--surface-2` background, 60px height, space-between. Logo: "Veri" + `--accent` "dian", weight 800. CTA: `--accent` fill button.

Hero: `background: --bg`, `grid-template-columns: 1fr 420px`, gap 56px, padding 72px 48px. Widget card in hero: 380×520px (not 600 — the demo uses a shorter card to fit the hero layout). Widget iframe embed in production code should link to the live widget endpoint.

Metrics strip: `border-top: 1px solid --border-soft`, `grid-template-columns: repeat(3, 1fr)`. Each cell: padding 32px 40px, right border. Value: 34px weight 800 `--accent`. Label: 13px `--text-3`.

Trust strip: `background: --bg`, 40px 48px padding, 3-column grid. Icon box: 40×40px `--accent-dim` fill, 1px rgba border, `--radius-xs`. Icon: 18px inline SVG `--accent`.

### Content Requirements

- **Headline**: "Ask anything about Bella Vista Coffee" (or equivalent real demo business)
- **Subheadline**: "Powered by Veridian — a grounded AI agent that cites its sources"
- **Widget embed**: `<iframe src="http://localhost:8001/widget.html?agent_id={id}" width="380" height="600">` (real Veridian agent)
- **Trust signals** (minimum one): "Answers grounded in your documents", "Cites sources for every response", "Escalates to a human when needed"
- **AI disclosure**: Widget itself shows "Powered by AI". Demo page may repeat it in the trust strip.
- **CTA for hiring managers**: GitHub repository link or "View the code" link

---

## Design System

**Selected: Parchment & Wine (Design G)** — locked. Do not implement Obsidian, Sandstone, or Spectrum.

Design G is a hybrid: IBM-Carbon/Design D enterprise structure (Inter font system, horizontal tabs, left-rule bubbles, rectangular geometry) dressed in Design F's warm alabaster backgrounds, deep burgundy accent (`#7B1C3A`), and antique gold secondary (`#B8860B`). The full visual reference is `.planning/phases/04-reasoning-engine-widget/design-g.html`.

Key decisions locked by Design G:
- **Widget accent**: `#7B1C3A` wine (not indigo, not violet, not terracotta)
- **Bubble style**: white card with 3px left wine border (not rounded pill, not gradient header)
- **User bubble**: rectangular (`--radius-sm` = 14px), wine fill
- **Citation**: bordered card row with left wine rule + View action (not inline muted text)
- **Disclosure bar**: left gold rule + "Powered by AI" + right "veridian v0" monospace tag
- **Agent header**: name label + AGENT badge pill above every agent bubble
- **Demo background**: `#FDF9F5` alabaster (not dark hero, not white)

**Font rule:**
- Widget bundle: `system-ui` only — no Google Fonts (bundle constraint).
- Admin and demo pages: load Inter + JetBrains Mono via Google Fonts CDN.

---

## Implementation Notes

### Widget (apps/widget/ — Preact)

- Build toolchain: Vite 5.x with `@preact/preset-vite`
- All CSS in a single bundled stylesheet or as Preact-scoped style tags — no external CSS CDN
- Font: `system-ui` only in widget — no Google Fonts import in the bundle (violates ≤20kb constraint)
- CSS custom properties set at `:host` (inside iframe body) from theming config received from `/widget/{agent_id}/config`
- Theming config format (from API): `{"primary_color": "#7B1C3A", "font_family": "system-ui", "border_radius": "14px"}` — maps to CSS custom property overrides
- SSE via native `EventSource` — no polyfill needed (all modern browsers)
- JWT stored as a JS module-scoped `let` variable — not in DOM, not in storage
- JWT refresh: on `EventSource` error or 401 from POST, re-fetch `/widget/{agent_id}/config` to get fresh JWT
- Send button: minimum 44×44px (WCAG 2.5.5) — do not reduce below this
- Inline SVG for all icons: send arrow, doc icon for citation, typing dots are CSS-only
- Avoid: lodash, date-fns, axios, any npm package with transitive deps that add >2kb gzipped

### Soul Editor (apps/admin/ — Next.js)

- `apps/admin/` does not exist yet — create with `npx create-next-app@latest apps/admin --typescript --tailwind --app`
- For M4 scope, a single page suffices: `app/agents/[id]/soul/page.tsx`
- Font: load Inter + JetBrains Mono via Google Fonts in `layout.tsx` (admin only — not in widget bundle)
- Use `fetch` with `X-API-Key` header — no separate auth system
- `PATCH /agents/{id}` body: `{"soul_voice": "...", "soul_role": "...", "soul_do_list": [...], "soul_donot_list": [...]}`
- Live preview panel: controlled `<pre>` that re-renders on every field change using `build_system_prompt` logic in TypeScript. Syntax: keys in `--accent`, string values in `--amber`, comments in `--text-4`
- Do/don't dynamic lists: `useState<string[]>` arrays; add appends empty string and focuses new input via `useRef` + `useEffect`; empty items stripped before PATCH
- Tab navigation: render all 5 tabs visually but only Soul tab is interactive in M4; others should not throw errors (no-op clicks)
- Tailwind is acceptable in the admin — it is NOT bundled into the widget
- shadcn is not initialized for this project — use raw Tailwind or Design G's CSS patterns for M4 admin
- CSS tokens: define `:root` variables matching Design G's token names in `globals.css`; components reference only the variables

### Demo Site (apps/demo/index.html or scripts/demo_m4.html)

- Static HTML + inline CSS — no build step required
- Google Fonts CDN: load Inter + JetBrains Mono (demo page only, not widget bundle)
- Use Design G's CSS token names (`--accent`, `--bg`, `--surface-1`, etc.) for consistency with admin
- Widget card in demo hero: 380×520px (shorter than the standalone widget's 600px — fits hero layout)
- The iframe embed in production uses: `<iframe src="http://localhost:8001/widget.html?agent_id={id}" width="380" height="600">` — the card wrapper clips it to 520px for the demo layout
- Demo includes: nav bar + hero + metrics strip (100% / <20kb / SSE) + trust strip + footer
- Footer copy: "veridian/m4 · portfolio demo · {author name}" + GitHub / UI-SPEC / Contact links
- The demo page doubles as the portfolio artifact entry — "View on GitHub ↗" CTA in nav and hero
- No JavaScript required on the demo page itself — widget handles all interactivity

### Spacing Scale

All spacing values in both surfaces follow the 8pt grid: 4, 8, 16, 24, 32, 48, 64px. No exceptions. Touch targets: send button and escalation submit button minimum 44×44px per WCAG 2.5.5. List add/remove buttons minimum 44px in the tap axis. The send button in design-g.html shows 36×36px — **this must be corrected to 44×44px in the implementation**.

### Copywriting Contract

| Element | Copy |
|---------|------|
| Widget disclosure | "Powered by AI" |
| Widget empty state | "Hi, I'm {agent_name}. Ask me anything." |
| Widget thinking label | "Looking that up..." (optional, design-dependent) |
| Widget error | "Something went wrong. Please try again." |
| Widget escalation | "I've flagged this for our team. Expect a reply within 24 hours." |
| Escalation form CTA | "Send my details" |
| Escalation confirmation | "Got it — our team will be in touch." |
| Send button | (icon only, aria-label: "Send message") |
| Soul editor page title | "Agent Soul" |
| Soul editor save CTA | "Save Soul" |
| Soul editor saved state | "Saved" |
| Soul editor error | "Save failed. Check your connection and try again." |
| Soul editor voice warning | "A voice description improves agent consistency." |
| Soul editor list warning | "Add at least one Do or Do-Not item for best results." |
| Demo page headline | "Ask anything about Bella Vista Coffee" |
| Demo page subheadline | "Powered by Veridian — a grounded AI agent that cites its sources" |

---

*Design contract for Phase 04. Checker can now validate against 6 design quality dimensions.*
