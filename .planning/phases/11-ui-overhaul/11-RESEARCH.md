# Phase 11: M11 — Admin UI End-to-End Overhaul - Research

**Researched:** 2026-05-25
**Domain:** Next.js 16 / React 19 admin UI, CSS custom properties, design system token migration
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **Background surface:** `skyline-w-chats.png` (1672×941px) is `body`-level, `background-attachment: fixed`, full-cover. Every screen renders on top of this photograph. Non-negotiable.
- **Token system:** Replace Parchment & Wine variables with Hillbrow at Dusk variables exactly as specified in the token migration table. Values are locked.
- **Typography stack:** Fraunces (display) + Inter (body) + JetBrains Mono (numbers/IDs). Add Fraunces via Google Fonts link. Remove Pixelify Sans / FungkyBrowDEMO.
- **Logo:** Replace spinning `w-chats-lettermann.png` + Pixelify wordmark with `logo-mark.svg` (30×30, static) + `wordmark.svg`. No spin animation.
- **Glass discipline:** Glass (`--glass-bg` + `backdrop-filter: blur(24px)`) only on: transparent nav before scroll, stat tiles, WorkflowCard, eyebrow pills. NEVER on dense data UI (tables, KB rows, eval tables, code blocks).
- **No solid backgrounds on layout wrappers:** Page/section/layout wrappers use `background: transparent`. Only cards and panels get `--surface-1` solid.
- **Sentence case for buttons/labels everywhere.** UPPERCASE TRACKED (`letter-spacing: 0.12em`) for micro-labels only.
- **Violet-tinted shadows always.** `rgba(11,7,23,...)` base. Never `rgba(0,0,0,...)` alone.
- **Max two `--cyan` touches per screen.**
- **No emoji except 📄 for document/citation chips.**
- **Backend API zero changes.** This is a pure UI milestone.
- **Clerk auth flow untouched.** Only visual wrapper changes.
- **Widget (`apps/widget/`) untouched.**
- **Component logic preserved.** Data fetching, state management, auth hooks unchanged. Style-only.
- **Route structure unchanged.** No new routes; no route removals.

### Claude's Discretion

- Wave sequencing within each wave (which tasks are atomic commits vs batched)
- Which inline JSX style objects to extract as named constants vs keep inline
- How to handle the Recharts chart color values (currently hardcoded hex) — update to new palette colors

### Deferred Ideas (OUT OF SCOPE)

- Any backend API changes
- New routes or new pages
- Widget redesign
- Animations beyond what's specified (no spring physics, no parallax)
- SSE-driven live log streaming changes
</user_constraints>

---

## Summary

M11 is a pure visual rearchitecture of `apps/admin/`. No backend changes, no logic changes, no new routes. Every page and component swaps from the Parchment & Wine light theme to the Hillbrow at Dusk dark system.

The project already has a complete, verified design system at `.claude/skills/wchats-design/` including a canonical CSS token file (`colors_and_type.css`), JSX component references (`ui_kits/wchats/`), preview HTML files for every component, and the canonical 4-screen prototype (`reference/wchats-hillbrow-at-dusk.html`). The skyline PNG, logo SVG, and wordmark SVG are all already present in `.claude/skills/wchats-design/assets/` — they just need to be copied to `apps/admin/public/`.

The admin codebase uses **inline `style={}` objects throughout** — there is no separate CSS file per component, no CSS modules, no Tailwind classes on the components being restyled. This means token migration is entirely a matter of updating CSS variable references and inline hex values in `globals.css` and component files. The only global CSS is `apps/admin/app/globals.css`, which holds all custom property definitions. Replacing the token block there propagates most of the visual change automatically for anything that already uses `var(--token)` references. Hardcoded hex values (notably in HeroSteps, eval chart colors, JourneyStepper) require file-by-file edits.

**Primary recommendation:** Execute as six waves matching CONTEXT.md's wave plan — token foundation first, then landing page, then authenticated screens in logical groups. The token replacement is the highest-leverage change; after Wave 1 the app will visually shift from light to dark across all routes that correctly use `var(--token)` references.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| CSS token system | Frontend Static (globals.css) | — | All design tokens live in a single CSS custom property block in globals.css; one file edit propagates to all consumers |
| Skyline background | Frontend Static (body CSS) | — | `background-image` on `body` in globals.css; `body::before` overlay and `body::after` grain also in globals.css |
| Font loading | Frontend Server (layout.tsx) | — | `next/font/google` for Inter/JetBrains Mono; Google Fonts `<link>` for Fraunces added to the `<head>` in layout.tsx |
| Logo/wordmark | Frontend Static (TopNav.tsx, page.tsx) | — | Static SVG assets in `/public`, referenced in `<img>` tags in nav and landing page header |
| Clerk appearance config | Frontend Server (layout.tsx) | — | `clerkAppearance` object in layout.tsx; hardcoded color values must update to new tokens |
| Component styles | Frontend Client (each .tsx) | — | Inline style objects in React components; each file needs targeted edits |
| Auth page visual wrapper | Frontend Client (sign-in/sign-up pages) | — | Transparent body wrapping + centred `--surface-1` card around Clerk component |
| WorkflowCard animation | Frontend Client (HeroSteps.tsx → new WorkflowCard) | — | Replace existing HeroSteps animation with WorkflowCard.jsx from ui_kits |

---

## Standard Stack

