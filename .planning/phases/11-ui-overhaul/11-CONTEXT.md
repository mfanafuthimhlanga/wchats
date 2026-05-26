# M11 — Admin UI End-to-End Overhaul
## Context Drop (captured 2026-05-25)

> **Status:** Context preserved — NOT yet planned or executed.
> M9 (Retrieval Strategy Synthesis) and M10 (Maintenance + Observability) are not yet implemented.
> This file is a north-star reference so M11 can be planned cleanly when its time comes.
> When M11 becomes active, run `/gsd-discuss-phase 11` with this file in context.

---

## What this milestone does

Replace the admin UI's current **Parchment & Wine** light theme (`--bg: #F0E8E0`, wine `--accent: #7B1C3A`) with the **Hillbrow at Dusk** dark design system end-to-end. Every screen, every component, every CSS variable. The W Chats brand identity — derived from the Johannesburg skyline at sunset — must be coherent from the public landing page through the authenticated agent-management dashboard to the deploy tab.

This is not a polish pass. It is a complete visual rearchitecture: token system, typography, logo, layout rules, component vocabulary.

---

## The background surface — the most important rule in this entire document

> **`skyline-w-chats.png` (1672×941px, 2.5MB) is the background surface for the entire admin application. Not the landing page hero. Not a section background. The whole app.**

The Johannesburg skyline at dusk is the canvas. Every screen — landing page, agent dashboard, journey view, eval, deploy, auth pages — renders on top of this photograph. The photo is fixed (`background-attachment: fixed`), full-cover, and persistent. When the user scrolls, the content moves but the city stays.

**Implementation — `body` in `globals.css`:**
```css
body {
  background-image: url('/skyline-w-chats.png');
  background-size: cover;
  background-position: center center;
  background-attachment: fixed;
  background-repeat: no-repeat;
  /* Dark overlay so content stays legible — NOT a solid background */
  background-color: #0B0717; /* --bg-deep as fallback if PNG fails to load */
}
```

**Implementation — dark overlay on top of the photo:**
The photo alone is too bright for dark UI legibility. A `body::before` pseudo-element provides a persistent semi-transparent indigo wash over the photo. The UI floats on top at `z-index: 1+`.

```css
body::before {
  content: '';
  position: fixed;
  inset: 0;
  z-index: 0;
  background:
    radial-gradient(ellipse 60% 40% at 80% 0%, rgba(244, 116, 140, 0.07) 0%, transparent 50%),
    radial-gradient(ellipse 40% 30% at 0% 60%, rgba(183, 154, 224, 0.05) 0%, transparent 50%),
    rgba(11, 7, 23, 0.72);  /* ~72% indigo veil — dark enough to read on, light enough for the city to show */
  pointer-events: none;
}

/* Film grain — prevents flat OLED feel at 72% veil */
body::after {
  content: '';
  position: fixed; inset: 0;
  z-index: 0;
  pointer-events: none;
  opacity: 0.025;
  mix-blend-mode: overlay;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
}

/* All page content must sit above the overlays */
#__next { position: relative; z-index: 1; }
```

**What this means for every surface above the photo:**

| Surface | Rule |
|---|---|
| `body` background | The photograph. Never a solid colour, never a gradient replacing the photo. |
| `--bg-deep #0B0717` | Fallback ONLY — used as `background-color` so the page is not white on a slow load. |
| Navigation bar | `--glass-bg rgba(30,22,56,0.55)` with `backdrop-filter: blur(24px) saturate(140%)` — the city reads through the nav at the top. Becomes `--bg-elev #181232` (solid) on scroll past the hero. |
| Page sections / route wrappers | Transparent (`background: transparent`) — the photo shows through all the way down the page. |
| Cards and panels | `--surface-1 #1E1638` solid — dark enough to be readable without blur. The city is the background; cards are the content layer sitting on it. |
| Glass stat tiles | `--glass-bg rgba(30,22,56,0.55)` with blur — approved glass surfaces. The photo subtly reads through the stat tiles. |
| Modals / drawers | `--surface-1` with `--shadow-lift` — solid, not glass (too much text). |
| Auth pages (sign-in/up) | Transparent wrapper so the city is the auth backdrop. Centred `--surface-1` card floats over it. |

**The rule of thumb:** if you would normally write `background: var(--bg)` or `background: var(--bg-deep)` on a section or layout wrapper, write `background: transparent` instead. The photo fills that space. Only use solid fills on cards and panels that contain dense text.

