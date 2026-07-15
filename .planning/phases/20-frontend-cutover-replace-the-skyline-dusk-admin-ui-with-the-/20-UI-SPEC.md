---
phase: 20
slug: frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-gotham-console
status: draft
shadcn_initialized: false
preset: none
created: 2026-07-15
---

# Phase 20 — UI Design Contract: the Gotham console

> Visual and interaction contract for the frontend cutover of `apps/admin`. This is **not a fresh design exercise** — the design is already made. `prototypes/gotham/` (11 static HTML pages + `tokens.css` + `app.css` + `scene.js` + `MESH.md`) is the canonical, locked source of truth (`.planning/phases/20-.../20-CONTEXT.md`, "Decisions → Design system"). This document formalizes that prototype into a contract an implementer can build against without opening the HTML files, and flags every place the prototype itself is inconsistent, silent, or in conflict with its own stated law so the planner/executor do not silently inherit a bug.

**Read this file plus the six documents it cites before planning tasks.** Wherever this spec says "port exactly," the prototype file + line reference is given.

---

## 0. Non-negotiables carried over from CONTEXT.md

1. Pure re-skin + re-IA of `apps/admin`. No new backend, table, or endpoint (Phase 21's job).
2. The provisioning flow (create → provision tenant DB → ingest → deploy) and every live endpoint the dusk pages already call must not regress. See §9 "Endpoint preservation map."
3. Routed Next.js pages, not `console.html`'s single-surface fold. `console.html` is **not shipped** in Phase 20 (§7).
4. three.js confined to landing (`/`) and auth (`/sign-in`, `/sign-up`) only.
5. "Colour is a verdict": green = pass, red = fail/gate-shut are the **only** hues in the console chrome. Eval channels are bone-by-luminance. Colour is never decoration.
6. Product copy is neutral/literal. Never explain the design's own metaphor to the user (MESH.md's "gate," "bench," "instrument" language is internal design vocabulary — it must not leak into UI copy beyond what the prototypes themselves already put in front of the user, e.g. "The gate is open").

---

## 1. Design System

| Property | Value |
|----------|-------|
| Tool | **none** — bespoke CSS custom-property token system, ported verbatim from `prototypes/gotham/tokens.css` + `app.css`. No component library (no shadcn/radix). This is an explicit continuation of the project's existing approach (dusk was also bespoke CSS, not shadcn) and is locked by CONTEXT.md ("port these; do not redesign") — introducing shadcn now would itself be a redesign decision outside this phase's scope. |
| Component library | none (hand-built components matching `app.css` class contracts: `.zone`, `.chip`, `.ledger`, `.btn`, `.command`, `.tile`, etc.) |
| Icon library | Inline hand-authored SVG (stroke-based, 14–24px viewBoxes), exactly as in the prototypes. `lucide-react` is present in `package.json` but is **not** used by any Gotham prototype page — do not substitute lucide icons for the prototype's bespoke strokes; the nav-rail glyphs, checkmarks, and doc icons are load-bearing design signatures (ORRERY "engraved plate" language). Port the inline `<svg>` markup as React components (`components/gotham/icons/*.tsx`). |
| Font | `Space Grotesk` (display), `Inter` (sans/body), `JetBrains Mono` (mono/numerals), `Newsreader` italic (the judge's voice). Loaded exactly as in every prototype `<head>`: `https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&family=Newsreader:ital,opsz,wght@0,6..72,400;1,6..72,400;1,6..72,500&family=Space+Grotesk:wght@500;600;700&display=swap`. **Recommendation (Claude's discretion, not in CONTEXT.md):** self-host via `next/font/google` instead of the Google Fonts `<link>` for CSP/perf reasons — same four families/weights, no visual change. Flag this as a planner decision, not a default; if self-hosting is skipped, port the `<link>` verbatim into `layout.tsx`. |

**Registry Safety:** not applicable — no shadcn, no component registry, no third-party blocks. See §11.

---

## 2. Design Tokens — full port of `prototypes/gotham/tokens.css`

This is the literal content of UI2-01. Port every custom property below into `apps/admin/app/globals.css`, replacing the existing dusk `:root` block (glass tiers, sunset-coral accent, jacaranda lilac, tower cyan, building amber — all retired, see §10 anti-pattern 2).

### 2.1 Surfaces (graphite, chroma zero)

| Token | Value | Role |
|---|---|---|
| `--bg` | `#0E1012` | the bench — page background, dominant surface (~60%) |
| `--surface` | `#15181B` | whisper-zone — `.zone` panels, cards, nav rail hover |
| `--surface-2` | `#1E2327` | raised — inputs, hover rows, `.chip-mute` |
| `--well` | `#08090B` | sunken — code/log blocks, `input`/`select`/`textarea` background |

### 2.2 Hairlines (engraved, never drawn as boxes)

| Token | Value |
|---|---|
| `--hairline` | `rgba(231, 229, 225, 0.13)` |
| `--hairline-soft` | `rgba(231, 229, 225, 0.06)` |
| `--hairline-strong` | `rgba(231, 229, 225, 0.30)` |

### 2.3 Bone lettering

| Token | Value | Role |
|---|---|---|
| `--ink` | `#E7E5E1` | primary text |
| `--ink-2` | `#9BA1A3` | secondary text (sub-copy, muted values) |
| `--ink-3` | `#6B7275` | tertiary text (micro-labels, timestamps, placeholders) |

### 2.4 LIVE — brightness, not a hue (the ~10% "accent" role)

| Token | Value | Role |
|---|---|---|
| `--live` | `#E7E5E1` | default live state — same value as `--ink` in the open-gate state |
| `--live-hot` | `#FFFFFF` | hot live — hover/emphasis on a live element |
| `--live-dim` | `rgba(231, 229, 225, 0.10)` | live-tinted fill (active nav icon bg, `.chip-live` bg) |
| `--live-ink` | `#0E1012` | text/graphite ON a bone fill (e.g. text inside `.btn-primary`) |

### 2.5 THE TWO VERDICT COLOURS — the only hues in the system

| Token | Value | Role |
|---|---|---|
| `--pass` | `#4CC38A` | verdict: held/passed |
| `--pass-dim` | `rgba(76, 195, 138, 0.13)` | pass chip/badge background |
| `--fail` | `#E5484D` | verdict: failed (also breach colour for a threshold-crossing metric) |
| `--fail-dim` | `rgba(229, 72, 77, 0.13)` | fail chip/badge background |
| `--seal` | `#E5484D` | the gate-shut seal — same red as `--fail`; it is the same claim |
| `--seal-hot` | `#FF6369` | seal hover/emphasis |
| `--seal-dim` | `rgba(229, 72, 77, 0.13)` | seal chip/badge background |

### 2.6 The four eval channels — bone by luminance, NOT four hues

| Token | Value | Channel (fixed mapping, do not reorder) |
|---|---|---|
| `--ch-1` | `#E7E5E1` | Faithfulness — brightest, matters most |
| `--ch-2` | `#A9AFB1` | Answer relevancy |
| `--ch-3` | `#7C8386` | Context recall |
| `--ch-4` | `#565C5F` | Context precision |

**These four values are the only permitted colours for the eval telemetry chart.** See §10 anti-pattern 1 — `eval.html` itself violates this with four literal hex hues; the Next.js port must use `--ch-1..4` instead, not the prototype's literal SVG stroke colours.

### 2.7 Type