### Core (already installed — no new packages needed)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Next.js | 16.2.6 | App router, SSR, layout | Already in use |
| React | 19.2.0 | UI components | Already in use |
| @clerk/nextjs | ^7.3.5 | Auth components + appearance | Already in use |
| @tanstack/react-query | ^5.100.11 | Data fetching (untouched in M11) | Already in use |
| Recharts | ^3.8.1 | Eval charts (colors update only) | Already in use |
| lucide-react | ^1.16.0 | Icon set in UserAvatar + SignOutTab | Already in use |

[VERIFIED: apps/admin/package.json]

### No new packages required
M11 is purely CSS/JSX style changes. All dependencies are already installed.

**Google Fonts** is loaded via `<link>` tag in `layout.tsx` (not `next/font/google`) — Fraunces is a variable font with the `SOFT` and `WONK` axes that `next/font/google` does not support. The `<link>` approach is required. [CITED: 11-CONTEXT.md typography section]

---

## Architecture Patterns

### System Architecture Diagram

```
User request
     │
     ▼
layout.tsx (RootLayout)
  ├── ClerkProvider (appearance={clerkAppearance}) ← update colors here
  ├── <head> Google Fonts links ← add Fraunces here
  ├── <body> ← globals.css applies skyline background here
  │    ├── body { background-image: skyline-w-chats.png }
  │    ├── body::before { dark veil overlay }
  │    └── body::after { film grain }
  └── #__next { z-index: 1 }
       │
       ├── TopNav.tsx ← glass nav, new logo
       ├── page.tsx (landing) ← transparent hero, WorkflowCard
       ├── agents/layout.tsx ← transparent wrapper
       │    └── agents/page.tsx ← greeting strip, AgentCard grid
       ├── agents/new/page.tsx ← dark form panel
       ├── agents/[id]/layout.tsx ← JourneyStepper (dark)
       │    ├── agents/[id]/page.tsx ← dark subtask cards
       │    ├── agents/[id]/soul/page.tsx ← soul editor dark
       │    ├── agents/[id]/ingest/page.tsx ← upload zone dark
       │    ├── agents/[id]/eval/page.tsx ← glass stat tiles + dark table
       │    ├── agents/[id]/deploy/page.tsx ← dark deploy tabs
       │    └── agents/[id]/settings/page.tsx ← standard form panel
       └── sign-in / sign-up ← transparent wrapper + surface-1 card
```

### Recommended Project Structure (unchanged)
```
apps/admin/
├── app/
│   ├── globals.css          # ALL token changes land here
│   ├── layout.tsx           # Font loading + Clerk appearance update
│   ├── page.tsx             # Landing page rebuild
│   ├── components/          # TopNav, AgentCard, HeroSteps → WorkflowCard, etc.
│   └── agents/              # All agent pages (style changes only)
└── public/
    ├── skyline-w-chats.png  # COPY from .claude/skills/wchats-design/assets/
    ├── logo-mark.svg        # COPY from .claude/skills/wchats-design/assets/
    └── wordmark.svg         # COPY from .claude/skills/wchats-design/assets/
```

### Pattern 1: Token-First Global Change
**What:** Replace the entire `:root {}` block in `globals.css` with the Hillbrow at Dusk token system. Add `body` background rules and overlay pseudo-elements.
**When to use:** Wave 1 — the single highest-leverage change. After this commit, any component using `var(--accent)`, `var(--text-1)`, etc. automatically shows the new palette.
**Example:**
```css
/* Source: .claude/skills/wchats-design/colors_and_type.css */
:root {
  --bg-deep: #0B0717;
  --bg: #0E0B1E;
  --surface-1: #140E2A;  /* NOTE: CONTEXT.md specifies #140E2A, not #1E1638 */
  --accent: #F4748C;
  /* ... full token set from 11-CONTEXT.md token migration table */
}

body {
  background-image: url('/skyline-w-chats.png');
  background-size: cover;
  background-position: center center;
  background-attachment: fixed;
  background-repeat: no-repeat;
  background-color: #0B0717; /* fallback */
  font-family: var(--font-sans);
  color: var(--text-1);
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  z-index: 0;
  background:
    radial-gradient(ellipse 60% 40% at 80% 0%, rgba(244,116,140,0.07) 0%, transparent 50%),
    radial-gradient(ellipse 40% 30% at 0% 60%, rgba(183,154,224,0.05) 0%, transparent 50%),
    rgba(11, 7, 23, 0.72);
  pointer-events: none;
}

body::after {
  content: '';
  position: fixed; inset: 0;
  z-index: 0;
  pointer-events: none;
  opacity: 0.025;
  mix-blend-mode: overlay;
  background-image: url("data:image/svg+xml,..."); /* film grain SVG */
}

#__next { position: relative; z-index: 1; }
```

### Pattern 2: Transparent Layout Wrappers
**What:** Remove `background: var(--bg)` / `background: var(--bg-deep)` from page/section/layout wrappers. Replace with `background: transparent`.
**When to use:** Every layout wrapper, every section container, every page `<div>` that previously provided a background fill. The skyline fills the space instead.
**Files affected:** `layout.tsx` body, `agents/[id]/layout.tsx`, `agents/new/page.tsx`, `agents/page.tsx`, `page.tsx` main section, `sign-in` and `sign-up` page wrappers.

