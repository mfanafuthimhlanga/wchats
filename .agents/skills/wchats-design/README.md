# W Chats — Hillbrow at Dusk Design System

> A skyline-derived dark palette for a serious customer-support agent platform that ships to small business owners.

---

## What is W Chats?

**W Chats** is a multi-tenant RAG platform that lets non-technical small business owners ship a customer service agent in under 30 minutes. Drop in business documents (PDFs, sheets, websites, FAQs), answer plain-language questions about the business, and the platform handles structure-aware ingestion, per-tenant Neon Postgres provisioning, hybrid retrieval, evaluation, red-teaming, and a 20kb embeddable widget.

It is at milestone **M8** (pre-deployment checklist + human validation). The platform is **open source** (AGPL-3.0). Operating entity: **Mzansi Agentive (Pty) Ltd**, Johannesburg.

### The audience is split

The landing page must serve two audiences simultaneously:

- **Non-technical operators** (the half the product is built for) — need to feel like the product is approachable.
- **Technical evaluators** (engineers, founders, AI/ML practitioners) — need to feel like there is real architectural rigour underneath.

The way the design serves both: **marketing-clear copy on the left, a verifiable working demo on the right.** Glass-card workflow animation visualises the build pipeline (provision → ingest → evaluate → ship) cross-fading into a live widget that answers a grounded RAG query with citations.

### The brand metaphor — "Hillbrow at Dusk"

The whole palette is derived from the photo of Johannesburg's Hillbrow skyline at sunset. **Deep indigo** sky (page surface), **sunset coral** pink band in the clouds (primary accent), **jacaranda lilac** flowering trees (secondary), **tower cyan** Sentech & Hillbrow observation-deck lights (rare highlight), **building amber** for lit windows at dusk (warmth). The actual photo is the hero background.

Brand entity: Mzansi Agentive — "Mzansi" is colloquial South African for "South Africa". The product is rooted in Johannesburg, and the visual identity refuses generic SaaS dark mode in favour of a specific place at a specific time.

---

## Source materials provided

This system was built from these inputs (preserved in `reference/`):

| File | What it is |
|---|---|
| `uploads/skyline-w-chats.png` | The Johannesburg skyline at sunset — copied to `assets/skyline-w-chats.png`. **Actual page background** for the hero, not a CSS recreation. |
| `uploads/skyline+hillbrowdusk-mockup.png` | A mockup of the landing page with the skyline as the bg — copied to `assets/reference-landing-mockup.png` for comparison. |
| `uploads/wchats-hillbrow-at-dusk.html` | The canonical four-screen prototype (Landing, Agents Home, Agents/New, Agent Detail). Source of truth for tokens, type, components. |
| `uploads/widget-animation-live.html` | Reference implementation of the build-pipeline → live-widget animation in the *prior* parchment-and-wine palette. Mechanics/phasing/JS carry over; styling restyled to Hillbrow at Dusk. |

No Figma, no GitHub repo. The HTML prototypes are the system.

---

## Index of this folder

```
README.md                       — this file (start here)
SKILL.md                        — Agent Skill manifest (cross-compatible with Claude Code)
colors_and_type.css             — design tokens + semantic CSS variables (LOAD THIS FIRST)

assets/
  skyline-w-chats.png           — hero background photo (1672×941)
  reference-landing-mockup.png  — the look-and-feel target
  logo-mark.svg                 — the coral-gradient square chatbox mark
  wordmark.svg                  — "w.chats" lockup with logomark
  icons/                        — Feather-style 24px line icons used in the prototype

reference/
  wchats-hillbrow-at-dusk.html  — the canonical 4-screen prototype (read for system rules)
  widget-animation-live.html    — animation mechanics reference

preview/                        — design system cards (each ~700px wide, registered for the gallery)

ui_kits/
  wchats/                       — the landing + product UI kit
    README.md
    index.html                  — interactive demo, click-thru
    Nav.jsx                     — transparent + solid top navs
    Hero.jsx                    — full hero with skyline + workflow card
    WorkflowCard.jsx            — the glass build-pipeline animation
    Widget.jsx                  — the embeddable chat widget
    HowItWorks.jsx              — 4-step grid
    ShipSection.jsx             — 2-col features
    Footer.jsx                  — landing footer
    Buttons.jsx                 — btn variants
    Chips.jsx                   — status chips
    StatCard.jsx                — glass stat tile
    AgentCard.jsx               — live/testing/draft cards
    components.jsx              — barrel export to window
```