**Skyline PNG location:**
- Source: `.claude/skills/wchats-design/assets/skyline-w-chats.png`
- Deploy to: `apps/admin/public/skyline-w-chats.png`
- Reference in CSS: `url('/skyline-w-chats.png')` (Next.js serves `/public` at `/`)

---

## Design system source of truth

**Location:** `.claude/skills/wchats-design/`

| File | Purpose |
|---|---|
| `SKILL.md` | Skill manifest — read this first |
| `README.md` | Full brand context, palette philosophy, voice rules, layout rules |
| `colors_and_type.css` | **The token system.** Drop into `globals.css` as the canonical source. Contains all CSS custom properties + semantic element defaults. |
| `ui_kits/wchats/` | Reference JSX components: Nav, Hero, WorkflowCard, AgentCard, StatCard, etc. |
| `ui_kits/wchats/styles.css` | Component-level styles that extend the token system |
| `assets/skyline-w-chats.png` | **The whole-app background photograph.** Copy to `apps/admin/public/`. Fixed cover on `body`. Never recreate as gradient. |
| `assets/logo-mark.svg` | Coral-gradient chat-square mark (replaces the spinning PNG) |
| `assets/wordmark.svg` | `w.chats` Fraunces lockup |
| `assets/icons/*.svg` | Feather-style stroke icon set |
| `preview/*.html` | Standalone design system card previews for every component |
| `reference/wchats-hillbrow-at-dusk.html` | The canonical 4-screen prototype — **the look-and-feel target** |

---

## Token migration: Parchment & Wine → Hillbrow at Dusk

The full token replacement in `apps/admin/app/globals.css`:

### Surface tokens
| Old (Parchment & Wine) | Old value | New (Hillbrow at Dusk) | New value |
|---|---|---|---|
| `--bg` | `#F0E8E0` | `--bg-deep` | `#0B0717` |
| `--surface-1` | `#FFFCF9` | `--surface-1` | `#140E2A` |
| `--surface-2` | `#F7F0EA` | `--surface-2` | `#1E1638` |
| `--surface-3` | `#EDE3D8` | `--surface-3` | `#382860` |
| *(none)* | — | `--bg` | `#0E0B1E` |

### Accent tokens
| Old | Old value | New | New value |
|---|---|---|---|
| `--accent` | `#7B1C3A` (wine/burgundy) | `--accent` | `#F4748C` (sunset coral) |
| `--accent-hover` | `#5E1229` | `--accent-hover` | `#F5899E` |
| `--accent-deep` | `#3D0B1F` | `--accent-deep` | `#C4435C` |
| `--accent-dim` | `rgba(123,28,58,0.08)` | `--accent-dim` | `rgba(244,116,140,0.10)` |

### New accent tokens (no old equivalent)
```css
--lilac:      #B79AE0;   /* jacaranda — secondary accent */
--lilac-dim:  rgba(183, 154, 224, 0.12);
--cyan:       #5EDFD3;   /* tower — rare highlight, max 2 per screen */
--amber:      #E8A87C;   /* building windows — warm illustrations */
```

### Border tokens
| Old | Old value | New | New value |
|---|---|---|---|
| `--border` | `#D9CCBE` | `--border` | `rgba(196,154,232,0.18)` (lavender) |
| `--border-soft` | `#EDE3D8` | `--border-soft` | `rgba(196,154,232,0.10)` |
| `--border-hard` | `#B8906A` | `--border-hard` | `rgba(244,116,140,0.32)` (coral) |
| *(none)* | — | `--glass-bg` | `rgba(30,22,56,0.55)` |
| *(none)* | — | `--glass-blur` | `blur(24px) saturate(140%)` |
| *(none)* | — | `--glass-border` | `rgba(244,232,220,0.10)` |

### Text tokens
| Old | Old value | New | New value |
|---|---|---|---|
| `--text-1` | `#1A0A0F` | `--text-1` | `#F0EBF8` |
| `--text-2` | `#4A2030` | `--text-2` | `#C4B8D8` |
| `--text-3` | `#8A6060` | `--text-3` | `#7B6B98` |
| `--text-4` | `#C4A0A0` | `--text-4` | `#4A3D62` |