### Pattern 3: Hardcoded Hex Replacement
**What:** Some components use hardcoded hex values instead of CSS variable references. These must be updated file-by-file.
**Files with hardcoded values:**
- `HeroSteps.tsx` — `#16A34A`, `#ef4444`, `#D97706` (orange step color), `var(--orange-dim)`, `var(--green-solid)`. Must replace with new palette: active step → `--accent`/`--accent-dim`, done step → `--green`/`--green-bg`.
- `JourneyStepper.tsx` — `rgba(217,119,6,0.08)`, `rgba(217,119,6,0.2)`, `#D97706`, `#16A34A` (amber step state). Replace with coral for active: `var(--accent-dim)`, `var(--border-hard)`, `var(--accent)`, done: `var(--green)`.
- `eval/page.tsx` — Recharts line colors are hardcoded hex (`#B8860B`, `#7B1C3A`, `#4A7C59`, `#4A6080`). Update to new palette tokens or new accent shades from CONTEXT.md.
- `layout.tsx` — `clerkAppearance` object has all Parchment & Wine hex values hardcoded. Must update to Hillbrow at Dusk values.
- `deploy/page.tsx` — `DEFAULT_CONFIG` widget colors use Parchment & Wine values (`#7B1C3A`, `#FDF9F5`). Update to dark defaults.
- `globals.css` — `.sign-out-tab` uses `#1A0A0F` and `#ef4444`. Update to `var(--bg-deep)` and `var(--red)`.

### Pattern 4: Glass Nav Scroll Behaviour
**What:** The nav starts glass (`--glass-bg` + `backdrop-filter: blur(24px)`) and transitions to solid (`--bg-elev #181232`) when the user scrolls past the hero.
**When to use:** TopNav.tsx only — authenticated nav and landing page nav.
**Implementation:** `useEffect` scroll listener on `window` → toggle a CSS class or inline state. Or use CSS `scroll-driven-animations` (supported in modern browsers — but keep a JS fallback for reduced-motion).
**Note from CONTEXT.md:** The authenticated TopNav target spec uses `--surface-1` fill (solid dark), not glass. Glass-to-solid scroll transition applies to the landing page nav only.

### Pattern 5: Clerk Appearance Object Update
**What:** The `clerkAppearance` in `layout.tsx` contains all Parchment & Wine hex values. These must be updated to Hillbrow at Dusk values.
**Target values (from CONTEXT.md):**
```typescript
// Source: 11-CONTEXT.md
const clerkAppearance = {
  variables: {
    colorPrimary: '#F4748C',         // --accent
    colorBackground: '#140E2A',      // --surface-1
    colorNeutral: '#C4B8D8',         // --text-2
    colorText: '#F0EBF8',            // --text-1
    colorTextSecondary: '#C4B8D8',   // --text-2
    colorInputBackground: '#1E1638', // --surface-2
    colorInputText: '#F0EBF8',       // --text-1
    colorDanger: '#F87171',          // --red
    borderRadius: '14px',            // --radius-sm
    fontFamily: 'Inter, system-ui, sans-serif',
  },
  elements: {
    card: {
      background: '#140E2A',                    // --surface-1
      border: '1px solid rgba(196,154,232,0.18)', // --border
      boxShadow: '0 4px 12px rgba(0,0,0,0.35), 0 24px 48px rgba(11,7,23,0.6)', // --shadow-lift
      borderRadius: '20px',
    },
    // ... formButtonPrimary, formFieldInput → dark values
  },
}
```

### Anti-Patterns to Avoid

- **Adding glass to tables or KB row lists:** Dense data must use solid `--surface-1`. Glass creates readability issues on complex data.
- **Using `background: var(--bg)` or `background: var(--bg-deep)` on page/section wrappers:** These become transparent. The skyline fills the space.
- **Recreating the skyline as a CSS gradient:** The `--sky` gradient in `colors_and_type.css` is a fallback only, not a replacement.
- **Title-casing button labels:** `Create agent`, `Save`, `Run evaluations` — not `Create Agent`, `Save Changes`.
- **Neutral grey shadows:** `rgba(0,0,0,0.5)` is wrong. Use `rgba(11,7,23,0.6)` (violet-tinted).
- **More than two `--cyan` uses per screen.**
- **Spinning logo on new logo assets:** The new `logo-mark.svg` is static. Remove `.logo-spin` class from it.
- **Using `--font-pixelify` after Wave 1:** The `localFont` import for `FungkyBrowDEMO.otf` and the `--font-pixelify` variable must be removed from `layout.tsx`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Design token system | Custom CSS vars | `colors_and_type.css` from `.claude/skills/wchats-design/` | Already specced and verified in the design system |
| WorkflowCard animation | New animation component | `ui_kits/wchats/WorkflowCard.jsx` | Reference implementation exists and is tested visually |
| Nav component | New nav HTML | `ui_kits/wchats/Nav.jsx` as reference | Exact glass/blur/scroll-state behaviour is documented |
| AgentCard styling | Custom card CSS | `ui_kits/wchats/AgentCard.jsx` + `preview/comp-agent-card.html` | Spec fully detailed including hover, gradient bar, chip colours |
| Stat tile glass pattern | Custom glass CSS | `preview/comp-stat-card.html` | Glass tile implementation is in the design preview |
| Skyline background | CSS gradient | `assets/skyline-w-chats.png` | Rules say never recreate as CSS |

**Key insight:** The design system is already fully materialised. Every component has a reference HTML file in `preview/` and a JSX reference in `ui_kits/wchats/`. The planner should task the executor to read those references before implementing each component.

---

## Component Inventory and Change Scope

### Wave 1 — Token Foundation + Background (globals.css + layout.tsx)