---

## CONTENT FUNDAMENTALS

### Voice

**Confident, technical, slightly self-aware.** The product respects the buyer's intelligence — both audiences. Copy is not breezy; it is not "Hey there!" Where bubbly tone is used, it is *the agent inside the widget* speaking to a customer ("Hi 👋 I'm Maya…"). The W Chats voice itself is engineer-speaks-to-engineer with one eye on the operator.

- **You/your** addressing the reader. Direct. *"You answer questions about your business in plain language."*
- **We** is used sparingly and only when describing the platform's behaviour. *"We deliver to Northside…"* — that's the *agent* speaking, not W Chats.
- **No marketing fluff.** No "revolutionary", "next-generation", "AI-powered". The product earns trust through specificity, not adjectives.
- **Numbers are evidence.** `<30 min signup-to-deployed`, `>0.85 faithfulness target`, `0 critical red-team threshold`. Always include the operator (less-than, greater-than) and the unit.

### Headline architecture

A two-line setup with an italic-coral turn:

> The chat widget [strike]is the hard part.[/strike]
> *The layer underneath is.*

The pattern: **state a common assumption, strike it through with a coral 3px line, replace with the actual truth in italic coral Fraunces.** Italics aren't decoration — they're a *correction* or *the real point*.

More examples from the prototype:

- *"Four steps. **One pipeline.** No code."* (italic-coral on "One pipeline")
- *"Agents that **earn** the right to ship."* (italic-coral on "earn")
- *"Good evening, **Fana**"* (italic-coral on the user's name in the greeting)

### Casing

- **Sentence case** for everything in the UI. Buttons: `Build your agent`, `Sign in`, `Start free →`. Section titles: `How it works`, `Pending your review`. NEVER `Build Your Agent`.
- **UPPERCASE TRACKED** (0.12em letter-spacing, 600 weight, 10.5–11px) for micro-labels: `OPEN SOURCE · v0.4.2 · M8`, `BUILD PIPELINE`, `7D CONV.`, `FAITH.`. This is the dominant "structural" type style.
- **Mono lowercase** for IDs, agent names in technical context, timestamps: `agent.kgalema`, `14:08 SAST`, `<30min`, `0.93`.
- **PascalCase** for agent display names: `Kgalema`, `Naledi`, `Themba`, `Amahle`, `Sipho`, `Bongi`. These are Zulu/Sotho/Xhosa names — a deliberate brand signal of Johannesburg-rooted identity.

### Punctuation & rhythm

- **Em-dash for the turn.** Heavy use of em-dashes mid-sentence to set up "but here's the real thing". *"W Chats does the serious work — structure-aware ingestion, hybrid retrieval, continuous evaluation, weekly red teaming — so small business owners ship…"*
- **Middle dot (·) as separator** in technical metadata. `agent.kgalema · v1`, `Mon–Fri 9am–6pm · Sat 9am–2pm`. Not commas, not slashes, not pipes.
- **Ampersand (&)** is fine in tight contexts (`Returns & refunds`, `FAQ & ordering`). Use sparingly.

### Emoji

- **Allowed inside the agent widget** when the agent itself is speaking warmly to an end customer: *"Hi 👋 I'm Maya"*. The 👋 hand and 🎉 are the canonical two.
- **Allowed as a *document* icon** in source/citation chips: `📄 Delivery Policy.pdf`. The file-emoji is treated as the icon, not decoration.
- **Never in W Chats's own marketing voice.** No emoji in headlines, sub-copy, buttons, nav, or labels.

### Specific copy library

```
Hero eyebrow:    ● OPEN SOURCE · v0.4.2 · M8
Hero head:       The chat widget [strike]is the hard part.[/strike] [em]The layer underneath is.[/em]
Hero sub:        W Chats does the serious work — structure-aware ingestion, hybrid
                 retrieval, continuous evaluation, weekly red teaming — so small
                 business owners ship a customer service agent that is actually
                 safe to deploy.
Primary CTA:     Build your agent →
Ghost CTA:       ▶ Watch the build  2:18

Trust strip:     <30 min  /  >0.85  /  0 critical
                 Signup to deployed / Faithfulness target / Red team threshold

Agent greeting:  Hi 👋 I'm Maya, Lakewood Bakery's assistant. How can I help you today?
Agent answer:    We deliver to Northside, Eastpark & Downtown. Mon–Fri 9am–6pm · Sat 9am–2pm.
                 No Sunday delivery.

Footer tag:      OPEN SOURCE · AGPL-3.0
Footer rights:   © 2026 Mzansi Agentive (Pty) Ltd
```

---

## VISUAL FOUNDATIONS

### Palette

A six-axis dark system: indigo surfaces, coral primary, lilac secondary, cyan tertiary (rare), amber warm, plus green/gold/red status. **Full token list in `colors_and_type.css`.**

| Axis | Anchor | Used for |
|---|---|---|
| Indigo surfaces | `--bg-deep #0B0717` → `--surface-3 #382860` | All page/card surfaces. Five steps of depth. |
| Coral (primary) | `--accent #F4748C` | CTAs, italic-Fraunces accents, focus rings, "live" status, eyebrow underlines. |
| Lilac (secondary) | `--lilac #B79AE0` | Avatars, secondary stat tile, "soul / shape" feature accents, jacaranda hits. |
| Cyan (rare highlight) | `--cyan #5EDFD3` | One or two highlights per screen, max. Tower lights. |
| Amber (warm) | `--amber #E8A87C` | Building windows, warm illustrations, "Sipho" agent icon. |
| Status | `--green --gold --red` | Live / testing / failure |

### Type

- **Display:** Fraunces (variable, axes `opsz` 9–144, `wght` 300–800, `SOFT` 0–100).
  - Hero headline → `font-variation-settings: "opsz" 144, "SOFT" 30` (sharper terminals).
  - Italic accent words → 300 italic + `SOFT 100` (softer terminals). The italic must look visibly different from upright — softer, more flowing.
- **Body:** Inter (300/400/500/600/700/800). With `font-feature-settings: "ss01","cv11","cv02"` for the cleaner alternates.
- **Mono:** JetBrains Mono (400/500/600). For numbers, IDs, timestamps, code, event-log lines, breadcrumb IDs, citation chips.

### Background system

- The **default page** is `--bg-deep` with two radial accent washes (top-right coral 8%, mid-left lilac 6%). Subtle.
- The **landing hero** has the literal skyline photograph (`assets/skyline-w-chats.png`) as its background, with a top vignette darkening the sky behind the headline (`linear-gradient(180deg, rgba(11,7,23,0.65) 0%, … 100%)`) and a side radial vignette for edge softening. The buildings must remain visible.
- **Section dividers** are 1px `--border-soft` horizontal lines — never thicker.
- **Dashboard greeting strip** uses two off-axis radial gradients (coral right, lilac top-left, both ~6–8% opacity) over `--bg`. Brings warmth without dominating.

### Animation

- **Easing:** `cubic-bezier(0.4, 0, 0.2, 1)` for the default snap (`--ease`); `cubic-bezier(0.16, 1, 0.3, 1)` for panel reveals (`--ease-out`, more deceleration).
- **Cross-fade between major UI states:** 0.6s opacity. Used to swap build-pipeline ↔ live-widget inside the hero card.
- **Pulse-soft** (1.6–1.8s, ease-in-out, infinite) for "live" dots — opacity oscillates 0.7 → 1, with a transient ring shadow.
- **Spin-soft** (3s linear, infinite) on the currently-active step circle — a soft glow ring expanding then dissipating.
- **Typing dots:** 6×6px circles, bounce 1.2s ease-in-out, staggered 0s / 0.2s / 0.4s.
- **Shimmer on source chips** during retrieval: `background-position` slide across a `--surface-2` → `--surface-3` gradient, ~1.5s.
- **No bouncing UI.** No spring overshoots on cards/buttons. The brand is calm-on-rigour.
- **Respect `prefers-reduced-motion: reduce`** — animations should fall back to the final state.

### Hover states

- **Buttons:** primary CTAs bump to `--accent-hover` (lighter pink) and gain a soft `translateY(-1px)` plus a wider glow (32px → 40px). Ghost buttons get a `--surface-1` fill and `--border-hard` (coral) rim.
- **Nav links:** background `--surface-1`, text `--text-1`.
- **Cards:** lift `translateY(-1px) to (-2px)`, border `--border-soft` → `--border`, gain `--shadow-card`.
- **Agent cards** also flash a 1px top gradient bar (transparent → coral → transparent, 0.6 opacity).
- **Icon-only buttons:** color `--text-3` → `--text-1`, border `--border` → `--border-hard`.

### Press states

- Buttons: `transform: translateY(0)` (cancel hover lift), no scale-down — keep the coral fills calm.
- Nav links: background dims to `--accent-dim`.

### Borders & dividers

- **Default border:** `rgba(196, 154, 232, 0.18)` — lavender at 18% opacity. Coloured, not neutral grey.
- **Soft (hairline):** `rgba(196, 154, 232, 0.10)` — for in-card dividers, footer separators, secondary card surfaces.
- **Hard (coral):** `rgba(244, 116, 140, 0.32)` — focus rings, hover rims, active accents.
- **Warm (amber):** `rgba(232, 168, 124, 0.22)` — used very rarely, for amber-tinted illustration containers.

### Shadows

Violet-tinted, **never neutral black**. Three steps:

- `--shadow-card`: `0 1px 2px rgba(0,0,0,0.3), 0 4px 16px rgba(11,7,23,0.5)` — default card lift.
- `--shadow-lift`: `0 4px 12px rgba(0,0,0,0.35), 0 24px 48px rgba(11,7,23,0.6)` — hero card, modals, browser frames.
- `--shadow-glow`: `0 0 32px rgba(244, 116, 140, 0.18)` — coral atmospheric glow under active CTAs and the hero workflow card.
- `--shadow-focus`: `0 0 0 3px rgba(244, 116, 140, 0.28)` — focus rings.

### Glass

Used **surgically**, not everywhere:

- Hero workflow card (the primary glass element — skyline reads through it subtly).
- Dashboard stat tiles (small glass tiles over the subtle radial gradient strip).
- Transparent top nav over the hero.
- Eyebrow pills.

Never on dense data UI (agent cards, KB lists, eval tables). Glass kills legibility on numbers. **Solid `--surface-1` with `--border-soft` always wins.**

```css
background: var(--glass-bg);              /* rgba(30, 22, 56, 0.55) */
backdrop-filter: var(--glass-blur);       /* blur(24px) saturate(140%) */
-webkit-backdrop-filter: var(--glass-blur);
border: 1px solid var(--glass-border);    /* rgba(244, 232, 220, 0.10) */
```

### Layout rules

- **Max content width:** 1400px (1500px outer wrapper). The hero is full-bleed; content stays inside the 1400 lane.
- **Hero split:** 50/50 (`minmax(0,1fr) minmax(0,0.9fr)`) on desktop, single-col below 1100px. Workflow card is `max-width: 460px`, right-aligned in its lane.
- **Section padding:** 96px vertical / 56px horizontal on desktop; 64px / 24px on mobile.
- **Card padding:** 22–28px. Glass cards 24px. Form panels 28px.
- **Gap between cards in a grid:** 16px. Grid columns: `repeat(4, 1fr)` (how-it-works, stats), `repeat(3, 1fr)` (agents), `repeat(2, 1fr)` (activity feed, ship section).

### Transparency & blur

- **When to use blur:** glass cards, transparent nav, hero eyebrow pill, modal scrim. Always `blur(24px) saturate(140%)` — the saturation lift is what stops glass looking grey.
- **When NOT to:** any surface with text smaller than 14px sitting on top of busy content (KB lists, eval tables, code blocks). Use solid `--surface-1` instead.

### Corner radii

| Token | Value | Where |
|---|---|---|
| `--radius-xs` | 8px | Buttons, inputs, icon-buttons, status chips |
| `--radius-sm` | 14px | Small cards, panels, demo step rows, icon boxes |
| `--radius-md` | 20px | Large cards, hero card, modals, agent cards |
| `--radius-lg` | 28px | Feature blocks (used rarely) |
| `--radius-pill` | 100px | Status chips, eyebrow pills, filter pills, footer tag |

Avatars are perfect circles. Logo mark is **8px** rounded square (not pill, not circle).

### Imagery vibe

- **Cool indigo with warm coral/amber pops.** Photos used in product should mirror this: dusk light, warm building windows against a cool sky, jacaranda purples.
- **No b&w, no flat-illustration vendors** (no undraw, no Storyset). Real photography or restrained product UI screenshots only.
- **No gradient-heavy AI-art.** The brand is rooted in a real place; AI illustration breaks that.

---

## ICONOGRAPHY

The prototype uses **Feather-style line icons** drawn inline as SVGs at 14–24px, `stroke-width: 2` (occasionally 2.2 or 2.5 on small marks for legibility), `stroke-linecap: round`, `stroke-linejoin: round`. The icons in `assets/icons/` are exact copies of the ones used in the reference HTML — extracted and made standalone.

Iconography rules:

- **Stroke icons only.** No filled-shape icons except for status dots and pulse rings.
- **Stroke width:** 2 default, 2.2 for "medium-weight emphasis" (ship-section icons, hero eyebrow chevrons), 2.5 for small marks (button arrows, check ticks).
- **Color:** matches the surrounding text colour by default (`currentColor`). On the coral icon-box pattern (44×44, `--accent-dim` background), the icon itself is `--accent`.
- **Sizing:** 14px (button arrows, chip dots), 16px (button chevrons, ship-item icons), 18px (status ticks), 20px (agent-card icon-box), 22px (how-card large icons), 24px (nav and feature illustrations).

### Substitution note

The W Chats prototype draws every icon inline. There is no icon font or sprite. We've extracted the canonical ones into `assets/icons/` as standalone SVGs. **If you need an icon not in the set, the closest CDN substitute is [Lucide](https://lucide.dev/)** — it is the maintained fork of Feather and matches the stroke style exactly. Pull from `https://unpkg.com/lucide-static@latest/icons/<name>.svg` and flag the substitution.

### Emoji as iconography

- 📄 as the **document icon** in source/citation chips. Don't replace it with an SVG; it's the canonical mark.
- 👋 and 🎉 inside the agent's own messages (not the W Chats voice).
- No other emoji.

### Unicode glyphs

- `·` (middle dot U+00B7) — the canonical metadata separator. Used everywhere.
- `→` (rightwards arrow U+2192) — button arrows. *But the prototype also uses SVG chevrons inside `.btn-primary`; both are acceptable.*
- `&` — fine, used in tight contexts.
- `&copy;` — footer.

### Logo

The logo mark is a **30×30px square, 8px-rounded, coral gradient (`--accent` → `--accent-deep`), chat-bubble glyph inside in white at 14px**. A subtle inner highlight gradient sits on top (white 15% → transparent at 40%). Coral-rim and coral-drop shadow.

Wordmark: `w.chats` in Fraunces 500, opsz 144, SOFT 50, letter-spacing -0.015em. The `.` is `--accent` coral.

Saved as `assets/logo-mark.svg` (mark only) and `assets/wordmark.svg` (full lockup).

---

## How to use this system

1. Drop `colors_and_type.css` into your `<head>` (after Google Fonts).
2. Set `<body class="">` — the defaults apply.
3. For the landing hero, use `assets/skyline-w-chats.png` as the section background with the documented vignette layers (see `ui_kits/wchats/Hero.jsx`).
4. For new components, **match an existing one**. The vocabulary is established: glass for the hero card, solid `--surface-1` for everything dense, coral CTAs, lilac for secondary accents, italic-Fraunces for the brand turn.
5. Run `ui_kits/wchats/index.html` to see the click-through demo.

---

## Caveats

- **Font substitution risk:** Fraunces, Inter, and JetBrains Mono are all on Google Fonts and load there. No local `.ttf` files are bundled — if you need offline assets, download from Google Fonts.
- **Imagery:** the only real photograph in the system is the skyline. Anywhere else "imagery" is needed, the prototype uses gradient washes and SVG silhouettes — *not* stock photography.
- **No GitHub repo or Figma file** was provided; everything in this system is reverse-engineered from the two reference HTML files. If you have access to the W Chats codebase, the README/SKILL should be cross-checked against `app/styles/tokens.*`.

---

*Built for Mzansi Agentive (Pty) Ltd · M8 · 2026*