### Shadow tokens (violet-tinted, never neutral grey)
| Old | Old value | New | New value |
|---|---|---|---|
| `--shadow-card` | `0 1px 2px rgba(74,32,48,.04), 0 4px 12px rgba(74,32,48,.06)` | `--shadow-card` | `0 1px 2px rgba(0,0,0,0.3), 0 4px 16px rgba(11,7,23,0.5)` |
| `--shadow-lift` | `0 4px 8px rgba(74,32,48,.04), 0 16px 32px rgba(74,32,48,.08)` | `--shadow-lift` | `0 4px 12px rgba(0,0,0,0.35), 0 24px 48px rgba(11,7,23,0.6)` |
| `--shadow-focus` | `0 0 0 3px rgba(123,28,58,0.18)` | `--shadow-focus` | `0 0 0 3px rgba(244,116,140,0.28)` |
| *(none)* | — | `--shadow-glow` | `0 0 32px rgba(244,116,140,0.18)` |

### Status tokens (recalibrated for dark)
| Old | Old value | New | New value |
|---|---|---|---|
| `--green` | `#166534` | `--green` | `#34D399` |
| `--green-bg` | `#F0FDF4` | `--green-dim` | `rgba(52,211,153,0.12)` |
| `--red` | `#B91C1C` | `--red` | `#F87171` |
| `--red-bg` | `#FEF2F2` | `--red-dim` | `rgba(248,113,113,0.12)` |
| `--amber` | `#92400E` | `--gold` | `#FBBF24` |
| `--amber-bg` | `#FEF3C7` | `--gold-dim` | `rgba(251,191,36,0.12)` |

### Typography tokens
| Old | New |
|---|---|
| `--font-sans: 'Inter'` | **keep** — Inter is correct for body |
| `--font-mono: 'JetBrains Mono'` | **keep** — correct for numbers, IDs, timestamps |
| `--font-pixelify` (Pixelify Sans — logo) | **replace** with Fraunces for the wordmark; remove Pixelify from font loading |
| *(none)* | Add `--font-display: 'Fraunces'` for headings |

---

## Typography upgrade

The design system uses three type families. All are on Google Fonts.

**Google Fonts import to add to `layout.tsx`:**
```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
<link
  href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT,WONK@9..144,300..800,0..100,0..1&family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
  rel="stylesheet"
/>
```

**Usage rules:**
- `Fraunces` — `opsz 144, SOFT 30` for display headings (sharp, authoritative). `opsz 144, SOFT 100, italic` for the italic-coral brand accent words (soft, flowing). Never title-case.
- `Inter` — all body copy, labels, navigation. Feature settings: `"ss01","cv11","cv02"`.
- `JetBrains Mono` — agent IDs, timestamps, eval scores, version strings, status metrics. Never regular body copy.

---

## Logo replacement

Current: spinning PNG (`/w-chats-lettermann.png`) + Pixelify Sans "Chats" wordmark.

New:
- **Mark:** `assets/logo-mark.svg` — 30×30px, 8px-rounded coral-gradient square with chat-bubble glyph. Static. No spin animation.
- **Wordmark:** `assets/wordmark.svg` — `w.chats` in Fraunces 500. The dot is coral `--accent`.
- Place SVGs in `apps/admin/public/` and reference as `/logo-mark.svg` and `/wordmark.svg`.
- Remove the `spin-cw` animation from the logo entirely.
- Remove the `--font-pixelify` font load and CSS variable.

---

## Screen inventory — what changes on each

### `app/page.tsx` — Landing page (public)
**Current state:** Light parchment background, basic grid layout, no hero background image, standard Inter h1, wine-red CTAs.

**Target state (reference: `reference/wchats-hillbrow-at-dusk.html` screen 1):**
- The skyline photograph is the page background (via `body` — see "The background surface" section above). The landing page layout wrapper is `background: transparent`.
- Glass nav floats over the city; the photograph reads through it on first load
- Hero layout: `background: transparent` — city shows through. The hero's visual atmosphere comes from the photograph plus a vignette overlay from `body::before`, not from a local section background.
- Eyebrow pill: `--glass-bg` glass surface, `● OPEN SOURCE · v0.4.2 · M8` in UPPERCASE tracked mono
- Headline: Fraunces display + strikethrough pattern — `The chat widget [strikethrough]is the hard part.[/strikethrough]` + italic-coral `The layer underneath is.`
- CTA row: coral primary button `Build your agent →` + ghost button `▶ Watch the build  2:18`
- Trust strip: `<30 min / >0.85 / 0 critical` in mono, UPPERCASE labels below
- Right col: the **WorkflowCard** glass animation (build pipeline cross-fading to live widget). Glass card so the city subtly reads through.
- Reference JSX: `ui_kits/wchats/Hero.jsx`, `ui_kits/wchats/WorkflowCard.jsx`, `ui_kits/wchats/Nav.jsx`