| File | Change Type | Scope |
|------|-------------|-------|
| `apps/admin/app/globals.css` | Full `:root` block replacement + new `body` rules + overlay pseudo-elements + remove `.logo-spin` from new logo | HIGH |
| `apps/admin/app/layout.tsx` | Add Fraunces `<link>`, remove `localFont` FungkyBrowDEMO import, update `clerkAppearance` object, update `<body>` class list | MEDIUM |
| `apps/admin/public/skyline-w-chats.png` | **Copy from** `.claude/skills/wchats-design/assets/skyline-w-chats.png` | ASSET |
| `apps/admin/public/logo-mark.svg` | **Copy from** `.claude/skills/wchats-design/assets/logo-mark.svg` | ASSET |
| `apps/admin/public/wordmark.svg` | **Copy from** `.claude/skills/wchats-design/assets/wordmark.svg` | ASSET |
| `apps/admin/app/components/TopNav.tsx` | Replace logo (SVG+wordmark, remove spin), update nav token references, glass nav background | MEDIUM |
| `apps/admin/app/agents/[id]/layout.tsx` | Remove `background: var(--bg)` from the flex wrapper | TRIVIAL |

**After Wave 1:** The city is visible behind the entire app. All `var(--accent)` references coral. All `var(--text-1)` is light. Cards/panels using `--surface-1` are dark.

### Wave 2 — Landing Page (`app/page.tsx`)

| File | Change Type | Scope |
|------|-------------|-------|
| `apps/admin/app/page.tsx` | Full rebuild — glass nav header, transparent hero, Fraunces headline with strikethrough, coral CTA, trust strip, WorkflowCard glass animation in right column | HIGH |
| `apps/admin/app/components/HeroSteps.tsx` | Replace with `WorkflowCard.jsx` pattern from `ui_kits/wchats/WorkflowCard.jsx`; update hardcoded hex step colors | HIGH |

**Reference:** `ui_kits/wchats/Hero.jsx`, `ui_kits/wchats/WorkflowCard.jsx`, `ui_kits/wchats/Nav.jsx`
**Visual reference:** `reference/wchats-hillbrow-at-dusk.html` screen 1

### Wave 3 — Agents Dashboard + AgentCard

| File | Change Type | Scope |
|------|-------------|-------|
| `apps/admin/app/agents/page.tsx` | Add greeting strip (radial gradient + Fraunces italic name), transparent wrapper, agent grid `repeat(3, 1fr)`, empty state update | MEDIUM |
| `apps/admin/app/components/AgentCard.tsx` | Update to `--surface-1` bg, `--border-soft`, hover translateY(-2px) + gradient bar, new STATUS_COLORS using `-dim` vars, UPPERCASE tracked role label | MEDIUM |

**Reference:** `ui_kits/wchats/AgentCard.jsx`, `preview/comp-agent-card.html`
**Visual reference:** `reference/wchats-hillbrow-at-dusk.html` screen 2

### Wave 4 — Agent Journey Screens

| File | Change Type | Scope |
|------|-------------|-------|
| `apps/admin/app/agents/[id]/page.tsx` | Token fixes, transparent page wrapper, dark status cards | SMALL |
| `apps/admin/app/components/JourneyStepper.tsx` | Replace amber active state with coral (`--accent-dim`, `--accent`), done state `--green`, solid `--surface-1` aside bg | MEDIUM |
| `apps/admin/app/components/StepSubtaskCard.tsx` | Update `--surface-2` to `--surface-1`, update left-border coral/green | SMALL |
| `apps/admin/app/agents/new/page.tsx` | `--surface-1` form panel, `--surface-2` inputs, `--shadow-focus` | SMALL |
| `apps/admin/app/agents/[id]/soul/page.tsx` | `--surface-1` panel, `--surface-2` textareas, Fraunces italic coral for agent name display | SMALL |
| `apps/admin/app/agents/[id]/ingest/page.tsx` | Upload zone `--accent-dim` hover, document rows `--surface-1`, status chip update | SMALL |

### Wave 5 — Eval + Deploy + Settings

| File | Change Type | Scope |
|------|-------------|-------|
| `apps/admin/app/agents/[id]/eval/page.tsx` | Glass stat tiles for aggregate scores (add tile layout), Recharts line color update, scenario table solid `--surface-1`, pass/fail chip update, schedule strip update | MEDIUM |
| `apps/admin/app/agents/[id]/deploy/page.tsx` | Tab border update, `--surface-1` panels, embed code block `--surface-2`, status banner token update, widget preview `DEFAULT_CONFIG` dark default colors | MEDIUM |
| `apps/admin/app/agents/[id]/settings/page.tsx` | Standard form panel pattern — same as soul editor | SMALL |
| `apps/admin/app/agents/[id]/components/AlertsBanner.tsx` | Token update for banner colors | SMALL |

### Wave 6 — Auth Pages + QA

| File | Change Type | Scope |
|------|-------------|-------|
| `apps/admin/app/sign-in/[[...sign-in]]/page.tsx` | Transparent `<main>` wrapper, centred `--surface-1` card with logo above | SMALL |
| `apps/admin/app/sign-up/[[...sign-up]]/page.tsx` | Same as sign-in | SMALL |
| `apps/admin/app/components/UserAvatar.tsx` | Wrap with `--lilac` ring (1px border `rgba(183,154,224,0.6)`), update Lucide icon color from `#ef4444` to `var(--accent)` | SMALL |
| `apps/admin/app/components/SignOutTab.tsx` | Token update: `#1A0A0F` → `var(--bg-deep)`, `#ef4444` → `var(--red)` | TRIVIAL |
| QA cross-screen consistency audit | Read `reference/wchats-hillbrow-at-dusk.html` and compare each screen | AUDIT |