| Token | Stack |
|---|---|
| `--display` | `'Space Grotesk', system-ui, sans-serif` |
| `--sans` | `'Inter', system-ui, sans-serif` |
| `--voice` | `'Newsreader', Georgia, serif` (always italic — the judge's hand, CHORUS) |
| `--mono` | `'JetBrains Mono', ui-monospace, monospace` (every number, always tabular) |

### 2.8 Shape

| Token | Value | Role |
|---|---|---|
| `--r-control` | `4px` | inputs, buttons, small controls |
| `--r-panel` | `6px` | `.zone`, cards, overlays |
| `--r-pill` | `100px` | chips |

### 2.9 Z-index scale

`--z-grid: 0; --z-bench: 1; --z-rail: 10; --z-strip: 20; --z-gate: 25; --z-sheet: 30; --z-toast: 40;` — port verbatim.

### 2.10 THE GATE — `data-gate="blocked"` root attribute

Port this block **exactly**, qualified with `:root` (specificity requirement — a bare attribute selector loses to the base `:root` block otherwise):

```css
:root[data-gate="blocked"] {
  --live:            var(--seal);
  --live-hot:        var(--seal-hot);
  --live-dim:        var(--seal-dim);
  --live-ink:        #0E1012;
  --hairline:        rgba(229, 72, 77, 0.24);
  --hairline-soft:   rgba(229, 72, 77, 0.10);
  --hairline-strong: rgba(229, 72, 77, 0.48);
}
```

This is the entire mechanism. One attribute on `<html>` (or the Next.js root layout element) repaints every hairline, every `--live`-derived colour, and (via `window.gotham.setGate()`) the three.js specimen, off this single flip. No component may hand-colour itself red on a critical finding — it sets `data-gate="blocked"` and lets the cascade do it. See §8.

### 2.11 Tint transition (the room changes temperature, it never cuts)

```css
.tint,
.tint *:not(svg):not(path):not(circle):not(line):not(polyline) {
  transition: background-color 600ms ease, border-color 600ms ease,
              color 600ms ease, box-shadow 600ms ease;
}
@media (prefers-reduced-motion: reduce) {
  .tint, .tint * { transition-duration: 1ms; }
}
```

Apply `.tint` to `<body>` (or the root layout wrapper) on every routed page. See §8.2 for the reduced-motion contract in full.

### 2.12 Spacing — documented exception to the generic 8pt scale

The prototypes use a **hand-tuned instrument-panel spacing system**, not a strict 4/8px grid: observed values include `4, 6, 8, 9, 10, 11, 12, 13, 14, 16, 18, 20, 22, 24, 26, 30, 34, 40, 44, 48, 56, 60, 72px`. This is intentional fidelity to a locked source design (CONTEXT.md: "port, do not redesign") and is a **documented, approved exception** to the standard 8-point spacing scale a fresh build would use. Do not normalize these values to the nearest 4px multiple — that would be an uninstructed redesign. Component-by-component spacing is specified in §6 per element, matching `app.css` / each page's inline `<style>` block exactly.

The one place a genuine 8pt-scale reference is useful is **new** chrome this phase must build that has no prototype precedent (e.g. Next.js loading skeletons, toast positioning if introduced) — for anything with no prototype precedent, default to multiples of 4 (`4/8/16/24/32/48/64`).

---

## 3. Typography — as used, not force-fit to "3–4 sizes"

Same exception logic as §2.12 applies: this is an instrument-panel type system with many functional micro-sizes (labels, mono readouts, thresholds) that a generic app would not need. Documented in full so the planner does not truncate it.

| Role | Size | Weight | Family | Line height | Where |
|---|---|---|---|---|---|
| Micro-label (tracked mono) | 9–11px | 700 | mono | 1.2, `letter-spacing: 0.16–0.22em`, uppercase | `.label`, chip text, channel names |
| Body | 13–14px | 400 | sans | 1.55 | `body`, paragraph copy, table cells |
| Body emphasis / buttons | 12.5–13.5px | 600 | sans | 1.3 | `.btn`, segmented control labels |
| Numerals (readouts) | 11–19px | 400 (tabular via `font-variant-numeric: tabular-nums` + `font-feature-settings: "tnum" 1, "zero" 1`) | mono | 1.1–1.2 | `.num`, `.mono`, every metric value |
| Sub-copy | 13–15.5px | 400 | sans | 1.5 | `.sub`, `.help` |
| Section heading (h2) | 16–22px | 500 | display | 1.1–1.2, `letter-spacing: -0.02em` | `.page-head h1`, `.step h3`, `h2` |
| Hero heading (h1, landing only) | `clamp(26px, 3.4vw, 44px)` | 500 | display | 1.08, `text-wrap: balance` | `index.html .hero h1` |
| The judge / voice | 14.5–17.5px | 400 italic | `Newsreader` (voice) | 1.62 | `.voice` — CHORUS typeset verdicts, always italic serif, the only serif in the product |

**Weight discipline:** 400 (regular, body/mono/voice) and 600 (semibold, buttons/emphasis/labels) are the two functional weights per the generic UI-SPEC convention; `Space Grotesk` headings pin to their own 500 as a display-specific, source-locked exception (not a third arbitrary weight introduced by this phase).

---

## 4. Color — "colour is a verdict"

| Role | Value | Usage |
|------|-------|-------|
| Dominant (~60%) | `--bg` `#0E1012` | page background, the bench |
| Secondary (~30%) | `--surface` `#15181B` / `--surface-2` `#1E2327` | `.zone` cards, nav rail, raised rows, inputs |
| Accent — "live" (~10%, brightness not hue) | `--live` `#E7E5E1` → `--live-hot` `#FFFFFF` on hover | **reserved for:** primary CTA (`.btn-primary`) fill+text, active nav-rail icon (`aria-current="page"`), all `:focus-visible` outlines, `.chip-live` + `.dot-live` (breathing pulse), the `.command` input caret/typing indicator, range-slider thumb + track fill (soul dials), `.zone[data-live="true"]` border glow (the live agent card), non-failing points-of-light in the three.js specimen and the ingest chunk swarm, `.frame-mark[data-grade="filed"]` outline+tick |
| Destructive / verdict-fail | `--fail` / `--seal` `#E5484D` → `--seal-hot` `#FF6369` | **reserved for:** the `data-gate="blocked"` room-wide repaint (root mechanism, §2.10), `.chip-fail` / `.chip-seal`, `.btn-seal` (destructive actions: "Simulate a critical finding," "Delete agent," "Delete permanently"), breached-threshold numerals/trace-lines (`data-breach="true"`, `data-failing="true"`), the critical-finding banner (`.critical`), `glyph-fail` icon, `.danger-status[data-armed="true"]`, the `.item-x` (remove-rule) hover state |
| Pass verdict (second semantic colour) | `--pass` `#4CC38A` | **reserved for:** `.chip-pass`, `glyph-pass` icon, passing-threshold table cells. This is the only other hue in the system besides fail-red — it is a verdict, not a decoration or a brand colour, and is never used outside a table/chip verdict context. |
| Eval channels | `--ch-1..4` (bone luminance only — §2.6) | the four ragas metric traces/numerals/chips on `eval.html`'s telemetry chart and any other four-channel readout. **Not** four hues — see §10 anti-pattern 1. |

Accent reserved for: the explicit list above — never "all interactive elements." Ghost buttons (`.btn-ghost`), muted chips (`.chip-mute`), and default table/label text stay in the bone-neutral ramp (`--ink` / `--ink-2` / `--ink-3`) and never receive `--live`.

**Widget preview exception (deploy.html / agent operations room):** the customer-facing widget preview (`.widget`, `.launcher`, `.w-*` classes in `deploy.html`) is explicitly and intentionally exempt from the console's dark bone/graphite palette. It hardcodes a light theme (`#F6F5F1` background family, `#12181A` text, avatar/launcher accent `#C79A3C`). MESH.md law 5: *"No emoji in the console. The customer widget may smile; the operator may not."* This is a deliberate second, small, light-mode palette for a preview-only surface and must **not** repaint on `data-gate="blocked"` — the gate must never repaint what the customer sees (`deploy.html` comment, line ~92–94). **Judgment call flagged for the planner:** the `#C79A3C` widget-accent hex is literally the retired "brass" gold from the old MESH.md palette (§10 anti-pattern 3) coincidentally reused here as the widget's own brand colour. Treat it as an intentional, standalone `--widget-accent: #C79A3C` token scoped only to the widget-preview component — do not resolve it from `--live`/`--ch-*`, and do not let it leak into console chrome. If the actual Preact widget (a separate package, out of this phase's scope) has a different real brand colour, this preview should match that widget's real theme instead of the prototype's placeholder — flag to the user/planner to confirm against the actual widget source if it exists.

---

## 5. Global Shell

### 5.1 Two shell variants (no third variant)

**A. Bare / topnav shell** — used only by the landing page (`index.html`). No fixed rail. A `.topnav` (`display:flex`, `border-bottom: 1px solid var(--hairline)`, `padding: 17px 40px`, `max-width: 1280px`, centred) with: wordmark (`w.chats`, the "w." in `--live`), nav links (`Docs`, `Pricing` — placeholder targets, keep as-is or point at real routes if they exist, otherwise `#`), and a single `.btn-primary` "Open the console" linking to `/agents`.

**B. Console / rail shell** — used by every other authenticated page (`/agents`, `/agents/new`, `/agents/[id]` and all its sub-routes). A fixed 56px left rail (`.rail`, `position: fixed; left:0; top:0; bottom:0; width:56px; border-right: 1px solid var(--hairline)`), containing:
- `.rail-mark` — the "w" wordmark glyph, links to `/` (home), 26×26px, `--live-ink` on `--live` (70% mix) background.
- Four or five `.rail-btn` icon links (36×36px, `text-decoration:none`): **Agents**, **Ingest**, **Eval**, **Deploy**, and (bottom-anchored via `.spacer` flex push) **Settings** on the operations-room pages. Note: `agents.html`/`agent-new.html` only show Agents/Ingest/Eval/Deploy (4 icons, no Settings, no bottom anchor); `agent.html`/`soul.html`/`ingest.html`/`eval.html`/`deploy.html`/`settings.html` show the same four **plus** Settings pushed to the bottom via `.spacer`. Port both variants exactly as the prototypes do — do not force Settings into every rail state.
- Active route gets `aria-current="page"` → `--live` icon colour + `--live-dim` background.
- Content wrapper gets `.deck { padding-left: 56px; }` so nothing scrolls under the rail.
- Responsive: at `max-width: 900px` the rail rotates to a bottom horizontal bar (`flex-direction: row; inset: auto 0 0 0; height: 56px; border-top` instead of `border-right`), and `.deck` gets `padding-left:0; padding-bottom:56px`.

### 5.2 Background treatment (every page)

Fixed, non-scrolling decorative layers, all `pointer-events: none`, `aria-hidden="true"`, in this stacking order (lowest z first):
1. `.graticule` — a 44×44px hairline grid (`linear-gradient` both axes at `--hairline-soft`), radial mask fading toward the edges, `opacity: 0.5`.
2. `.bloom` — a soft radial glow at `52% 48% at 50% 34%` using `color-mix(in oklch, var(--live) 8%, transparent)` — this is the ambient "pool of light," and it inherits the gate flip automatically because it reads `--live`.
3. Four `.cross` registration crosshairs (9×9px hairline crosses) pinned to the four corners of the content area — exact offsets differ per page shell (landing uses `22px` all corners; rail pages use `78px`/`70px` left to clear the rail, collapsing to `16px`/`22px` under the rail's mobile breakpoint). Port the per-page offset values from each prototype's `<style>` block.

None of these three layers may ever be replaced by a decorative illustration, gradient mesh, or brand mark — they are the entire "AERIAL" background language (MESH.md: "the console is one brass graticule. No cards floating on it, only whisper-zones").

### 5.3 three.js specimen — confined to landing + auth only

`scene.js` exposes `window.gotham` (state: `{ gate: 'open'|'blocked', soul: {warmth, rigor, candor} }`, methods `setGate()`, `setSoul()`) and `window.mountGotham(hostEl, { scenarios, fails })`, which:
- Dynamically imports `three@0.161.0` from a CDN (`https://unpkg.com/three@0.161.0/build/three.module.js`) inside a `try/catch` — this was necessary for the static prototype (opened over `file://`, where local ES module imports are CORS-blocked). **Judgment call flagged for the planner:** in the Next.js build, prefer installing `three` as a real npm dependency (not currently in `apps/admin/package.json` — must be added) and importing it normally; keep the same `try/catch`-degrades-to-CSS-bloom behavior for network/WebGL failure, but the reason for the CDN import (file:// CORS) no longer applies once this is server-built. This is not specified by CONTEXT.md and is left to the planner to decide; either approach satisfies the design law, only the import mechanism differs.
- Renders four draw calls: the agent core (custom shader, `IcosahedronGeometry`, warmth/rigor/candor uniforms), the armature cage (wireframe icosahedron), two armillary rings, and a `Points` cloud of scenario dots (bone, oxblood if failing, fibonacci-sphere distributed).
- On `gate` change, lerps every colour uniform from bone → oxblood over the same visual language as the CSS `data-gate` flip, driven by its own internal easing (not the CSS 600ms transition — it's `gateT += (targetGate - gateT) * Math.min(1, dt * 3.4)` per frame, i.e. framerate-driven convergence, not a CSS transition).
- Respects `prefers-reduced-motion`: slows time accumulation to 0.2× and reduces spin, but does not fully freeze (a spinning specimen at reduced rate is judged acceptable motion by the prototype's own author — do not "fix" this to a full freeze without a product decision, since CONTEXT.md's reduced-motion requirement is scoped to "skip the gate-shutter repaint and row fades," not the specimen).

**Confinement rule (design law, non-negotiable):** `mountGotham()` may only be called from `/` (landing) and the two Clerk auth routes (`/sign-in`, `/sign-up`). It must **not** be mounted anywhere under `/agents/**` — `soul.html`'s temperament preview (`#scene`, 168×168px) is the one place inside the prototypes that mounts it on an authenticated page, and per CONTEXT.md this is explicitly against the design law locked for this phase ("three.js confined to landing + auth only"). **This is a direct conflict between the prototype file (`soul.html`) and the locked CONTEXT.md decision.** Resolution (not optional, follow CONTEXT.md): on the ported `/agents/[id]/soul` page, the temperament dials' live-reshaping preview must be replaced with a **non-WebGL fallback** — reuse the same `.bloom`-only CSS treatment the specimen degrades to when WebGL is unavailable, or a static/CSS-only visualization of the three warmth/rigor/candor values (e.g. three animated CSS-only readouts). Do not mount `window.mountGotham` on the soul page. Flag this explicitly to the planner as a scoped deviation from the literal prototype file, required by the higher-priority CONTEXT.md law.

---

## 6. Per-Route Component Specs

Layout container for every routed console page: `<div class="page">` (`max-width: 1280px; padding: 34px 40px 72px`, `24px 20px 48px` under 900px) inside `.deck`. Provisioning uses a narrower `.page.prov` (`max-width: 1060px`). Settings uses `.page` at `max-width: 760px`.

### 6.1 Landing — `/` (replaces current `apps/admin/app/page.tsx`)

**Source:** `index.html`. **Shell:** A (topnav). **three.js:** yes, `#scene` host, `{ scenarios: 64, fails: 3 }`.

| Section | Content | States |
|---|---|---|
| Hero | h1 (2-line max, `text-wrap:balance`), lede paragraph, two CTAs (`Build your agent` → `/agents`, `See the gate` → `#gate` anchor), a `dl.stats` row (3 stat tiles: Agents verified / Median faithfulness / Critical findings shipped) | Static marketing copy — the 248+/0.91/0 numbers are demo placeholders; **flag to planner:** decide whether these become live aggregate stats (needs a public metrics endpoint — none exists per AGENT-MGMT-GAPS.md) or stay as static launch copy. Default to static copy since no backend exists; do not invent an endpoint. |
| The check | the three.js specimen + legend (`61 passing` / `3 failing` dots) | Same specimen degrade-to-bloom rule as §5.3 |
| The evidence | a 5-row `.ledger` table of example eval scenarios + a `.voice` line about filed failures | Static illustrative content, not tied to a real agent |
| The gate | live-updating demo: `.gate-line` h2, `.chip` gate state, a signal-readiness `.ledger` (4 rows), two demo buttons "Simulate a critical finding" / "Clear the finding" that toggle `data-gate` on `<html>` **client-side only, no backend call** | This is a marketing demo of the gate mechanic, not tied to a real agent — keep it client-only exactly as ported; do not wire it to a real agent's data |
| Three steps | a `rule-double` 3-column `.steps` grid (Ingest/Evaluate/Deploy) | static |
| Footer | `.foot` — two mono spans | static; drop "prototype" wording (`The Gotham console · prototype`) — replace with real product footer copy, e.g. current year + company line, since "prototype" must not ship |

### 6.2 Agents dashboard — `/agents` (replaces `apps/admin/app/agents/page.tsx`)

**Source:** `agents.html`. **Shell:** B (rail, active=Agents). **three.js:** no.

- `.page-head` with h1 "Agents," a `.sub` line, and `.btn-primary` "New agent" → `/agents/new`.
- **Command strip** (`.command`, MERIDIAN pattern): a permanent, always-visible text input with `/` keyboard shortcut to focus, `Enter` to submit, reply rendered in-place below (`.cmd-out`). **Judgment call flagged for the planner (no CONTEXT.md answer, no backend exists):** the prototype's command strip fakes NL command dispatch via keyword string-matching (`indexOf('eval')`, `indexOf('ingest')`, etc.) with hardcoded reply text — this is decoration in a functional slot if shipped as-is (violates the anti-pattern rule in §10) because it looks like it dispatches real commands but does not. **Do not port the fake dispatch logic.** Two compliant options: (a) omit the command strip from Phase 20 entirely and reintroduce it once Phase 21 or a later phase provides a real command-dispatch endpoint, or (b) ship the input chrome only, keyboard-focusable, but disabled with placeholder copy such as "Command dispatch is not available yet" and no submit handler. Recommend (a) — cut it — since a disabled-but-visible input closer resembles "explaining the metaphor" than a clean honest-empty-state. Flag this choice to the user/planner; do not silently pick one.
- **Agent grid** (`.agents`, 3-col → 2-col ≤1000px → 1-col ≤700px): one `.zone.card` per agent from `GET /api/v1/agents`. Each card: name (h3) + mono id, chips (`chip-live`/`chip-mute`/`chip-seal` per status — map backend agent status to Live/Testing/Building + a Gate-shut chip when applicable), a `.hair` divider that turns oxblood when `data-shut="true"`, a 3-col `.metrics` row (Docs / Pass rate / Sessions — pass rate shows `pending` in muted mono if no eval has run), a `.card-foot` (created date + "Open →" affordance). **Anti-pattern gate (§10.4):** `.card-open` uses `::after { position:absolute; inset:0 }` to make the whole zone clickable while keeping exactly one real `<a>` in the tree — do not nest a second `<a>` (e.g. the card name) inside the card; if the name needs to be a link too, make it a `<span>`/non-interactive element and rely on the stretched `.card-open` anchor, exactly as the prototype does.
- Empty state (no agents yet): not shown in the prototype — **flag to planner:** author empty-state copy per the copywriting contract (§8 in the generic template sense — see the Copywriting Contract table below) since the prototype has no zero-agents example.

### 6.3 Provisioning — `/agents/new` (replaces `apps/admin/app/agents/new/page.tsx`)

**Source:** `agent-new.html`. **Shell:** B (rail, no active item shown in prototype — it's reached via "New agent," treat Agents as the active rail icon since there's no dedicated provisioning icon). **three.js:** no. **Colour law:** *this page has zero verdict colour, on purpose* — "there is nothing to gate... not one pixel of it" (`agent-new.html` header comment). Do not introduce `--live`/`--pass`/`--fail` anywhere on this page except focus-visible outlines and the segmented-tone-control's selected state (which uses `--live` per `app.css .seg input:checked + label`).

- **Stepper** (`.stepper`, 4 `.station`s on one hairline rule, not cards): Provision (active) → Configure (locked) → Test (locked) → Deploy (locked). Steps 2–4 show a small lock glyph and `opacity: 0.62` (`data-locked="true"`). **This directly implements UI2-04's "steps 2–4 locked until step 1 completes."** Reuse the existing `apps/admin/app/components/JourneyStepper.tsx` logic (`deriveStepState`) — it already encodes this exact gating (`locked`/`active`/`done` per step) for the dusk build; restyle it to the `.stepper`/`.station` visual contract instead of rewriting the state machine.
- **Two-column build area** (`.build`, 560px form + 240–300px cold instruments, single column ≤960px):
  - Left: the create form (name, business-type select, tone segmented control), a `.voice` line, then `.actions` (Create agent / Cancel), then a live elapsed-time counter (`#elapsed`) and a `.done` block (agent-id + "Open the agent" CTA) that appears on success.
  - Right: "Instruments · no signal yet" — 4 dead-flat `.flatline` readouts (Faithfulness/Answer relevancy/Context recall/Context precision, each reading `--`) and an "On create" checklist (4 rows: provision tenant DB / create empty KB / draft soul / seed eval scenarios) that ticks each item as provisioning proceeds.
- **This is the exact provisioning flow that must not regress** — wire `Create agent` to the same `POST {apiBase}/me/provision` → `POST {apiBase}/api/v1/agents` sequence the current `apps/admin/app/agents/new/page.tsx` already calls (see §9). The prototype's `setTimeout`-staged checklist animation is a UI-only progress simulation; drive it from real request/response timing (or SSE if the backend emits provisioning progress events) instead of fixed `550ms` steps — do not fake completion state.
- Focus management: on submit, disable the fieldset, `aria-busy` on the submit button; on completion, move focus to the "Open the agent" link — port this a11y behavior verbatim.

### 6.4 Agent operations room — `/agents/[id]` (replaces `apps/admin/app/agents/[id]/page.tsx`)

**Source:** `agent.html`. **Shell:** B (rail, no active icon — none of the 4 nav icons represent "overview"; leave none active, or add an implicit "Agents" active state matching the current agent's parent). **three.js:** no (per §5.3 confinement law — `agent.html` itself does not mount the specimen; only `soul.html` incorrectly does).

Header: h1 (agent name) + `.sub` (role + "serving since"), an `.ident` block (Agent label, mono id, `.chip-live` "Serving" — status-driven), and a `.gatebar` (chip + message + "last verified" timestamp) that reads across the full width under a `rule-double`-style top border.

**Six regions, in this order, each an `.section`** (top border rule, `.section-head` with a `.label` + a mono meta line):

| # | Region (design name) | Backend status (from AGENT-MGMT-GAPS.md) | Phase 20 treatment |
|---|---|---|---|
| 1 | **Live** | PARTIAL/ABSENT — conversations/messages persist, but no aggregate metrics endpoint (CSAT, p95 latency, cost/session, containment, thumbs-down = not stored anywhere) | **Honest empty state.** Render the region shell (`.chans` grid, 7 channel slots: sessions, containment, escalation to human, csat, thumbs down, p95 latency, cost per session) with a single empty-state message in place of the live SVG traces — e.g. "Live performance metrics are not available yet." No fabricated numbers, no fake trace lines. Do not render the `<svg>` trace-drawing code path at all when there is no data; a flat/zero trace is still a fabricated value. |
| 2 | **Retrieval health** | ABSENT — no `retrieval_metrics` table, no recall/nDCG/MRR/reranker-lift/staleness computed anywhere | **Honest empty state.** Render the region shell (context-window bar, "what we put in it" bar, the 10-row readings `.ledger`) with an empty-state message, e.g. "Retrieval health instrumentation ships in a future release." Do not render the demo bars/table with the prototype's hardcoded numbers. |
| 3 | **The bench** (failure-triage) | ABSENT (UI-only) — no trace-listing endpoint, no grade endpoint, no promote-to-scenario | **Honest empty state.** Render the two-pane shell (`.sheet` contact list + `.enlarger` print-under-the-lamp) with an empty-state message, e.g. "No failing production traces to review yet." Do not render the 8 hardcoded demo traces. Keyboard/roving-listbox interaction pattern (arrow keys, P/H/X grade shortcuts) should still be documented here (§6.4.1) for Phase 21 to implement against once the endpoints exist — this spec's interaction contract stands even though the region ships empty in Phase 20. |
| 4 | **Judgement** (eval suite ledger) | **PARTIAL — real backend exists.** `eval_runs`/`eval_results`/`eval_scenarios` are queryable now (`GET .../eval-runs`), trigger exists. Coarse `source` enum (`generated`/`mined`/`production_failure`) exists; fine-grained origin/trace-id linkage and the born-in-production vs authored counts do not (Phase 21 OPS-12). | **Wire real data, partial empty state.** Render the scenario `.ledger` (Scenario / Origin / Added / Last verdict) from the real `eval_scenarios`/`eval_results` data using the existing `source` enum for the Origin column (map `production_failure`/`mined` → "trace …" style copy where a linkage exists, `generated` → "authored"). The 5-tile summary row (scenarios / held / failed / **born in production** / **authored**) — render "scenarios," "held," "failed" from real aggregate counts; render "born in production" and "authored" tiles as honest-empty (`--` or "not tracked yet") since OPS-12 hasn't shipped the provenance columns needed to compute them precisely. |
| 5 | **Adversary** (red team) | PARTIAL — `red_team_runs` persist with findings JSON + `max_severity` + `deployment_blocked`; list/get/trigger endpoints exist. Per-strategy coverage as first-class rows does not exist (Phase 21 OPS-13/14). | **Wire the summary, honest-empty the coverage table.** Render the severity tile row (`.sev`) and the critical-finding banner (`.critical`) from the real latest `red_team_runs` row (severity counts derived from the findings JSON, `deployment_blocked` drives the banner). Render "Run the programme" wired to the real trigger endpoint. The per-strategy coverage `.ledger` (Strategy / Probes / Coverage % / Open findings / Last run) has no backing rows — render it as an honest-empty state, e.g. "Per-strategy coverage detail ships in a future release; showing the latest run summary above." |
| 6 | **The prompt** (versions) | ABSENT — no `prompt_versions` table; `PATCH /agents/{id}` overwrites the soul in place, single version, no diff/canary/rollback | **Honest empty state.** Do not render the version list/diff/canary/rollback UI from the prototype (`.vers`, `.diff`, promote/rollback buttons) with fabricated version history. Show only the current live soul reference with a link to `/agents/[id]/soul` and an empty-state message, e.g. "Version history, canary releases and rollback ship in a future release." |

Region 1–3, 6 render **only** their shell + empty-state copy in Phase 20 — do not port any of the prototype's client-side mock-data generation (`mulberry32` seeded noise, the hardcoded `traces[]` array, the hardcoded `channels[]` values). Regions 4–5 wire to real endpoints and show partial-empty sub-elements as specified. This is the concrete meaning of ROADMAP UI2-05 ("honest empty states for the not-yet-backed regions").

**6.4.1 The bench interaction contract (spec-only, for Phase 21 to build against):** roving-listbox contact sheet (`role="listbox"`, buttons as `role="option"`), arrow keys move selection within `.sheet`, `P`/`H`/`X` keys grade the currently-focused trace (filed/held/dismissed) from anywhere within `.bench`, a filed trace cannot be re-graded (TERRARIUM law — "a scenario cannot be withdrawn from the suite"), grading announces via a visually-hidden `aria-live="polite"` region. Preserve this exact keyboard contract when Phase 21 wires it live.

**Gate mechanism on this page:** the `.gatebar` and the `data-gate` root attribute must reflect the **real** deploy-checklist state (from the same `checklist-runs`/`approve-deployment` data the Deploy page already reads — see §9), not a page-local simulated toggle. Do not port `agent.html`'s demo "Run the programme" → `setTimeout` → fake critical finding flow verbatim; the critical-finding banner and gate state must derive from live red-team/checklist data.

### 6.5 Soul — `/agents/[id]/soul` (replaces `apps/admin/app/agents/[id]/soul/page.tsx`)

**Source:** `soul.html`. **Shell:** B (rail). **three.js:** **no** — see §5.3 confinement conflict resolution; replace `#scene` with a CSS-only fallback.

- Two-column `.soul` grid (form + sticky "object" prompt preview, single column ≤1000px).
- Form sections: Identity (name, role select, greeting textarea), Temperament (3 range-slider "dials": Warmth/Rigor/Candor, each with a live mono readout + a describing sentence that changes in 3 bands — 0–33/34–66/67–100 — per `BANDS` in the script), Rules (Do / Do not — dynamic add/remove list rows with commit-on-blur/Enter, discard-on-Escape).
- Sticky `.savebar` (bottom, `.tint`): "Last saved {timestamp}" / "Unsaved changes" + Draft chip, "Save soul" button. Dirty-state tracking (`touch()`) announces once, not per keystroke.
- Right column, **the artifact**: a live-regenerated `<pre>` system-prompt preview (`#prompt`), rebuilt on every keystroke from the form + dial state, headings in `--ink`-weight bold, dial rows in a hanging-indent mono block. This is a functional slot (MESH.md/CONTEXT.md anti-pattern rule) — nothing decorative may occupy it; it must always show the real prompt text the save action will persist, never a placeholder illustration.
- Wire `Save soul` to `PATCH {apiBase}/api/v1/agents/{id}` (existing endpoint, see §9) instead of the prototype's local-only "saved" timestamp stamp.
- Dial semantics (`warmth`, `rigor`, `candor`, 0–100 each) map directly onto whatever soul fields the current `PATCH` payload already expects — confirm against `apps/admin/app/agents/[id]/soul/page.tsx`'s existing request shape before wiring; do not invent new soul fields.

### 6.6 Ingest — `/agents/[id]/ingest` (replaces `apps/admin/app/agents/[id]/ingest/page.tsx`)

**Source:** `ingest.html`. **Shell:** B (rail, active=Ingest). **three.js:** no.

- Two-tab panel (`role="tablist"`, arrow-key roving tabs): "Upload file" (dropzone, `.drop`, drag-over state, real `<input type="file">` styled as a button) and "Add URL" (a single URL field + Fetch button).
- Knowledge-base `.ledger` (Document / Type / Chunks / Status / Added) reflecting the real document list from `GET {apiBase}/api/v1/agents/{id}/documents`. Status cell states: `Parsed` (`.chip-pass`), `Processing` (`.chip-live` + live elapsed counter), `Failed` (`.chip-seal` + a `Retry` button + a visually-hidden reason string), pending chunk count (`pending` in muted mono while processing).
- **HIVE chunk swarm**: on a document transitioning to "parsing," an SVG particle swarm streams ~40 dots from off-canvas to a golden-angle-spiral cluster at a **capped speed** (`MAX_SPEED = 2.6px/frame`, arrival easing inside a slowing radius) — this speed cap is explicitly the point (HIVE's "dispatch births a centroid" language) and must be preserved, not sped up for perceived performance. **Colour fix required (§10 anti-pattern 3):** the prototype hardcodes the swarm dot fill to the literal retired-brass hex `#C79A3C` (`ingest.html:442`, with its own comment admitting "literal: var() will not resolve here" — true for raw SVG attributes, but the literal must be the **current** `--live` bone value, not the old brass gold). Port the swarm dot colour as the resolved bone hex (`#E7E5E1` in the open-gate state, or dynamically read `getComputedStyle(document.documentElement).getPropertyValue('--live')` at draw time so it still tracks a gate-blocked repaint) — **not** `#C79A3C`.
- `prefers-reduced-motion`: swarm dots render already-settled at their final positions with no animation (`stream()` calls `done()` immediately) — port this reduced-motion branch exactly.
- Wire file upload / URL fetch to the existing `POST {apiBase}/api/v1/agents/{id}/documents` flow and the SSE progress stream the current dusk ingest page already consumes (`eventsUrl` in `apps/admin/app/agents/[id]/ingest/page.tsx`) instead of the prototype's client-only chunk-count simulation (`chunksFor()` hash function). The visual swarm animation is real UI polish layered over real SSE-driven state transitions, not a replacement for them.

### 6.7 Eval — `/agents/[id]/eval` (replaces `apps/admin/app/agents/[id]/eval/page.tsx`)

**Source:** `eval.html`. **Shell:** B (rail, active=Eval). **three.js:** no.

- **Telemetry chart** (`.telemetry`): an SVG line chart, 4 channel traces over the last N runs, each numeral "pinned" to the head of its own trace via a computed leader line (VITALS pattern — `layout()` measures trace endpoints in pixel space and lays the numeral + leader against them, with collision-avoidance nudging between numerals `MIN_GAP = 44px`). A dashed neutral gate-threshold line at 0.90, labelled `GATE 0.90`. Collapses to a static 2-col grid of `.pin`s under 900px (leaders hidden).
- **Colour fix required (§10 anti-pattern 1):** the four trace `<polyline>` strokes and the four `.dot`/`.pin` swatches in the prototype use literal hex `#C79A3C` (gold), `#5FA3C7` (blue), `#4FA88A` (jade/green — collides with the `--pass` verdict colour), `#9C8DC4` (purple). **This is the clearest violation of the "colour is a verdict" law in the entire prototype set** — CONTEXT.md is explicit: "Eval channels are values of bone by luminance; red appears only on failure." Port the four traces using `--ch-1..4` (`#E7E5E1`, `#A9AFB1`, `#7C8386`, `#565C5F`) resolved to literal hex for the SVG `stroke`/`fill` attributes (CSS custom properties do not resolve inside SVG presentation attributes — read via `getComputedStyle` at draw time, same technique as §6.6's swarm-colour fix, so the values still track light/dark or gate-state changes if that's ever introduced). Do not use the prototype's literal brand-hue palette.
- Real data source: drive the chart + the ledger from `GET {apiBase}/api/v1/agents/{id}/eval-runs` and `.../eval-runs/{id}/results` (existing endpoints, already consumed by `apps/admin/app/agents/[id]/eval/page.tsx`) instead of the prototype's 8-point hardcoded polyline arrays.
- **The judge** (CHORUS): a word-by-word typeset verdict sentence with a blinking compositor caret (`step-end`, 0.9s), typed at 30ms/word via `setInterval`. On `prefers-reduced-motion`, set the full sentence instantly with no caret. A visually-hidden `role="status" aria-live="polite"` element receives the full sentence at once regardless of animation, so screen readers never hear word-by-word. The verdict text itself must be generated from real run data (or omitted if there's no LLM-judge summary endpoint) — do not hardcode the prototype's placeholder sentence in production.
- "Run evals" button wires to the existing `POST {apiBase}/api/v1/agents/{id}/eval-runs/trigger`.
- Scenario `.ledger`: Scenario / Source / Faithfulness / Relevancy / Verdict / Ran — from real `eval_results`, `data-verdict="pass"|"fail"` driving row emphasis (`.ledger tr[data-verdict="fail"] td.num { color: var(--ink) }` — i.e. only failing-row numerals get full-strength ink; passing rows stay dimmer, a subtlety worth preserving).

### 6.8 Deploy — `/agents/[id]/deploy` (replaces `apps/admin/app/agents/[id]/deploy/page.tsx`)

**Source:** `deploy.html`. **Shell:** B (rail, active=Deploy). **three.js:** no. **This page's data is already fully real** in the current dusk build (checklist-runs, approve-deployment, widget-config) — Phase 20 is a straight re-skin here, no empty states needed.

- Two-column `.bench` (gate table + embed + appearance on the left, sticky widget preview on the right; single column, preview hidden, ≤1100px).
- **The gate**: a 4-row `.ledger` (Evals pass rate / Red team / Knowledge base / Soul), each with a `.why` micro-caption, a value, and a pass/fail chip — sourced from the real checklist-run data (same shape the current dusk deploy page already fetches). `data-gate` root attribute must reflect the **real** `deployment_blocked`/checklist state, not the prototype's `#break`/`#clear` demo toggle buttons — those two "Test the gate" buttons are prototype-only instrumentation for the static demo and **must not ship** in production (they let an operator fake a critical finding, which is not a real product action). Drop the `.rig` "Test the gate" section entirely from the production build.
- "Approve deploy" wires to `POST {apiBase}/api/v1/agents/{id}/approve-deployment` (existing endpoint), disabled when the gate is shut, with the exact consequence-copy pattern (`.voice.consequence`) describing what approval does before the operator commits.
- Embed snippet: `<script src="https://cdn.wchats.co.za/widget.js" data-agent="{agent-id}" async>` with a working copy-to-clipboard (Clipboard API with an `execCommand` fallback for insecure/`file://` contexts — the fallback matters less in production HTTPS but port it anyway, it's a one-line safety net).
- Appearance tiles (Floating button / Side panel / Inline) as a real radio-group wired to `GET`/`PUT {apiBase}/api/v1/agents/{id}/widget-config` (existing endpoint). The sticky preview re-renders per the selected mode's caption and layout — this preview uses the widget-exception light palette from §4, not console tokens.

### 6.9 Settings — `/agents/[id]/settings` (replaces `apps/admin/app/agents/[id]/settings/page.tsx`)

**Source:** `settings.html`. **Shell:** B (rail, active=Settings). **three.js:** no.

- Record section: agent name (disabled input, rename arrives with multi-agent workspaces per the prototype's own help copy — keep that copy, it's accurate scope framing, not metaphor), a `.facts` definition list (Agent id, Neon project id) each with a working Copy-to-clipboard button.
- **Danger zone**: a `.voice` sentence describing the blast radius (document count, eval suite, red-team history, session records — populate from real counts if available, otherwise keep generically true phrasing), an "arm" button (`aria-expanded`) that reveals a type-to-confirm panel (must type the exact agent id to enable "Delete permanently"), Escape/Cancel to disarm. **Arming the panel sets `data-gate="blocked"` on the whole console as a warning treatment** (WARDEN law — "a destructive act is... the temperature of the room") and disarming restores it — port this exactly, it is intentional and matches the design law, not a bug.
- **Real endpoint exists** (`DELETE /agents/{agent_id}` in `apps/api/app/api/v1/agents.py:208`) — wire "Delete permanently" to a real call and redirect to `/agents` on success. **Do not ship the prototype's fake "Prototype build. No agent was deleted." message** — that status line was for the static demo only.

### 6.10 `console.html` — not routed in Phase 20

Explicitly deferred (CONTEXT.md `## Deferred Ideas`). No Next.js route corresponds to it. Do not port its single-surface fold, the "shutter" full-screen gate treatment, the tool-overlay pattern, or the evidence-spine sediment visualization in this phase. Its patterns (the physical shutter drop, the tool re-tooling overlay) may inform a future phase but are out of scope here — flag any temptation to "sneak in" console.html's shutter as scope creep.

---

## 7. Route Inventory — prototype → Next.js, replace vs delete

| Prototype file | Next.js route | Current dusk file it replaces | Action |
|---|---|---|---|
| `index.html` | `/` | `apps/admin/app/page.tsx` | Full rebuild |
| `agents.html` | `/agents` | `apps/admin/app/agents/page.tsx` | Full rebuild |
| `agent-new.html` | `/agents/new` | `apps/admin/app/agents/new/page.tsx` | Full rebuild, **reuse `JourneyStepper.tsx` state logic** (§6.3) |
| `agent.html` | `/agents/[id]` | `apps/admin/app/agents/[id]/page.tsx` | Full rebuild + honest empty states (§6.4) |
| `soul.html` | `/agents/[id]/soul` | `apps/admin/app/agents/[id]/soul/page.tsx` | Full rebuild, drop three.js (§5.3) |
| `ingest.html` | `/agents/[id]/ingest` | `apps/admin/app/agents/[id]/ingest/page.tsx` + `DocumentDetailModal.tsx` | Full rebuild; `DocumentDetailModal.tsx` restyled, not deleted (no prototype equivalent screen exists — restyle to `.zone`/`.ledger` tokens, keep its own layout) |
| `eval.html` | `/agents/[id]/eval` | `apps/admin/app/agents/[id]/eval/page.tsx` | Full rebuild, fix channel colours (§6.7) |
| `deploy.html` | `/agents/[id]/deploy` | `apps/admin/app/agents/[id]/deploy/page.tsx` | Full rebuild, drop demo gate-test buttons (§6.8) |
| `settings.html` | `/agents/[id]/settings` | `apps/admin/app/agents/[id]/settings/page.tsx` | Full rebuild, wire real delete (§6.9) |
| `console.html` | *(none)* | — | **Not shipped** (§6.10) |
| n/a — no prototype | `/sign-in`, `/sign-up` | `apps/admin/app/sign-in/[[...sign-in]]/page.tsx`, `sign-up/...` | Re-skin chrome to Gotham tokens (bare shell, §5.1-A treatment), keep Clerk `<SignIn>`/`<SignUp>` components; three.js **is** permitted here per the design law even though no prototype file demonstrates it — mount the same landing specimen behind/beside the Clerk form |

### 7.1 Component-level disposition

| Current component | Disposition |
|---|---|
| `components/TopNav.tsx` | **Delete.** Replaced by the Gotham rail (§5.1-B), a structurally different IA (top nav → fixed left rail). Build a new `components/gotham/Rail.tsx`. |
| `components/UserAvatar.tsx` | **Delete or relocate — flagged ambiguity.** No Gotham prototype shows account/avatar chrome anywhere (they're unauthenticated static demos). Recommendation: keep the existing `components/SignOutTab.tsx` peeking-tab pattern (it already solves "where does sign-out live without cluttering the rail") and restyle it to bone/graphite tokens; drop `UserAvatar.tsx` unless it's used for something beyond the removed `TopNav`. Confirm with the planner before deleting — check all call sites first. |
| `components/SignOutTab.tsx` | **Keep, restyle.** Bone/graphite colours, `--fail`/`--seal` accent stripe already matches the "destructive corner tab" role it plays; port its interaction (peek → full reveal on hover/focus) unchanged. |
| `components/JourneyStepper.tsx` | **Keep logic, restyle presentation** to `.stepper`/`.station` (§6.3). Do not rewrite `deriveStepState`. |
| `components/StepSubtaskCard.tsx` | **Delete.** No equivalent in any Gotham page; it was dusk-landing-specific decorative chrome (HeroPipeline family). |
| `components/HeroPipeline.tsx`, `components/HeroSteps.tsx` | **Delete.** Landing is a full rebuild (§6.1); the "three steps" section replaces this decoratively but is a plain `rule-double` grid, not a pipeline animation component. |
| `components/AgentCard.tsx` | **Restyle** to the `.zone.card` contract in §6.2 (keep as the agents-grid card component, new visual only). |
| `components/QueryProvider.tsx` | **Keep unchanged** — infra, not visual. |
| `agents/[id]/components/AlertsBanner.tsx` | **Restyle** to Gotham chip/banner tokens; keep its `GET .../alerts` + resolve wiring unchanged (real endpoint, §9). Where in the new IA does it render? **Flagged ambiguity:** no Gotham prototype page shows a generic alerts banner. Recommend surfacing it at the top of the operations-room page (`agent.html`'s equivalent, §6.4), above the six regions, since alerts are cross-cutting operational signal — confirm with planner. |
| `agents/[id]/ingest/DocumentDetailModal.tsx` | **Restyle**, keep structure (no prototype modal equivalent — ingest.html has no modal; document detail was a dusk-only affordance). Use `.zone`/`.well`/`.ledger` tokens for its contents. |
| `apps/admin/app/globals.css` | **Replace `:root` token block + `body` background treatment entirely** with §2's port. Delete `.glass`/`.glass-strong`/`.glass-nav`/`.on-photo`/`@supports not (backdrop-filter)` fallback and the `body { background-image: url('/skyline-w-chats.png') ... }` rule (dusk-only). Keep the `.sign-out-tab` block (restyled colours only, §7.1) and the `prefers-reduced-motion` global reset (compatible, may be merged with §2.11's `.tint` rule). |
| `public/skyline-w-chats.png` | **Delete** the file reference from CSS; confirm with planner whether to delete the asset file itself or leave it unused in `public/` (no functional harm either way, but dead weight). |

---

## 8. "Colour is a verdict" — the enforceable rule

1. **Only three hue families exist in the console:** bone-neutral (`--ink`/`--ink-2`/`--ink-3`/`--live` ramp, no hue), pass-green (`--pass`), fail/seal-red (`--fail`/`--seal`). Nothing else. Any new component introduced by the port that wants a fourth colour (a badge, an icon tint, a chart series) must be re-expressed as one of these three, or as a bone-luminance step (`--ch-1..4`-style).
2. **A colour is a claim, not a style.** Before applying `--pass`/`--fail`/`--seal` to any pixel, ask: "is this asserting that something held or failed?" If not, it must be bone-neutral.
3. **`--live` is brightness, not a hue.** It equals `--ink`/`#E7E5E1` in the open-gate state — it is never a "brand blue" or similar; it is the top of the bone ramp made bright, and the ONE thing that makes it read as "accent" is restraint (it appears only on the specific element list in §4), not saturation.
4. **The eval channels are the four bone-luminance steps, never four hues** — see §2.6, §6.7 fix.
5. **Red only on failure**, never as a generic "important" or "warning-but-not-really" signal. There is no amber/yellow "warning" tier in this system (unlike the retired dusk `--gold` token) — a signal either holds (pass/bone) or fails (red); do not introduce an intermediate warning hue anywhere in the port.
6. **The gate flip is the only mechanism that recolours the whole page**, via `data-gate` (§2.10). No component may independently decide to render itself red/oxblood outside of (a) its own local pass/fail verdict chip, or (b) inheriting `--live`'s gate-driven value. A component should never contain logic like `color: agent.hasCriticalFinding ? red : normal` — it should set `data-gate` at the root and let CSS cascade do it, exactly as `agent.html`'s and `deploy.html`'s scripts do.

### 8.1 Interaction & motion — `data-gate` shutter behaviour

- The mechanism is a single DOM attribute write: `document.documentElement.dataset.gate = 'open' | 'blocked'`, paired with `window.gotham.setGate(state)` on any page where the specimen is mounted (landing/auth only, §5.3).
- Visual repaint is driven entirely by CSS custom-property inheritance (§2.10) plus the `.tint` transition class (§2.11) — **not** by imperative style-setting in JS. Any component that needs to react to the gate state should read `--live`/`--hairline*` via CSS, not via a JS conditional checking `data-gate`.
- Transition timing: `600ms ease` on `background-color`, `border-color`, `color`, `box-shadow`, scoped to `.tint` and its non-SVG descendants (SVG `path`/`circle`/`line`/`polyline` are excluded from the CSS transition — their colour changes, where they happen at all per §6.7/§6.6's `getComputedStyle` re-read pattern, should snap or use their own JS-driven easing, matching the three.js specimen's per-frame lerp rather than a CSS transition).
- **`prefers-reduced-motion: reduce` contract (UI2-08, exact):**
  - `.tint` transition duration collapses to `1ms` (§2.11) — the repaint still happens, instantly, rather than easing.
  - The three.js specimen (landing/auth only) slows its internal clock to 0.2× and reduces spin/wobble amplitude, but keeps rendering (§5.3) — this is a "reduced," not "eliminated," motion accommodation, matching the prototype's own `REDUCED` branching in `scene.js`.
  - The ingest chunk swarm (§6.6) renders pre-settled with no particle animation.
  - Row fades (e.g. `agent.html`'s `fileScenario()` inserting a new suite row with a 420ms opacity transition) are skipped — the row appears at full opacity immediately.
  - The eval judge's word-by-word typeset (§6.7) sets the full sentence instantly with no blinking caret.
  - The provisioning checklist's staged reveal (§6.3) marks all rows done immediately instead of staggering by `550ms` steps.
  - Global fallback: any animation/transition not explicitly listed above should still respect the standard `animation-duration: 0.01ms !important` / `transition-duration: 0.01ms !important` blanket rule (already present in current `globals.css` and in `app.css` — keep both, they're compatible; the `.tint`-scoped `1ms` rule and the global `0.01ms` rule do not conflict since `1ms` also satisfies "near-instant").

---

## 9. Endpoint preservation map (non-regression contract)

Every one of these calls exists in the current dusk build and must be preserved by the Gotham-ported page that owns the same responsibility. This is the concrete meaning of CONTEXT.md's "every live endpoint the dusk pages already call must NOT regress."

| Endpoint | Current caller(s) | New page owner |
|---|---|---|
| `POST {apiBase}/me/provision` | `agents/page.tsx`, `agents/new/page.tsx` | `/agents` (first-run provision-check), `/agents/new` |
| `GET {apiBase}/api/v1/agents` | `agents/page.tsx` | `/agents` |
| `GET/PATCH {apiBase}/api/v1/agents/{id}` | `agents/[id]/page.tsx`, `layout.tsx`, `soul/page.tsx`, `new/page.tsx` | `/agents/[id]`, `/agents/[id]/soul`, `/agents/new` |
| `DELETE {apiBase}/api/v1/agents/{id}` | *(not currently called by any dusk page — real endpoint exists server-side, §6.9)* | `/agents/[id]/settings` (new wiring, not a regression since it's newly connected, not newly built) |
| `GET/POST {apiBase}/api/v1/agents/{id}/documents` | `agents/[id]/page.tsx`, `layout.tsx`, `ingest/page.tsx` | `/agents/[id]` (Judgement/summary counts), `/agents/[id]/ingest` |
| `GET {apiBase}/api/v1/agents/{id}/documents/{docId}` | `ingest/page.tsx` | `/agents/[id]/ingest` |
| `GET {apiBase}/api/v1/agents/{id}/documents/{documentId}/detail` | `DocumentDetailModal.tsx` | `/agents/[id]/ingest` (modal) |
| SSE `{apiBase}{eventsUrl}` (ingest progress) | `ingest/page.tsx` | `/agents/[id]/ingest` (drives real swarm-animation state, §6.6) |
| `GET {apiBase}/api/v1/agents/{id}/eval-runs`, `.../eval-runs/{id}/results`, `.../eval-runs/trigger` | `layout.tsx`, `eval/page.tsx` | `/agents/[id]/eval`, `/agents/[id]` (Judgement region, §6.4) |
| `GET/PUT {apiBase}/api/v1/agents/{id}/widget-config` | `deploy/page.tsx` | `/agents/[id]/deploy` |
| `GET/POST {apiBase}/api/v1/agents/{id}/checklist-runs`, `.../checklist-runs/{runId}`, `.../checklist-runs/{id}/acknowledge`, `.../approve-deployment` | `deploy/page.tsx` | `/agents/[id]/deploy`, `/agents/[id]` (gatebar, §6.4) |
| `GET {apiBase}/api/v1/agents/{id}/alerts`, `.../alerts/{alertId}/resolve` | `AlertsBanner.tsx` | wherever `AlertsBanner` is relocated to (§7.1 flagged ambiguity) |

No new endpoints are introduced by this table. Where §6.4 wires "real data" for the Judgement/Adversary regions, it is reusing the eval-runs and red-team-runs endpoints already listed here (or their equivalents — confirm a `red_team_runs` list endpoint exists at the same shape the checklist consumes; `deployment.py:154`'s `_fetch_red_team_summary_sync` is a server-side helper, not necessarily a public list endpoint — **flag to planner:** verify a `GET /agents/{id}/red-team-runs` (or similar) public endpoint exists before wiring §6.4 region 5's summary tiles; if it does not, that summary tile becomes honest-empty too, not partial).

---

## 10. Critical anti-patterns — MUST-NOTs

1. **Eval channel colours (§6.7).** `eval.html` uses four literal hex hues (`#C79A3C` gold, `#5FA3C7` blue, `#4FA88A` green, `#9C8DC4` purple) for the four ragas channels, directly contradicting tokens.css's `--ch-1..4` (all bone luminance) and CONTEXT.md's explicit law. **Must fix at port time** — use `--ch-1..4` resolved hex, not the prototype's literal SVG colours.
2. **No dusk/skyline/amber-console residue.** After every token rename, grep the old token names repo-wide — CSS fails silently on undefined custom properties (white-on-white, or in this case bone-on-graphite invisible text). Specifically grep for and remove: `--bg-deep`, `--glass-bg*`, `--glass-blur*`, `--glass-highlight`, `--well` (dusk's `--well` is a *different* rgba value than Gotham's `--well` — do not assume the name carrying over means the value is compatible, redefine it per §2.1), `--chip`, `--border*`, `--accent*`, `--lilac*`, `--cyan`, `--amber*`, `--gold*`, `--text-1..4`, `--radius-*`, `--shadow-*`, `--font-display` (dusk uses Fraunces, Gotham uses Space Grotesk — same variable name, different value, must be fully replaced not merged), `.glass`, `.glass-strong`, `.glass-nav`, `.on-photo`, `skyline-w-chats.png`. **Outstanding audit called out by CONTEXT.md:** `prototypes/gotham/MESH.md:46` still says "brass armature" and the palette section header still says `"Brass on Petrol"` (MESH.md line 10) — MESH.md is lineage documentation, not shipped CSS, so it does not need to be fixed for the app to work, but its language must not leak into any UI copy or code comments written during this phase (call the palette "Bone on Graphite" everywhere in code/docs written for Phase 20, matching tokens.css and CONTEXT.md, not MESH.md).
3. **Literal `#C79A3C` (retired brass gold) in ingest.html's chunk swarm (`ingest.html:442`).** Must resolve to the current `--live` bone value at port time (§6.6), not carry the literal old hex forward.
4. **`console.html`'s `--brass-*` → `--live-*` shim (`console.html:25-38`)** confirms `app.css` itself is clean (no `var(--brass...)` residue was found in `app.css`) but that at least one component author expected `--brass*` variable names to exist. Do not introduce any `--brass*` custom property in the Next.js port; if any future component needs this shim pattern, name it after the semantic role (`--live*`), never the retired material metaphor.
5. **Nested `<a>` inside a card `<a>` wrapper.** The browser ejects the inner anchor. `agents.html`'s `.card-open::after { inset:0 }` stretched-link pattern is the correct approach — exactly one real `<a>` per card, everything else `<span>`/`<button>`/non-interactive (§6.2).
6. **Decoration in a functional slot.** Two concrete instances already identified in this port: (a) the agents-dashboard command strip (§6.2) — do not ship fake command-dispatch chrome with no real backend behind it; (b) the soul editor's prompt preview (§6.5) — the artifact pane must always show the real generated prompt, never a placeholder/illustration.
7. **UI copy that explains the metaphor.** "The gate," "the bench," "the whisper-zone" are internal design vocabulary from MESH.md. Only port the copy the prototypes *actually put in front of the user* (e.g. "The gate is open. This agent can meet a customer." — this is product copy, keep it) — do not add new copy that narrates the design system to the operator (e.g. never write something like "Welcome to the bench, where your agent's instruments live").
8. **Fake/demo interaction handlers shipping to production.** Three concrete instances found and must be cut or rewired, not ported verbatim: the landing page's gate demo buttons (§6.1, fine to keep — it's explicitly a marketing demo, not tied to a real agent), `deploy.html`'s "Test the gate" simulate/clear buttons (§6.8, **must be dropped**, not marketing-demo-safe since it's on a real agent's real deploy page), `settings.html`'s "Prototype build. No agent was deleted." message (§6.9, **must be replaced** with a real DELETE call).
9. **No horizontal overflow at 1440/1280/900px (UI2-08).** The `.page { max-width: 1280px }` container plus each page's own breakpoints (900px is the universal rail-collapse breakpoint; per-page secondary breakpoints at 1000/1080/1100px for two-column layouts collapsing to one column) must be verified at exactly these three viewport widths during executor QA — the `eval.html` telemetry chart's leader-line layout (`ResizeObserver`-driven, §6.7) is the most likely regression point since it measures pixel geometry against `wrap.clientWidth`.

---

## 11. Registry Safety

| Registry | Blocks Used | Safety Gate |
|----------|-------------|--------------|
| shadcn official | none | not applicable |
| third-party | none | not applicable |

No component registry is used anywhere in this phase (§1). The `shadcn init` gate is skipped: `components.json` does not exist in `apps/admin`, and CONTEXT.md's locked decision ("canonical design contract = prototypes/gotham/... port these, do not redesign") is itself a decision against introducing a component-registry system in this phase — doing so now would be a redesign of the build approach, not a re-skin. If a future phase wants shadcn, that is a separate, explicit decision outside Phase 20's scope.

---

## 12. Copywriting Contract

| Element | Copy |
|---------|------|
| Primary CTA (landing) | "Build your agent" (→ `/agents`) |
| Primary CTA (agents dashboard) | "New agent" (→ `/agents/new`) |
| Primary CTA (provisioning) | "Create agent" |
| Primary CTA (deploy) | "Approve deploy" |
| Empty state — agents dashboard, zero agents | Not specified by any prototype — author per pattern: heading "No agents yet," body "Provision your first agent to start ingesting documents and shipping a verified assistant." + "New agent" CTA (reuses the primary CTA, no new copy needed beyond the heading/body) |
| Empty state — Live region (§6.4 #1) | "Live performance metrics are not available yet." |
| Empty state — Retrieval health region (§6.4 #2) | "Retrieval health instrumentation ships in a future release." |
| Empty state — The bench region (§6.4 #3) | "No failing production traces to review yet." |
| Empty state — born-in-production/authored tiles (§6.4 #4) | "not tracked yet" (mono, matching the existing `pending` treatment style in `ingest.html`) |
| Empty state — Adversary coverage table (§6.4 #5) | "Per-strategy coverage detail ships in a future release; showing the latest run summary above." |
| Empty state — The prompt region (§6.4 #6) | "Version history, canary releases and rollback ship in a future release." |
| Error state — document parse failure | Already authored in prototype, keep verbatim: chip "Failed" + Retry button + reason string (e.g. "Parser found no headings in {filename}") |
| Error state — deploy refused | Already authored, keep verbatim pattern: "deploy refused. the gate is shut. {failing signal names} did not hold." (adapt to sentence case per MESH.md law 4 if the source string is lowercase-styled, which several console.html strings are — the routed pages' prototypes, e.g. `deploy.html`, already use sentence case; follow those, not console.html's lowercase micro-copy, since console.html is unshipped) |
| Destructive confirmation — delete agent | Type-to-confirm pattern: "Type the agent id to confirm," input placeholder = the real agent id, "Delete permanently" button disabled until exact match, warning voice line naming the real blast radius (doc count, eval suite, red-team history, session records) |
| Destructive confirmation — command strip removal (if cut per §6.2) | n/a — no UI, feature removed rather than confirmed |

---

## 13. Accessibility

- **Keyboard nav:** skip-link (`.skip`, "Skip to content," visually hidden until focused) on every page; roving-tab pattern for the ingest upload/URL tabs (arrow keys, Home/End); roving-listbox pattern for the bench contact-sheet (§6.4.1, spec-only in Phase 20); `/` global shortcut to focus the command strip/input where one exists (agents dashboard — if kept, §6.2) or the eval/other command inputs; `Escape` closes/discards inline add-rows (soul editor rule lists) and disarms the settings danger-zone panel.
- **Focus-visible:** `outline: 2px solid var(--live); outline-offset: 2px` globally (`app.css :focus-visible`), plus component-specific offset variants (segmented control uses `-3px` inset offset, ingest file-input label uses `2px`). Every custom control (segmented tone selector, appearance tiles, range sliders) must have a real, reachable, keyboard-operable native input under the hood — the prototypes already do this (visually-hidden real `<input>` + styled `<label>`), preserve it, never fake a control with a `<div onClick>`.
- **Contrast:** the bone-on-graphite ramp is high-contrast by construction (`#E7E5E1` on `#0E1012` ≈ 14.7:1); no additional contrast work should be needed if tokens are ported verbatim without alteration. Verify `--ink-3` (`#6B7275`) on `--bg`/`--surface` for the smallest micro-labels (9-10px mono) meets at least WCAG AA for non-text/large-text use, since it's used at very small sizes — this is the one combination worth spot-checking with a contrast tool during QA, as the source design accepts it for de-emphasized metadata but the actual ratio should be confirmed (`#6B7275` on `#0E1012` ≈ 3.9:1 — below AA for normal text; this is intentional de-emphasis in the source but confirm this size/weight only ever carries non-essential metadata, never the sole carrier of required information).
- **No horizontal overflow at 1440/1280/900px** — see §10 anti-pattern 9.
- **`aria-live` regions:** gate-state announcements (`role="status" aria-live="polite"`), the eval judge's full-sentence screen-reader echo (separate from the visual word-by-word typeset), command-strip replies, bench grading announcements — port every one of these `sr-only`/`vh` announcement elements exactly, they are load-bearing a11y, not decoration.
- **Table semantics:** every `.ledger` uses real `<th scope="col">`/`<th scope="row">` and a visually-hidden `<caption>` describing the table's content for screen readers (several prototypes already do this, e.g. `index.html`'s evidence table) — carry this forward to every ledger table in the port, including the ones currently missing a caption in the prototype source (add one if absent).

---

## 14. Component Inventory (new, shared)

Build these once in `apps/admin/app/components/gotham/` and reuse across all routes rather than duplicating markup per page:

| Component | Backs prototype class(es) | Notes |
|---|---|---|
| `Rail.tsx` | `.rail`, `.rail-mark`, `.rail-btn` | Two icon-set variants (4 icons vs 4+Settings, §5.1-B) |
| `PageChrome.tsx` | `.graticule`, `.bloom`, `.cross` × 4 | Wraps every routed page; accepts per-page cross-offset props |
| `Zone.tsx` | `.zone`, `[data-live]` | Generic panel/card primitive |
| `Chip.tsx` | `.chip-live/pass/fail/seal/mute` + `.dot`/`.dot-live` | Verdict-chip primitive, enforces the §8 colour law by construction (accepts a `verdict` prop, not a raw colour) |
| `Ledger.tsx` | `.ledger` + caption/scope conventions | Enforces `<caption>` + proper `scope` attrs (§13) |
| `Btn.tsx` | `.btn-primary/ghost/seal` + disabled | — |
| `CommandStrip.tsx` | `.command` | Only used if §6.2's judgment call keeps it; build behind a feature flag so it's trivial to omit |
| `GateProvider.tsx` (context) | `data-gate` root attribute + `.tint` | Central place that writes `document.documentElement.dataset.gate` and (on landing/auth only) calls `window.gotham.setGate()` — no other component should write this attribute directly |
| `Specimen.tsx` (client component, dynamic-imported) | `scene.js`/`mountGotham` | Mounted only by `/`, `/sign-in`, `/sign-up` (§5.3) |
| `EmptyState.tsx` | n/a — new, no prototype precedent | Generic honest-empty-state block for §6.4's four not-yet-backed regions; consistent heading+body+(optional link) shape |

---

## Checker Sign-Off

- [ ] Dimension 1 Copywriting: PASS
- [ ] Dimension 2 Visuals: PASS
- [ ] Dimension 3 Color: PASS
- [ ] Dimension 4 Typography: PASS
- [ ] Dimension 5 Spacing: PASS
- [ ] Dimension 6 Registry Safety: PASS

**Approval:** pending

---

## UI-SPEC COMPLETE

`.planning/phases/20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-/20-UI-SPEC.md`