### `app/components/TopNav.tsx` — Authenticated top nav
**Current state:** Light `--bg` fill, wine accent for active links, Pixelify logo.

**Target state (reference: `preview/comp-top-nav.html`):**
- `--surface-1` fill (dark), `--border-soft` bottom border
- Logo: `logo-mark.svg` (30×30) + `wordmark.svg` — no spin
- Active nav links: `--accent-dim` background, `--accent` text
- Inactive: `--text-3` text, hover: `--surface-2` background + `--text-1`
- UserAvatar: `--lilac` background gradient for the avatar ring (not wine)
- Reference: `ui_kits/wchats/Nav.jsx`

### `app/agents/page.tsx` — Agents dashboard
**Current state:** Light parchment padding, basic h1, auto-fill card grid.

**Target state (reference: `reference/wchats-hillbrow-at-dusk.html` screen 2 — "Agents Home"):**
- Greeting strip: `--bg` with two off-axis radial gradients (coral top-right 8%, lilac top-left 6%). Contains `Good evening, [Name]` in Fraunces italic-coral for the name, UPPERCASE micro-label showing time + agent count.
- Agent grid: `repeat(3, 1fr)` with 16px gap, `--radius-md` cards, `--shadow-card`
- Empty state: centered with coral eyebrow pill + Fraunces italic accent
- "Create agent" CTA: coral primary
- Reference: `ui_kits/wchats/AgentCard.jsx`

### `app/components/AgentCard.tsx` — Individual agent card
**Current state:** Unknown (not yet read in detail).

**Target state (reference: `preview/comp-agent-card.html`, `ui_kits/wchats/AgentCard.jsx`):**
- `--surface-1` background, `--border-soft` border, `--shadow-card`, `--radius-md`
- Agent name: Fraunces 600, `--text-1`
- Status chip: `--green` / `--gold` / `--red` with matching `-dim` background, UPPERCASE tracked label
- Agent role: Inter `--text-3`, UPPERCASE tracked micro-label
- Hover: `translateY(-2px)`, `--border` border, `--shadow-card`, 1px top gradient bar (transparent → coral → transparent)
- NO glass on agent cards — solid `--surface-1` only (glass rule: never on dense data UI)
- Icon box: 40×40px `--accent-dim` background, `--accent` stroke icon

### `app/agents/new/page.tsx` — Create agent wizard
**Current state:** Not detailed — wizard form.

**Target state:**
- `--surface-1` panel, `--border` borders on inputs
- Input focus: `--shadow-focus` (coral ring)
- Labels: UPPERCASE tracked Inter 11px `--text-3`
- Field headings: Inter 600 `--text-1`
- Error states: `--red` text + `--red-dim` background alert
- Soul fields (voice, do, do-not): `--surface-2` textarea background
- Submit CTA: coral primary button

### `app/agents/[id]/page.tsx` — Agent detail / journey view
**Current state:** Uses `JourneyStepper` + `StepSubtaskCard`.

**Target state:**
- `--bg` page, `--surface-1` panel for the step list
- Active step: coral left-border accent + `--accent-dim` background
- Completed step: `--green` checkmark, `--text-3` text
- Pending step: `--text-4` text, `--border-soft` border
- SSE progress events: JetBrains Mono 13px `--text-3` for event log lines
- Step section title: UPPERCASE tracked `--text-3`

### `app/agents/[id]/ingest/page.tsx` — Ingest tab
**Current state:** Document upload + status list.

**Target state:**
- Upload zone: `--surface-1` background, `--border` dashed border, `--text-3` placeholder text
- Upload zone hover: `--accent-dim` background, `--border-hard` border
- Document rows: `--surface-1` card, `--border-soft` dividers between rows
- Status chips: `--green-dim`/`--red-dim`/`--gold-dim` backgrounds
- Document icon: 📄 (canonical — not SVG)
- Filename: JetBrains Mono `--text-2`

### `app/agents/[id]/soul/page.tsx` — Soul editor
**Current state:** Structured form with voice/do/do-not fields.