---

## Common Pitfalls

### Pitfall 1: `background-attachment: fixed` on iOS Safari
**What goes wrong:** `background-attachment: fixed` is not supported on iOS/mobile Safari. The background scrolls with content instead of staying fixed.
**Why it happens:** iOS Safari has a known limitation with fixed background attachments inside scrolling containers.
**How to avoid:** The CONTEXT.md spec uses `background-attachment: fixed` — this is the correct production spec for desktop. For M11 (portfolio/admin tool), this is acceptable. If mobile support is required later, wrap in a `@supports` query. This is not in scope for M11.
**Warning signs:** On mobile devices the skyline scrolls with content.

### Pitfall 2: `z-index` stack conflicts with `body::before` and `body::after`
**What goes wrong:** The `body::before` (veil, z-index 0) and `body::after` (grain, z-index 0) sit above page content if `#__next` doesn't have `position: relative; z-index: 1`.
**Why it happens:** Fixed pseudo-elements at `z-index: 0` can render above content that has no stacking context.
**How to avoid:** The rule `#__next { position: relative; z-index: 1; }` must be in `globals.css`. This is specified in CONTEXT.md. Verify it is present before testing.
**Warning signs:** Page content invisible or partially obscured.

### Pitfall 3: Hardcoded hex values in components survive the token replacement
**What goes wrong:** After replacing globals.css tokens, some components still render Parchment & Wine colors because they use hardcoded hex (`#7B1C3A`, `#16A34A`, `#D97706`) instead of CSS variable references.
**Why it happens:** The current codebase has a mix of CSS variable usage and hardcoded hex values.
**How to avoid:** For each wave, grep the files being changed for hex color literals (`#[0-9A-Fa-f]{3,6}`) and replace with the appropriate CSS variable.
**Files with known hardcoded values (confirmed by code audit):**
- `HeroSteps.tsx`: `#16A34A` (green), `#ef4444` (red), orange-themed step state uses `var(--orange)` and `var(--orange-dim)` which will not exist in new token set
- `JourneyStepper.tsx`: `rgba(217,119,6,...)`, `#D97706`, `#16A34A`
- `eval/page.tsx`: Recharts line colors `#B8860B`, `#7B1C3A`, `#4A7C59`, `#4A6080`
- `layout.tsx`: All `clerkAppearance` values
- `deploy/page.tsx`: `DEFAULT_CONFIG` widget colors
- `globals.css`: `.sign-out-tab` uses `#1A0A0F`, `rgba(239, 68, 68, ...)`, `#ef4444`
- `AgentCard.tsx`: STATUS_COLORS uses `var(--green-bg)`, `var(--amber-bg)`, `var(--red-bg)` — these OLD vars must be mapped to new tokens (`--green-bg` changes from `#F0FDF4` to `rgba(111,232,170,0.10)`)

### Pitfall 4: `--font-pixelify` references after font removal
**What goes wrong:** Removing `localFont` for FungkyBrowDEMO without removing all `var(--font-pixelify)` usages causes visible fallback to system sans-serif.
**Why it happens:** `layout.tsx` currently sets `--font-pixelify` via the `localFont` variable; `page.tsx` and `TopNav.tsx` both use `fontFamily: 'var(--font-pixelify)'` in inline styles.
**How to avoid:** In Wave 1, search for `--font-pixelify` and `font-pixelify` across all files. Replace every occurrence. Two confirmed locations: `page.tsx` (landing nav wordmark span) and `TopNav.tsx` (logo span).

### Pitfall 5: `--green-bg` and `--amber-bg` token name changes break status chip logic
**What goes wrong:** Old tokens `--green-bg` (was `#F0FDF4`) and `--amber-bg` (was `#FEF3C7`) are referenced in STATUS_COLORS maps in `AgentCard.tsx`, `agents/[id]/page.tsx`, and `deploy/page.tsx`. The new token for green backgrounds is `--green-bg: rgba(111,232,170,0.10)` and for amber/gold is `--gold-bg`. This is a rename from `--amber` / `--amber-bg` to `--gold` / `--gold-bg`.
**How to avoid:** The STATUS_COLORS maps and any inline styles using `var(--amber)` or `var(--amber-bg)` must be updated. `--amber` in Hillbrow at Dusk means the warm building-window amber (`#E8A87C`), not the status warning color. Status warning is `--gold: #FBBF24` / `--gold-dim`. Check every `--amber-bg` and `--amber` usage.

### Pitfall 6: Google Fonts Fraunces variable axes in next/font/google
**What goes wrong:** Attempting to load Fraunces via `next/font/google` fails or loses the `SOFT` and `WONK` axes because Next.js font optimization doesn't always pass all variable font axes.
**Why it happens:** `next/font/google` does not support all custom variable font axes.
**How to avoid:** Load Fraunces via a standard `<link>` tag in `layout.tsx` (inside the `<head>` using Next.js `<Head>` or in the layout `<html>` element via metadata). The existing `Inter` and `JetBrains Mono` can remain as `next/font/google` imports. [ASSUMED — verify whether Next.js 16 adds support for custom font axes]

### Pitfall 7: Recharts chart renders incorrectly on dark background
**What goes wrong:** Recharts `CartesianGrid`, `XAxis`, `YAxis`, `Tooltip` use light default colors that become invisible on dark backgrounds.
**Why it happens:** Recharts defaults are designed for light mode.
**How to avoid:** The eval page already passes custom `stroke`, `fill`, and `contentStyle` to Recharts components. These just need color value updates to the new palette. `CartesianGrid stroke="var(--border)"` is already there — `--border` will update automatically once globals.css is replaced.

---

## Code Examples

### CSS Token Override Pattern (globals.css)
```css
/* Source: .claude/skills/wchats-design/colors_and_type.css + 11-CONTEXT.md */
:root {
  /* Surfaces */
  --bg-deep:    #0B0717;
  --bg:         #0E0B1E;
  --surface-1:  #140E2A;
  --surface-2:  #1E1638;
  --surface-3:  #382860;

  /* Accents */
  --accent:       #F4748C;
  --accent-hover: #F5899E;
  --accent-deep:  #C4435C;
  --accent-dim:   rgba(244,116,140,0.10);

  /* New accent palette */
  --lilac:     #B79AE0;
  --lilac-dim: rgba(183,154,224,0.12);
  --cyan:      #5EDFD3;
  --amber:     #E8A87C;

  /* Borders */
  --border:      rgba(196,154,232,0.18);
  --border-soft: rgba(196,154,232,0.10);
  --border-hard: rgba(244,116,140,0.32);
  --glass-bg:    rgba(30,22,56,0.55);
  --glass-blur:  blur(24px) saturate(140%);
  --glass-border: rgba(244,232,220,0.10);

  /* Text */
  --text-1: #F0EBF8;
  --text-2: #C4B8D8;
  --text-3: #7B6B98;
  --text-4: #4A3D62;

  /* Status — new dark-calibrated values */
  --green:    #34D399;
  --green-bg: rgba(52,211,153,0.12);  /* renamed from --green-bg light #F0FDF4 */
  --red:      #F87171;
  --red-bg:   rgba(248,113,113,0.12); /* renamed from --red-bg light #FEF2F2 */
  --gold:     #FBBF24;                /* NEW name — was --amber for status */
  --gold-bg:  rgba(251,191,36,0.12);  /* was --amber-bg */

  /* Shadows */
  --shadow-card:  0 1px 2px rgba(0,0,0,0.3), 0 4px 16px rgba(11,7,23,0.5);
  --shadow-lift:  0 4px 12px rgba(0,0,0,0.35), 0 24px 48px rgba(11,7,23,0.6);
  --shadow-focus: 0 0 0 3px rgba(244,116,140,0.28);
  --shadow-glow:  0 0 32px rgba(244,116,140,0.18);

  /* Typography */
  --font-display: 'Fraunces', Georgia, serif;
  --font-sans:    'Inter', system-ui, sans-serif;
  --font-mono:    'JetBrains Mono', ui-monospace, monospace;
  /* --font-pixelify REMOVED */
}
```

### Glass Nav Pattern
```tsx
// Source: ui_kits/wchats/Nav.jsx + 11-CONTEXT.md
// Authenticated nav: solid --surface-1
<nav style={{
  height: '56px',
  background: 'var(--surface-1)',
  borderBottom: '1px solid var(--border-soft)',
  // ...rest of nav styles unchanged
}} />

// Landing page nav: glass before scroll, solid after
const [scrolled, setScrolled] = useState(false)
// useEffect scroll listener sets scrolled = window.scrollY > 60
<nav style={{
  background: scrolled ? 'var(--bg-elev)' : 'var(--glass-bg)',
  backdropFilter: scrolled ? 'none' : 'var(--glass-blur)',
  transition: 'background 0.3s, backdrop-filter 0.3s',
}} />
```

### Fraunces Font Loading (layout.tsx)
```tsx
// Source: 11-CONTEXT.md typography section
// Add to <head> in RootLayout — replaces localFont for FungkyBrowDEMO
export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT,WONK@9..144,300..800,0..100,0..1&family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className={`${inter.variable} ${mono.variable}`}>
        {/* fungkyBrow variable removed */}
```

### AgentCard Hover Gradient Bar Pattern
```tsx
// Source: preview/comp-agent-card.html + ui_kits/wchats/AgentCard.jsx + 11-CONTEXT.md
// Hover: translateY(-2px), --border, --shadow-card, 1px top gradient bar
<div
  style={{
    background: 'var(--surface-1)',
    border: '1px solid var(--border-soft)',
    borderRadius: 'var(--radius-md)',
    boxShadow: 'var(--shadow-card)',
    transition: 'transform 0.2s, box-shadow 0.2s, border-color 0.2s',
    // On hover: add data-hovered or CSS :hover class
    // 1px top gradient bar via ::before — needs CSS class or style injection
    // Simplest pattern: conditional borderTop from hover state:
  }}
  onMouseEnter={e => e.currentTarget.style.transform = 'translateY(-2px)'}
  onMouseLeave={e => e.currentTarget.style.transform = 'translateY(0)'}
/>
```