**Target state:**
- `--surface-1` form panel with `--border`
- Field labels: UPPERCASE tracked Inter `--text-3`
- Textareas: `--surface-2` background, `--border-soft` border, `--text-1` text
- Agent name display: Fraunces italic-coral for the name itself
- Save CTA: coral primary

### `app/agents/[id]/eval/page.tsx` — Eval dashboard
**Current state:** Eval run results.

**Target state:**
- Metric stat tiles: small glass tiles (`--glass-bg`, `--glass-blur`, `--glass-border`) — one of the approved glass use cases (stat tiles)
- Metric label: UPPERCASE tracked mono `--text-3`
- Metric value: JetBrains Mono 28px `--text-1` for the score number
- Trend chip: `--green-dim` / `--red-dim` background, mono value
- Scenario table: solid `--surface-1` rows — NO glass on tables
- Pass/fail chip: UPPERCASE tracked, status colour backgrounds

### `app/agents/[id]/deploy/page.tsx` — Deploy tab
**Current state:** Embed code + widget customiser.

**Target state:**
- Embed code block: `--surface-2` background, JetBrains Mono `--text-2`, coral "Copy" button
- Widget preview panel: dark with the agent's avatar centred
- Approval flow: coral primary button for approval; warning checkboxes use `--gold` accent
- Block state: `--red-dim` banner, `--red` text, disabled deploy button
- Ship state: `--green-dim` banner, `--green` text, active coral deploy button

### `app/agents/[id]/settings/page.tsx` — Settings tab
**Current state:** Unknown detail.

**Target state:** Standard form panel pattern — same as soul editor.

### `app/sign-in/` and `app/sign-up/` — Auth pages
**Current state:** Clerk-rendered components over parchment background.

**Target state:**
- The Johannesburg skyline IS the auth page background — no additional background colour needed, the `body` photo handles it. The page layout wrapper is `background: transparent`.
- Clerk component: wrap in a centred `--surface-1` card with `--radius-md` and `--shadow-lift`. The dark card floats over the city.
- Logo above the card: mark + wordmark in the dark style
- The city at night visible behind the sign-in card is the intended visual effect — city as context for where this product lives.

---

## Component-level changes

### `app/components/HeroSteps.tsx`
Current: Simple step animation in parchment style.
Replace with: Full **WorkflowCard** glass animation from `ui_kits/wchats/WorkflowCard.jsx` — the build-pipeline → live-widget cross-fade. The glass card (`--glass-bg`, `blur(24px)`) with the skyline reading through it is one of the approved glass use cases.

### `app/components/JourneyStepper.tsx`
Current: Step list with status icons.
Update: Dark surfaces, coral active step left-border, completed step `--green` checkmark, pending `--text-4`. Section headers UPPERCASE tracked.

### `app/components/StepSubtaskCard.tsx`
Current: Subtask expansion cards.
Update: `--surface-1` background, `--border-soft` border. Event log lines in JetBrains Mono `--text-3`.

### `app/components/SignOutTab.tsx`
Current: Red-accented slide-out tab (references `#ef4444` and `#1A0A0F` inline).
Update: Keep the mechanism; update to use `--red` and `--bg-deep` via design tokens. The slide-out tab is dark and coral-red accented — already close to the new palette.

### `app/components/UserAvatar.tsx`
Current: Clerks avatar component.
Update: Wrap with a `--lilac` ring (1px border `rgba(183,154,224,0.6)`).

---

## Rules that must never be broken (from `SKILL.md`)

1. **Never recreate the skyline as CSS.** Use `assets/skyline-w-chats.png`. It lives at `.claude/skills/wchats-design/assets/skyline-w-chats.png`. Copy to `apps/admin/public/` during the phase.
2. **No glass on dense data UI.** Agent card lists, KB rows, eval tables, conversation tables, code blocks — solid `--surface-1` only. Glass is for: hero card, stat tiles, transparent nav, eyebrow pills.
3. **Coral is primary. Cyan is rare.** Max two `--cyan` touches per screen.
4. **Violet-tinted shadows always.** `rgba(11,7,23,...)` base. Never `rgba(0,0,0,...)` alone.
5. **Sentence case everywhere.** Buttons: `Build your agent`, `Create agent`, `Sign in`. NEVER title-case.
6. **UPPERCASE TRACKED for micro-labels.** `font-size: 10.5-11px`, `letter-spacing: 0.12em`, `font-weight: 600`. Used for: step labels, metric labels, status chips, nav section dividers.
7. **No emoji in the W Chats voice.** 📄 only as document icon in citation/source chips. Emoji stays inside the agent widget.
8. **Respect `prefers-reduced-motion`.**