### Micro-Label UPPERCASE Tracked Pattern
```tsx
// Source: .claude/skills/wchats-design/colors_and_type.css .micro-label class
// Used for: step labels, metric labels, status chips, nav section dividers
<span style={{
  fontSize: '10.5px',         // --t-micro
  fontWeight: 600,
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
  color: 'var(--text-3)',
}}>
  STATUS
</span>
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Pixelify Sans / FungkyBrowDEMO logo font | Fraunces variable font + SVG wordmark | M11 | Wordmark is now a proper SVG; no font dependency for logo |
| Spinning logo PNG (`spin-cw` animation) | Static SVG logo mark | M11 | Cleaner, no animation jank |
| Parchment & Wine light theme (`--bg: #F0E8E0`) | Hillbrow at Dusk dark theme (`--bg-deep: #0B0717`) | M11 | Full dark-mode visual system |
| Solid parchment body background | Skyline photograph as persistent body background | M11 | Brand-defining visual identity |
| Orange/amber active step state in JourneyStepper | Coral active step state (`--accent`) | M11 | Removes dependency on `--orange` and `--orange-dim` which no longer exist in token set |

**Deprecated/outdated in this milestone:**
- `--font-pixelify` CSS variable and `FungkyBrowDEMO.otf` font file: remove from layout.tsx and globals.css
- `--orange` and `--orange-dim` tokens: used only in HeroSteps.tsx and JourneyStepper.tsx; both get replaced with coral accent in M11
- `.logo-spin` animation: remove from globals.css (or keep but not applied to new logo)
- `--green-solid` token: used in HeroSteps and AgentCard; maps to `--green` in new token set (or define `--green-solid` as alias)
- `--red-bg: #FEF2F2` and `--green-bg: #F0FDF4` and `--amber-bg: #FEF3C7`: all become rgba-based dark variants

---

## Token Migration Reference (from CONTEXT.md — executor working reference)

### Critical name changes that BREAK existing code
These old token names are used in components but change meaning in the new system:

| Old token name | Old value | New equivalent | New value | Action |
|----------------|-----------|----------------|-----------|--------|
| `--amber` | `#92400E` (status warning) | `--gold` | `#FBBF24` | Replace `var(--amber)` with `var(--gold)` in all status contexts |
| `--amber-bg` | `#FEF3C7` | `--gold-dim` | `rgba(251,191,36,0.12)` | Replace `var(--amber-bg)` with `var(--gold-dim)` |
| `--green-bg` | `#F0FDF4` | `--green-bg` | `rgba(52,211,153,0.12)` | Same name, different value — auto-update via globals.css |
| `--green-solid` | `#16A34A` | `--green` | `#34D399` | Replace `var(--green-solid)` with `var(--green)` |
| `--red-bg` | `#FEF2F2` | `--red-bg` | `rgba(248,113,113,0.12)` | Same name, different value — auto-update via globals.css |
| `--orange` | `#EA580C` | *(removed)* | — | Replace with `var(--accent)` or `var(--gold)` depending on context |
| `--orange-dim` | `rgba(234,88,12,0.10)` | *(removed)* | — | Replace with `var(--accent-dim)` |
| `--amber` (new) | — | `#E8A87C` | warm building windows | This is NOT a status color in new system |

---

## Open Questions (RESOLVED)

1. **`--green-solid` in AgentCard STATUS_COLORS and HeroSteps**
   - What we know: `--green-solid: #16A34A` is used as the "done" step fill color in HeroSteps and the green status chip fill.
   - What's unclear: Whether to define `--green-solid` as a token alias in globals.css pointing to the new `--green: #34D399`, or replace all usages with `var(--green)`.
   - Recommendation: Replace usages with `var(--green)` and remove `--green-solid` from globals.css. The new `--green` value is the dark-mode recalibrated equivalent.

2. **Fraunces WONK axis in Google Fonts URL**
   - What we know: CONTEXT.md specifies `SOFT,WONK` in the font URL. The `colors_and_type.css` only specifies `opsz,wght,SOFT`.
   - What's unclear: Whether the WONK axis is necessary for the specific styles used in this phase.
   - Recommendation: Include WONK in the font URL as CONTEXT.md specifies; it doesn't hurt if unused. [ASSUMED — verify WONK axis availability in Google Fonts for Fraunces]

3. **`background-attachment: fixed` and `position: sticky` interaction**
   - What we know: `background-attachment: fixed` on `body` may create stacking context issues with `position: sticky` elements (like TopNav).
   - What's unclear: Whether Next.js 16 + React 19's rendering creates any specific issues.
   - Recommendation: Test after Wave 1. The sticky nav should work correctly if `#__next { position: relative; z-index: 1; }` is set. [ASSUMED — no known conflicts documented]

4. **Recharts chart line colors for eval page**
   - What we know: Current colors are `#B8860B` (faithfulness), `#7B1C3A` (answer relevancy), `#4A7C59` (context precision), `#4A6080` (context recall).
   - What's unclear: The exact new colors are not specified in CONTEXT.md — just that charts use `--surface-1` background.
   - Recommendation: Map to the new palette — faithfulness: `--gold (#FBBF24)`, answer_relevancy: `--accent (#F4748C)`, context_precision: `--green (#34D399)`, context_recall: `--lilac (#B79AE0)`. These four cover the four design system accent colors distinctly.

---

## Environment Availability

Step 2.6: SKIPPED. M11 is pure Next.js admin UI changes. No external tools, CLI utilities, runtimes beyond Node.js/pnpm (already confirmed operational), or external services are required. The design system assets are local files — no downloads needed.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | None (no automated UI tests in admin app) |
| Config file | none |
| Quick run command | `cd apps/admin && pnpm run build` (TypeScript compile check) |
| Full suite command | `cd apps/admin && pnpm run build && pnpm run lint` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| UI-01 | globals.css token replacement compiles without errors | build | `pnpm run build` in apps/admin | ✅ |
| UI-02 | Skyline PNG loads at `/skyline-w-chats.png` | manual-visual | Visual inspection in browser | N/A |
| UI-03 | No TypeScript type errors from style object changes | build | `pnpm run build` | ✅ |
| UI-04 | No `var(--font-pixelify)` references remain | lint/grep | `grep -r "font-pixelify" apps/admin/app/` | N/A |
| UI-05 | All `--amber-bg` usages replaced | lint/grep | `grep -r "amber-bg" apps/admin/app/` | N/A |
| UI-06 | No `--orange` or `--orange-dim` usages remain | lint/grep | `grep -r "\-\-orange" apps/admin/app/` | N/A |

### Sampling Rate
- **Per task commit:** `pnpm run build` in `apps/admin` — TypeScript compile check
- **Per wave merge:** `pnpm run build && pnpm run lint` — full compile + lint
- **Phase gate:** Visual audit against `reference/wchats-hillbrow-at-dusk.html` before `/gsd-verify-work`

### Wave 0 Gaps
None — no test infrastructure gaps. The admin app has no automated visual tests; all validation is build-time + manual visual audit. This is acceptable for a pure CSS/style phase.

---

## Security Domain

M11 is a pure visual restyle. No authentication logic changes, no new API endpoints, no data handling changes. ASVS categories V2/V3/V4/V6 are not applicable.

V5 (Input Validation): Not applicable — no new input fields are being added.

**Security domain: SKIPPED** — this phase makes zero changes to any security-relevant code paths.

---

## Proposed Acceptance Criteria for M11

Since M11 has no formal requirement IDs in REQUIREMENTS.md, the following are the proposed acceptance criteria:

| ID | Criterion |
|----|-----------|
| UI-01 | Johannesburg skyline photograph is visible behind every route — landing, agents, eval, deploy, auth pages |
| UI-02 | Parchment & Wine color values (`#F0E8E0`, `#7B1C3A`, `#FFFCF9`) are absent from `globals.css` `:root` block |
| UI-03 | The `--font-pixelify` variable and FungkyBrowDEMO font load are absent from the codebase |
| UI-04 | Fraunces loads via Google Fonts `<link>` tag in `layout.tsx` |
| UI-05 | Logo mark is the coral SVG square; no spin animation |
| UI-06 | `pnpm run build` in `apps/admin` exits zero |
| UI-07 | Landing page renders: glass nav, Fraunces headline with strikethrough, trust strip, WorkflowCard animation |
| UI-08 | Agent card uses `--surface-1` background with no glass, with coral/green/gold status chips |
| UI-09 | Eval page stat tiles use glass (`--glass-bg` + `backdrop-filter: blur`) |
| UI-10 | Sign-in / sign-up: skyline visible behind the Clerk card; card uses `--surface-1` dark background |
| UI-11 | All inline style objects using hardcoded hex from Parchment & Wine palette have been replaced |
| UI-12 | Cross-screen visual audit against `reference/wchats-hillbrow-at-dusk.html` passes (subjective — reviewer confirms match) |

---

## Sources

### Primary (HIGH confidence)
- `11-CONTEXT.md` — canonical design contract for this phase (user-authored locked decisions)
- `.claude/skills/wchats-design/SKILL.md` — design system rules
- `.claude/skills/wchats-design/colors_and_type.css` — canonical token values [VERIFIED: file read]
- `apps/admin/app/globals.css` — current token state (Parchment & Wine) [VERIFIED: file read]
- `apps/admin/app/layout.tsx` — current font loading and Clerk appearance [VERIFIED: file read]
- All component files in `apps/admin/app/` — verified by direct file read

### Secondary (MEDIUM confidence)
- `apps/admin/package.json` — dependency versions [VERIFIED: file read]
- Design system preview HTML files in `.claude/skills/wchats-design/preview/` — component reference implementations [VERIFIED: directory listing]
- JSX reference components in `.claude/skills/wchats-design/ui_kits/wchats/` [VERIFIED: directory listing]

### Tertiary / Assumed
- Next.js 16 `next/font/google` support for Fraunces variable font axes — [ASSUMED: assumed not fully supported based on known limitations; use `<link>` approach as specified]
- `background-attachment: fixed` + `position: sticky` interaction in Next.js 16 — [ASSUMED: no known conflicts documented; test in browser after Wave 1]

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Next.js 16's `next/font/google` does not reliably support all variable font axes (SOFT, WONK) for Fraunces | Font Loading pattern | Low — CONTEXT.md already specifies `<link>` approach which bypasses this entirely |
| A2 | `background-attachment: fixed` on `body` does not conflict with `position: sticky` on TopNav in the Next.js 16 app router | Wave 1 / pitfalls | Medium — if it does conflict, the nav z-index may need adjustment; catch in Wave 1 manual test |
| A3 | Recharts eval chart line colors should map to `--gold`, `--accent`, `--green`, `--lilac` for the four metrics | Wave 5 / Open Questions | Low — purely aesthetic, easy to change if user prefers different mapping |

---

## Metadata

**Confidence breakdown:**
- Token migration: HIGH — CONTEXT.md provides exact old→new values; codebase fully audited
- Component change scope: HIGH — every file read and change type classified
- Pitfalls: HIGH — all identified from direct code inspection, not training knowledge
- WorkflowCard animation pattern: MEDIUM — JSX reference exists in ui_kits; exact adaptation to Next.js needs implementation-time attention

**Research date:** 2026-05-25
**Valid until:** 2026-07-25 (stable domain — design system and codebase both locked)