---

## What does NOT change in this milestone

- **Backend API** — zero changes. This is a pure UI milestone.
- **Clerk auth** — authentication flow stays as-is; only the visual wrapper changes.
- **Widget (`apps/widget/`)** — the Preact widget has its own design; the admin overhaul does not touch it.
- **Component logic** — data fetching, state management, auth hooks are preserved. We are changing styles, not behaviour.
- **Route structure** — no new routes, no route changes.

---

## Phase planning notes for when M11 becomes active

When planning this phase, break it into waves:

**Wave 1 — Token foundation + background**
- Replace `globals.css` token system (Parchment → Hillbrow at Dusk) — all surface, accent, border, text, shadow vars
- Add `body { background-image: url('/skyline-w-chats.png'); background-size: cover; background-position: center; background-attachment: fixed; background-color: #0B0717; }` — the skyline becomes the whole-app canvas in this single step
- Add `body::before` dark veil overlay (rgba(11,7,23,0.72) + accent radials) + `body::after` film grain
- Ensure `#__next { position: relative; z-index: 1; }` so Next.js root sits above the pseudo-elements
- Strip all `background: var(--bg)` / `background: var(--bg-deep)` from page/section/layout wrappers — replace with `background: transparent`
- Add Google Fonts import for Fraunces to `layout.tsx`
- Copy `skyline-w-chats.png`, `logo-mark.svg`, `wordmark.svg` to `apps/admin/public/`
- Update `TopNav.tsx` logo (SVG + wordmark, remove spin, remove Pixelify)
- After this wave: the city is visible behind the entire app on every route

**Wave 2 — Landing page**
- Full `app/page.tsx` rebuild: skyline hero, WorkflowCard glass animation, trust strip
- This is the highest-visibility surface and the north-star check

**Wave 3 — Agent dashboard + AgentCard**
- Greeting strip with Fraunces italic name
- AgentCard to match `ui_kits/wchats/AgentCard.jsx`

**Wave 4 — Agent journey screens**
- `[id]/page.tsx` JourneyStepper + StepSubtaskCard dark styling
- `[id]/soul/page.tsx` soul editor
- `[id]/ingest/page.tsx` ingest tab

**Wave 5 — Eval + Deploy + Settings**
- Stat tiles with glass (approved use case)
- Eval scenario table in solid `--surface-1`
- Deploy approval flow
- Settings form panel

**Wave 6 — Auth pages + QA pass**
- Sign-in / sign-up wrapper
- Full cross-screen consistency audit against `reference/wchats-hillbrow-at-dusk.html`
- Accessibility pass (contrast ratios on dark backgrounds; coral text on dark must clear WCAG AA)

---

## Design system quick-reference

Full quick-reference is in `SKILL.md` and `README.md`. One-para summary:

**The background is the photograph.** `skyline-w-chats.png` (Johannesburg skyline at dusk, 1672×941px) is `body`-level, fixed, full-cover, across every screen. A 72% indigo veil (`body::before`) makes content readable; film grain prevents flat OLED feel. All layout wrappers and page sections are `background: transparent` — the city shows through. Cards and panels use solid `--surface-1 #1E1638`. Glass (`--glass-bg rgba(30,22,56,0.55)` + `backdrop-filter: blur(24px)`) is used surgically: nav before scroll, stat tiles, workflow card, eyebrow pills — the city reads through them. Primary accent is **sunset coral** `#F4748C`. Secondary is **jacaranda lilac** `#B79AE0`. Tertiary **tower cyan** `#5EDFD3` is rare, max two touches per screen. Status: `--green #6FE8AA` / `--gold #F0C674` / `--red #FF8585` recalibrated for dark. Type: **Fraunces** (display) + **Inter** (body) + **JetBrains Mono** (numbers, IDs). Shadows always violet-tinted. Animation calm, no bouncing. Sentence case; UPPERCASE TRACKED for micro-labels.

---

*Context drop captured: 2026-05-25. M9 and M10 not yet implemented.*
*Design system: `.claude/skills/wchats-design/` (extracted from `W Chats Design System.zip`)*
*Canonical prototype: `.claude/skills/wchats-design/reference/wchats-hillbrow-at-dusk.html`*
