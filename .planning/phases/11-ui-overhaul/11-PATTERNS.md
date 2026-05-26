# Phase 11 — Pattern Map: Admin UI Hillbrow at Dusk Overhaul

**Mapped:** 2026-05-26
**Files analysed:** 22 (new/modified files across 6 waves)
**Analogs found:** 22 / 22 (all files read from codebase; design system refs loaded)

---

## File Classification

| New/Modified File | Role | Data Flow | Change Type | Closest Analog |
|-------------------|------|-----------|-------------|----------------|
| `apps/admin/app/globals.css` | config/tokens | static | FULL_REPLACEMENT `:root` block + new `body` rules | `colors_and_type.css` (canonical source) |
| `apps/admin/app/layout.tsx` | config/provider | static | TARGETED_EDITS | `colors_and_type.css` + RESEARCH.md pattern 5 |
| `apps/admin/public/skyline-w-chats.png` | asset | static | ASSET_COPY | `.claude/skills/wchats-design/assets/skyline-w-chats.png` |
| `apps/admin/public/logo-mark.svg` | asset | static | ASSET_COPY | `.claude/skills/wchats-design/assets/logo-mark.svg` |
| `apps/admin/public/wordmark.svg` | asset | static | ASSET_COPY | `.claude/skills/wchats-design/assets/wordmark.svg` |
| `apps/admin/app/components/TopNav.tsx` | component | request-response | TARGETED_EDITS | `ui_kits/wchats/Nav.jsx` |
| `apps/admin/app/agents/[id]/layout.tsx` | layout | request-response | TRIVIAL | self (remove `background: var(--bg)`) |
| `apps/admin/app/page.tsx` | page | request-response | FULL_REPLACEMENT | `ui_kits/wchats/Hero.jsx` |
| `apps/admin/app/components/HeroSteps.tsx` | component | event-driven | FULL_REPLACEMENT | `ui_kits/wchats/WorkflowCard.jsx` |
| `apps/admin/app/agents/page.tsx` | page | CRUD | TARGETED_EDITS | self (greeting strip add + grid update) |
| `apps/admin/app/components/AgentCard.tsx` | component | CRUD | TARGETED_EDITS | `ui_kits/wchats/components.jsx` (AgentCard target) |
| `apps/admin/app/agents/[id]/page.tsx` | page | CRUD | TOKEN_ONLY + STATUS_MAP | self (STATUS_COLORS rename) |
| `apps/admin/app/components/JourneyStepper.tsx` | component | request-response | TARGETED_EDITS | self (hardcoded hex → tokens) |
| `apps/admin/app/components/StepSubtaskCard.tsx` | component | request-response | TOKEN_ONLY | self (`--surface-2` → `--surface-1`) |
| `apps/admin/app/agents/new/page.tsx` | page | CRUD | TOKEN_ONLY | `agents/[id]/soul/page.tsx` (same form panel pattern) |
| `apps/admin/app/agents/[id]/soul/page.tsx` | page | CRUD | TOKEN_ONLY | self (textareas + label upgrade) |
| `apps/admin/app/agents/[id]/ingest/page.tsx` | page | file-I/O | TARGETED_EDITS | self (PARSE_STATUS_COLORS rename + upload zone) |
| `apps/admin/app/agents/[id]/eval/page.tsx` | page | CRUD | TARGETED_EDITS | self (glass stat tiles add + Recharts colours) |
| `apps/admin/app/agents/[id]/deploy/page.tsx` | page | CRUD | TARGETED_EDITS | self (DEFAULT_CONFIG dark + token update) |
| `apps/admin/app/agents/[id]/settings/page.tsx` | page | CRUD | TOKEN_ONLY | `agents/[id]/soul/page.tsx` |
| `apps/admin/app/sign-in/[[...sign-in]]/page.tsx` | page | request-response | TARGETED_EDITS | `agents/[id]/sign-up/[[...sign-up]]/page.tsx` |
| `apps/admin/app/sign-up/[[...sign-up]]/page.tsx` | page | request-response | TARGETED_EDITS | `agents/[id]/sign-in/[[...sign-in]]/page.tsx` |
| `apps/admin/app/components/UserAvatar.tsx` | component | static | TRIVIAL | self (add lilac ring, change icon colour) |
| `apps/admin/app/components/SignOutTab.tsx` | component | static | TOKEN_ONLY | self (hardcoded hex → tokens in globals.css) |

---

## Pattern Assignments

### Wave 1 — Token Foundation + Background

---

#### `apps/admin/app/globals.css`
**Role:** CSS token config
**Change type:** FULL_REPLACEMENT of `:root {}` block + new `body` / `body::before` / `body::after` rules

**Analog:** `.claude/skills/wchats-design/colors_and_type.css` (lines 1–286 — canonical source)

**Current `:root` pattern** (lines 3–58):
```css
:root {
  --bg: #F0E8E0;
  --surface-1: #FFFCF9;
  --surface-2: #F7F0EA;
  --surface-3: #EDE3D8;
  --border: #D9CCBE;
  --border-soft: #EDE3D8;
  --border-hard: #B8906A;
  --accent: #7B1C3A;
  --accent-hover: #5E1229;
  --accent-deep: #3D0B1F;
  --accent-dim: rgba(123, 28, 58, 0.08);
  --gold: #B8860B;
  --text-1: #1A0A0F;
  --text-2: #4A2030;
  --text-3: #8A6060;
  --text-4: #C4A0A0;
  --shadow-card: 0 1px 2px rgba(74,32,48,0.04), 0 4px 12px rgba(74,32,48,0.06);
  --shadow-focus: 0 0 0 3px rgba(123,28,58,0.18);
  --red: #B91C1C;  --red-bg: #FEF2F2;
  --green: #166534; --green-bg: #F0FDF4; --green-solid: #16A34A;
  --amber: #92400E; --amber-bg: #FEF3C7;
  --orange: #EA580C; --orange-dim: rgba(234, 88, 12, 0.10);
  --font-sans: 'Inter', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;
}
```

**Current `body` pattern** (lines 103–109):
```css
body {
  font-family: var(--font-sans);
  background: var(--bg);
  color: var(--text-1);
  -webkit-font-smoothing: antialiased;
}
```

**Current animation / sign-out** (lines 119–207):
- `.logo-spin { animation: spin-cw 6s linear infinite; }` — remove or keep but not applied
- `.sign-out-tab { background: #1A0A0F; border: 1px solid rgba(239,68,68,0.25); }` — hardcoded hex, replace with tokens

**Target `:root` pattern** — copy verbatim from `.claude/skills/wchats-design/colors_and_type.css` lines 15–175. Key additions vs. current:
```css
:root {
  /* Surfaces */
  --bg-deep: #0B0717;
  --bg: #110C24;
  --bg-elev: #181232;
  --surface-1: #1E1638;
  --surface-2: #2A1E4A;
  --surface-3: #382860;

  /* Glass */
  --glass-bg: rgba(30, 22, 56, 0.55);
  --glass-bg-strong: rgba(40, 28, 72, 0.70);
  --glass-border: rgba(244, 232, 220, 0.10);
  --glass-blur: blur(24px) saturate(140%);

  /* Accents */
  --accent: #F4748C;
  --lilac: #B79AE0;  --lilac-dim: rgba(183,154,224,0.12);
  --cyan: #5EDFD3;
  --amber: #E8A87C;  /* NOTE: amber = building warmth in new system, NOT status warning */

  /* Status — renamed */
  --green: #6FE8AA;  --green-bg: rgba(111,232,170,0.10);
  --red: #FF8585;    --red-bg: rgba(255,133,133,0.10);
  --gold: #F0C674;   --gold-bg: rgba(240,198,116,0.10);
  /* --green-solid, --amber-bg, --orange, --orange-dim: REMOVED */

  /* Typography additions */
  --font-display: "Fraunces", Georgia, serif;
  /* --font-pixelify: REMOVED */
}
```

**Target `body` pattern** (from CONTEXT.md lines 26–66):
```css
body {
  background-image: url('/skyline-w-chats.png');
  background-size: cover;
  background-position: center center;
  background-attachment: fixed;
  background-repeat: no-repeat;
  background-color: #0B0717; /* fallback */
  font-family: var(--font-sans);
  color: var(--text-1);
  -webkit-font-smoothing: antialiased;
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
  background-image: url("data:image/svg+xml,..."); /* film grain SVG — copy from CONTEXT.md line 62 */
}

#__next { position: relative; z-index: 1; }
```

**Target `.sign-out-tab` pattern** — replace hardcoded hex with tokens:
```css
.sign-out-tab {
  background: var(--bg-deep);           /* was: #1A0A0F */
  border: 1px solid rgba(var(--red), 0.25); /* was: rgba(239,68,68,0.25) */
}
.sign-out-tab::before { background: var(--red); } /* was: #ef4444 */
```

**Pitfalls:**
- `--green-solid`, `--orange`, `--orange-dim`, `--amber-bg`, `--amber` (status) are used by 5+ components — removing them from `:root` without updating those files FIRST will break rendering. Wave 1 should remove from `:root` while providing migration targets in each wave.
- Keep `@keyframes pulse-ring` but update its colour from `rgba(234,88,12,...)` to `rgba(244,116,140,...)` (coral).
- Keep all other `@keyframes` unchanged — they are not colour-dependent.

---

#### `apps/admin/app/layout.tsx`
**Role:** Root server layout — font loading + Clerk appearance
**Change type:** TARGETED_EDITS

**Analog:** RESEARCH.md Pattern 5 + CONTEXT.md typography section

**Current font loading pattern** (lines 1–11):
```tsx
import localFont from 'next/font/local'
const fungkyBrow = localFont({ src: '../public/fonts/FungkyBrowDEMO.otf', variable: '--font-pixelify' })
// ...
<body className={`${inter.variable} ${mono.variable} ${fungkyBrow.variable}`}>
```

**Current Clerk appearance pattern** (lines 14–75):
```tsx
const clerkAppearance = {
  variables: {
    colorPrimary: '#7B1C3A',
    colorBackground: '#FFFCF9',
    colorNeutral: '#4A2030',
    colorText: '#1A0A0F',
    colorInputBackground: '#F7F0EA',
    colorDanger: '#B91C1C',
  },
  elements: {
    card: {
      background: '#FFFCF9',
      border: '1px solid #D9CCBE',
      boxShadow: '0 1px 2px rgba(74,32,48,0.04), ...',
    },
    formButtonPrimary: { background: '#7B1C3A' },
    formFieldInput: { background: '#F7F0EA', border: '1px solid #D9CCBE', color: '#1A0A0F' },
  },
}
```

**Target font loading pattern** (CONTEXT.md lines 192–198):
```tsx
// Remove: import localFont / fungkyBrow const / fungkyBrow.variable from className
// Add to <html> <head> via Next.js metadata or direct <link> in layout body:
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
```

**Target Clerk appearance pattern** (RESEARCH.md Pattern 5):
```tsx
const clerkAppearance = {
  variables: {
    colorPrimary: '#F4748C',          // --accent
    colorBackground: '#1E1638',       // --surface-1
    colorNeutral: '#C4B8D8',          // --text-2
    colorText: '#F0EBF8',             // --text-1 (NOTE: colors_and_type uses #F4EDE5 — use CONTEXT.md value)
    colorTextSecondary: '#C4B8D8',    // --text-2
    colorInputBackground: '#2A1E4A',  // --surface-2
    colorInputText: '#F0EBF8',        // --text-1
    colorDanger: '#FF8585',           // --red
    borderRadius: '14px',
    fontFamily: 'Inter, system-ui, sans-serif',
  },
  elements: {
    card: {
      background: '#1E1638',
      border: '1px solid rgba(196,154,232,0.18)',    // --border
      boxShadow: '0 4px 12px rgba(0,0,0,0.35), 0 24px 48px rgba(11,7,23,0.6)',  // --shadow-lift
      borderRadius: '20px',
    },
    formButtonPrimary: { background: '#F4748C', color: '#0B0717' },
    formFieldInput: {
      background: '#2A1E4A',
      border: '1px solid rgba(196,154,232,0.18)',
      color: '#F0EBF8',
      borderRadius: '8px',
    },
    userButtonAvatarBox: { background: '#1E1638', borderRadius: '8px' },
    userButtonTrigger: { background: '#1E1638', borderRadius: '8px', padding: '4px' },
    userButtonPopoverCard: {
      background: '#1E1638',
      border: '1px solid rgba(196,154,232,0.18)',
    },
  },
}
```

**Pitfall:** Next.js App Router does not support `<head>` children directly in `RootLayout` body — use the `<html>` element or add the Fonts link in the `<head>` metadata export. The `<link>` tag for Fraunces cannot go through `next/font/google` because SOFT and WONK axes are not supported. Place directly in `RootLayout` JSX as `<head>` content, or use Next.js metadata `links` property.

---

#### `apps/admin/app/components/TopNav.tsx`
**Role:** Authenticated top nav — client component
**Change type:** TARGETED_EDITS

**Analog:** `ui_kits/wchats/Nav.jsx` (target structure), `ui_kits/wchats/components.jsx` (Logo pattern)

**Current pattern** (lines 12–71):
```tsx
<nav style={{
  height: '56px',
  background: 'var(--bg)',           // ← REPLACE with var(--surface-1)
  borderBottom: '1px solid var(--border-soft)',
  // ...
}}>
  <img
    src="/w-chats-lettermann.png"    // ← REPLACE with logo-mark.svg
    style={{ animation: 'spin-cw 4s linear infinite' }}  // ← REMOVE animation
  />
  <span style={{ fontFamily: 'var(--font-pixelify)' }}>  // ← REPLACE with wordmark.svg
    Chats
  </span>
  {/* Nav links already use var(--accent-dim) / var(--accent) — these will auto-update */}
  {/* UserAvatar — wrap with lilac ring in UserAvatar.tsx */}
```

**Target pattern:**
```tsx
<nav style={{
  height: '56px',
  background: 'var(--surface-1)',            // dark solid — authenticated nav is not glass
  borderBottom: '1px solid var(--border-soft)',
  // ...
}}>
  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginRight: '32px' }}>
    <img src="/logo-mark.svg" alt="W Chats" style={{ width: '30px', height: '30px' }} />
    {/* No animation. No span with --font-pixelify. */}
    <img src="/wordmark.svg" alt="w.chats" style={{ height: '20px' }} />
  </div>
  {/* Nav links: active state already uses var(--accent-dim)/var(--accent) — no change needed
      Inactive: var(--text-2) → verify this becomes light enough on dark nav
      Hover: add onMouseEnter/Leave or CSS class for var(--surface-2) background + var(--text-1) */}
```

**Pitfall:** `--font-pixelify` is used in the `<span>` for "Chats" text. This span is fully replaced by `wordmark.svg`. Search for `fontFamily: 'var(--font-pixelify)'` in TopNav.tsx (line 37) and page.tsx (line 61) — both must be replaced.

---

#### `apps/admin/app/agents/[id]/layout.tsx`
**Role:** Agent detail layout wrapper
**Change type:** TRIVIAL

**Current pattern** (line 205):
```tsx
<div style={{
  display: 'flex',
  minHeight: 'calc(100vh - 56px)',
  fontFamily: 'var(--font-sans)',
  background: 'var(--bg)',    // ← REMOVE — background becomes transparent
}}>
```

**Target pattern:**
```tsx
<div style={{
  display: 'flex',
  minHeight: 'calc(100vh - 56px)',
  fontFamily: 'var(--font-sans)',
  background: 'transparent',  // skyline shows through
}}>
```

**Pitfall:** None — trivial single-property change.

---

### Wave 2 — Landing Page

---

#### `apps/admin/app/page.tsx`
**Role:** Public landing page
**Change type:** FULL_REPLACEMENT

**Analog:** `ui_kits/wchats/Hero.jsx` (structure), `ui_kits/wchats/Nav.jsx` (landing nav)

**Current pattern** (lines 30–139):
```tsx
<main style={{ minHeight: '100vh', background: 'var(--bg)', fontFamily: 'var(--font-sans)' }}>
  <header style={{ background: 'var(--bg)', position: 'sticky' }}>
    <img src="/w-chats-lettermann.png" style={{ animation: 'spin-cw 4s linear infinite' }} />
    <span style={{ fontFamily: 'var(--font-pixelify)' }}>Chats</span>
  </header>
  <section style={{ padding: '80px 32px' }}>
    <h1 style={{ fontSize: '26px', fontWeight: 800 }}>Ship a customer support agent...</h1>
    <HeroSteps />
  </section>
</main>
```

**Target pattern** (from CONTEXT.md "app/page.tsx" spec + ui_kits/wchats/Hero.jsx):
```tsx
<main style={{ background: 'transparent', fontFamily: 'var(--font-sans)' }}>
  {/* Landing nav — glass before scroll, solid after */}
  <header style={{
    background: scrolled ? 'var(--bg-elev)' : 'var(--glass-bg)',
    backdropFilter: scrolled ? 'none' : 'var(--glass-blur)',
    transition: 'background 0.3s, backdrop-filter 0.3s',
    borderBottom: scrolled ? '1px solid var(--border-soft)' : 'none',
    position: 'sticky', top: 0, zIndex: 100,
  }}>
    <img src="/logo-mark.svg" style={{ width: '30px', height: '30px' }} />
    <img src="/wordmark.svg" style={{ height: '20px' }} />
  </header>

  {/* Hero — transparent, city shows through */}
  <section style={{ background: 'transparent', padding: '80px 32px' }}>
    {/* Eyebrow pill */}
    <div style={{ background: 'var(--glass-bg)', backdropFilter: 'var(--glass-blur)',
                  border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-pill)',
                  padding: '4px 14px', display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10.5px', fontWeight: 600,
                     letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-3)' }}>
        OPEN SOURCE · v0.4.2 · M8
      </span>
    </div>

    {/* Fraunces headline with strikethrough */}
    <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 400,
                 fontVariationSettings: '"opsz" 144, "SOFT" 30',
                 letterSpacing: '-0.035em', lineHeight: 0.98 }}>
      The chat widget{' '}
      <span style={{ textDecoration: 'line-through', textDecorationColor: 'var(--accent)',
                     textDecorationThickness: '3px', color: 'var(--text-3)' }}>
        is the hard part.
      </span>
      {' '}
      <em style={{ fontStyle: 'italic', fontWeight: 300, color: 'var(--accent)',
                   fontVariationSettings: '"opsz" 144, "SOFT" 100' }}>
        The layer underneath is.
      </em>
    </h1>

    {/* CTA row */}
    <div style={{ display: 'flex', gap: '12px' }}>
      <Link href="/sign-up" style={{ background: 'var(--accent)', color: 'var(--text-on-accent)',
                                     borderRadius: 'var(--radius-sm)', padding: '14px 28px',
                                     fontWeight: 600 }}>
        Build your agent →
      </Link>
      <button style={{ background: 'transparent', border: '1px solid var(--border)',
                       color: 'var(--text-2)', borderRadius: 'var(--radius-sm)', padding: '14px 28px' }}>
        ▶ Watch the build  2:18
      </button>
    </div>

    {/* Trust strip — JetBrains Mono numbers, UPPERCASE labels */}
    {/* Right col — WorkflowCard (replaces HeroSteps) */}
    <HeroSteps />  {/* now renders WorkflowCard internally */}
  </section>
</main>
```

**Pitfall:** `background: 'var(--bg)'` on both `<main>` and `<header>` must become `'transparent'`. The scroll listener for the glass/solid nav toggle must be added via `useEffect` — requires `'use client'` on page.tsx or extraction to a client sub-component.

---

#### `apps/admin/app/components/HeroSteps.tsx`
**Role:** Animated right-column component for landing page
**Change type:** FULL_REPLACEMENT (logic + styles)

**Analog:** `ui_kits/wchats/WorkflowCard.jsx` (target structure, 19s loop, phase cross-fade)

**Current hardcoded hex / removed tokens** (in renderStepCard, lines 298–407):
```tsx
// Step card border:
border: isActive ? '1.5px solid var(--orange)' : isDone ? '1px solid var(--green-solid)' : ...
// Step card box-shadow:
boxShadow: isActive ? 'var(--shadow-lift), 0 0 0 3px var(--orange-dim)' : ...
// Circle background active:
background: 'var(--orange-dim)', color: 'var(--orange)', border: '2px solid var(--orange)'
// Circle background done:
background: 'var(--green-solid)'  // → becomes var(--green)
// Live dot:
background: 'var(--orange)'       // → becomes var(--accent)
// Connector line done:
background: 'var(--green-solid)'  // → becomes var(--green)
// Widget live dot:
background: 'var(--green-solid)', boxShadow: '0 0 6px rgba(22,163,74,0.6)'  // → var(--green)
```

**Target pattern** — copy WorkflowCard.jsx's CSS-class-based step state system:
```tsx
// Step card — active state uses coral, not amber/orange:
border: isActive ? '1.5px solid var(--accent)' : isDone ? '1px solid var(--green)' : '1px solid var(--border-soft)'
boxShadow: isActive ? 'var(--shadow-lift), 0 0 0 3px var(--accent-dim)' : ...
// Circle — active:
background: 'var(--accent-dim)', color: 'var(--accent)', border: '2px solid var(--accent)'
// Circle — done:
background: 'var(--green)', color: '#fff'
// Live dot: background: 'var(--accent)'
// Widget header: background: 'var(--accent)' (already correct)
// Widget live dot: background: 'var(--green)'
// Widget messages area: background: 'var(--surface-2)' (no change — token value updates)

// For the citation chip border: was rgba(123,28,58,0.15) → rgba(244,116,140,0.15)
// For renderVerified border: was rgba(22,163,74,0.25) → use var(--green-border) = rgba(111,232,170,0.25)
// For renderStripe pay button done: was 'var(--green-solid)' → 'var(--green)'
// For receipt PAID chip: background: 'var(--green-bg)', color: 'var(--green)' (same token names, new values auto-update)
```

**Pulse ring keyframe update** in globals.css:
```css
/* Old: */
@keyframes pulse-ring {
  0%   { box-shadow: 0 0 0 0   rgba(234, 88, 12, 0.50); }
  60%  { box-shadow: 0 0 0 8px rgba(234, 88, 12, 0);    }
  100% { box-shadow: 0 0 0 0   rgba(234, 88, 12, 0);    }
}
/* New: */
@keyframes pulse-ring {
  0%   { box-shadow: 0 0 0 0   rgba(244, 116, 140, 0.50); }
  60%  { box-shadow: 0 0 0 8px rgba(244, 116, 140, 0);    }
  100% { box-shadow: 0 0 0 0   rgba(244, 116, 140, 0);    }
}
```

**Pitfall:** `var(--orange)`, `var(--orange-dim)`, `var(--green-solid)` will be undefined after globals.css update. Every occurrence in this file must be replaced before Wave 1 completes, or the component will render with no border/bg on active steps.

---

### Wave 3 — Agents Dashboard + AgentCard

---

#### `apps/admin/app/agents/page.tsx`
**Role:** Authenticated agents dashboard
**Change type:** TARGETED_EDITS

**Analog:** self (add greeting strip, update grid columns, transparent wrapper)

**Current wrapper pattern** (lines 146–153):
```tsx
<div style={{ padding: '40px 32px', maxWidth: '1180px', margin: '0 auto' }}>
```

**Current grid pattern** (lines 232–244):
```tsx
<div style={{
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
  gap: '20px',
}}>
```

**Target wrapper pattern** — transparent, greeting strip added above existing content:
```tsx
{/* Greeting strip — radial gradients, Fraunces italic name */}
<div style={{
  background: 'var(--bg)',
  backgroundImage: `
    radial-gradient(ellipse 60% 40% at 80% 0%, rgba(244,116,140,0.08) 0%, transparent 50%),
    radial-gradient(ellipse 40% 30% at 0% 60%, rgba(183,154,224,0.06) 0%, transparent 50%)`,
  padding: '32px 32px 24px',
}}>
  <p style={{ fontSize: '10.5px', fontWeight: 600, letterSpacing: '0.12em',
              textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: '8px' }}>
    {/* UPPERCASE TRACKED micro-label: time + agent count */}
  </p>
  <h1 style={{ fontFamily: 'var(--font-display)', fontWeight: 400,
               fontVariationSettings: '"opsz" 144, "SOFT" 30', fontSize: '32px' }}>
    Good evening,{' '}
    <em style={{ fontStyle: 'italic', fontWeight: 300, color: 'var(--accent)',
                 fontVariationSettings: '"opsz" 144, "SOFT" 100' }}>
      {userName}
    </em>
  </h1>
</div>

{/* Grid — fixed 3 columns */}
<div style={{
  display: 'grid',
  gridTemplateColumns: 'repeat(3, 1fr)',    // was: repeat(auto-fill, minmax(280px, 1fr))
  gap: '16px',                              // was: 20px
}}>
```

**Pitfall:** The `<div>` wrapper has no `background` — it inherits from `body`. Do not add `background: var(--bg)` or `background: var(--bg-deep)`. The greeting strip gets `background: var(--bg)` with its radial overlay ONLY because it is a full-width band that needs to anchor the gradients. All other wrappers: `background: transparent`.

---

#### `apps/admin/app/components/AgentCard.tsx`
**Role:** Individual agent card
**Change type:** TARGETED_EDITS

**Analog:** `ui_kits/wchats/AgentCard.jsx` (target), CONTEXT.md AgentCard spec

**Current STATUS_COLORS pattern** (lines 26–31):
```tsx
const STATUS_COLORS = {
  ready:        { bg: 'var(--green-bg)', fg: 'var(--green)', label: 'Ready' },
  pending:      { bg: 'var(--amber-bg)', fg: 'var(--amber)', label: 'Provisioning' },
  provisioning: { bg: 'var(--amber-bg)', fg: 'var(--amber)', label: 'Provisioning' },
  error:        { bg: 'var(--red-bg)',   fg: 'var(--red)',   label: 'Error' },
}
```

**Current card wrapper pattern** (lines 111–122):
```tsx
<div style={{
  background: 'var(--bg)',          // ← REPLACE with var(--surface-1)
  border: '1px solid var(--border-soft)',
  borderRadius: 'var(--radius-xs)',  // ← REPLACE with var(--radius-md)
  boxShadow: 'var(--shadow-card)',
}}>
```

**Target STATUS_COLORS pattern** — rename amber tokens:
```tsx
const STATUS_COLORS = {
  ready:        { bg: 'var(--green-bg)', fg: 'var(--green)',   label: 'Ready' },
  pending:      { bg: 'var(--gold-bg)',  fg: 'var(--gold)',    label: 'Provisioning' },  // --amber-bg → --gold-bg
  provisioning: { bg: 'var(--gold-bg)',  fg: 'var(--gold)',    label: 'Provisioning' },
  error:        { bg: 'var(--red-bg)',   fg: 'var(--red)',     label: 'Error' },
}
// Fallback for unknown status: bg: 'var(--surface-3)', fg: 'var(--text-3)'
```

**Target card wrapper pattern:**
```tsx
// Hover state managed with useState(false) + onMouseEnter/Leave
<div
  onMouseEnter={() => setHovered(true)}
  onMouseLeave={() => setHovered(false)}
  style={{
    background: 'var(--surface-1)',
    border: `1px solid ${hovered ? 'var(--border)' : 'var(--border-soft)'}`,
    borderRadius: 'var(--radius-md)',
    boxShadow: 'var(--shadow-card)',
    transform: hovered ? 'translateY(-2px)' : 'translateY(0)',
    transition: 'transform 0.2s, box-shadow 0.2s, border-color 0.2s',
    // 1px gradient bar on top on hover (via borderTop or background-image):
    borderTop: hovered ? '1px solid var(--border-hard)' : '1px solid var(--border-soft)',
  }}
>
```

**Target status chip pattern** (UPPERCASE TRACKED):
```tsx
<span style={{
  padding: '3px 10px',
  borderRadius: 'var(--radius-pill)',
  fontSize: '10.5px',          // --t-micro
  fontWeight: 600,
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
  background: c.bg,
  color: c.fg,
  whiteSpace: 'nowrap',
}}>
  {c.label}
</span>
```

**Target agent name pattern** — Fraunces 600:
```tsx
<h3 style={{
  fontFamily: 'var(--font-display)',
  fontSize: '17px',
  fontWeight: 600,
  fontVariationSettings: '"opsz" 144, "SOFT" 30',
  color: 'var(--text-1)',
  margin: 0,
}}>
```

**Pitfall (Pitfall 5):** `var(--amber-bg)` and `var(--amber)` for status will resolve to `#E8A87C` (building warmth, NOT status warning) in the new token set. This will render status chips the wrong colour. Must rename to `var(--gold-bg)` and `var(--gold)` in STATUS_COLORS.

---

### Wave 4 — Agent Journey Screens

---

#### `apps/admin/app/agents/[id]/page.tsx`
**Role:** Agent detail right-panel (dispatch between Provision / Configure / Test)
**Change type:** TOKEN_ONLY + STATUS_MAP

**Analog:** self — same STATUS_COLORS issue as AgentCard.tsx

**Current STATUS_COLORS pattern** (lines 38–43): same as AgentCard — `--amber-bg` / `--amber` for pending/provisioning.

**Target:** Replace identically to AgentCard.tsx STATUS_COLORS (see above). The page uses `getStatusColor(agent.status).bg` / `.fg` inline — no other style changes needed beyond the map update and token auto-propagation from globals.css.

**Pitfall:** `background: 'var(--surface-2)'` on `provisioningPanel` (line 157) — `--surface-2` in the new system is `#2A1E4A` (a valid dark elevated card). This is correct — no change needed for that particular style. However, confirm all page panel wrappers are `--surface-1` or `transparent`, not `--bg` or `--bg-deep`.

---

#### `apps/admin/app/components/JourneyStepper.tsx`
**Role:** Left-rail step navigation
**Change type:** TARGETED_EDITS

**Analog:** self (hardcoded hex replacement)

**Current aside wrapper** (lines 46–53):
```tsx
<aside style={{
  width: '320px',
  borderRight: '1px solid var(--border-soft)',
  padding: '32px 24px',
  background: 'var(--bg)',    // ← REPLACE with var(--surface-1)
}}>
```

**Current active step style** (lines 87–97):
```tsx
...(visualState === 'active'
  ? { background: 'rgba(217,119,6,0.08)', border: '1px solid rgba(217,119,6,0.2)' }   // amber
  : visualState === 'done'
  ? { background: 'var(--green-bg)', border: '1px solid rgba(22,163,74,0.2)' }         // old green
  : { background: 'transparent', border: '1px solid transparent', opacity: 0.45 })
```

**Current circle style** (lines 109–113):
```tsx
...(visualState === 'done'
  ? { background: '#16A34A', color: '#fff' }                                           // hardcoded green
  : visualState === 'active'
  ? { background: 'transparent', border: '2px solid #D97706', color: '#D97706' }       // hardcoded amber
  : { background: 'var(--surface-3)', border: '1px solid var(--border)', color: 'var(--text-4)' })
```

**Current connector line** (line 187):
```tsx
background: visualState === 'done' ? '#16A34A' : 'var(--border-soft)'  // hardcoded green
```

**Target patterns:**
```tsx
// Aside wrapper:
background: 'var(--surface-1)',  // was: var(--bg)

// Active step container:
{ background: 'var(--accent-dim)', border: '1px solid var(--border-hard)' }
// Done step container:
{ background: 'var(--green-bg)', border: '1px solid var(--green-border)' }

// Active circle:
{ background: 'transparent', border: '2px solid var(--accent)', color: 'var(--accent)' }
// Done circle:
{ background: 'var(--green)', color: '#fff' }

// Done badge chip:
{ background: 'var(--green-bg)', color: 'var(--green)', borderRadius: '999px' }

// Connector line done:
background: 'var(--green)'  // was: '#16A34A'
```

**Pitfall:** Three hardcoded hex values (`#16A34A` twice, `#D97706` twice, `rgba(217,119,6,...)` twice) — all must be replaced. `rgba(22,163,74,0.2)` border on done-step also needs to become `var(--green-border)` = `rgba(111,232,170,0.25)`.

---

#### `apps/admin/app/components/StepSubtaskCard.tsx`
**Role:** Subtask card inside agent detail right panel
**Change type:** TOKEN_ONLY

**Analog:** self

**Current card wrapper** (lines 49–64):
```tsx
<div style={{
  background: 'var(--surface-2)',   // ← REPLACE with var(--surface-1)
  border: '1px solid var(--border-soft)',
  borderLeft:
    state === 'completed' ? '3px solid var(--green)'
    : state === 'active'  ? '3px solid var(--accent)'
    : '1px solid var(--border-soft)',
}}>
```

**Target pattern:**
```tsx
background: 'var(--surface-1)',  // elevate one step from --surface-2
// Border-left and icon box are already using correct token names — auto-updates from globals.css
```

**Pitfall:** None beyond the `--surface-2` → `--surface-1` swap. The green/accent left-border is already using correct variable names.

---

#### `apps/admin/app/agents/new/page.tsx`
**Role:** Create agent wizard form
**Change type:** TOKEN_ONLY

**Analog:** `apps/admin/app/agents/[id]/soul/page.tsx` (same form panel pattern)

**Key pattern to match:**
```tsx
// Form panel wrapper:
background: 'var(--surface-1)',
border: '1px solid var(--border)',
borderRadius: 'var(--radius-sm)',
// Input/select elements:
background: 'var(--surface-2)',
border: '1px solid var(--border-soft)',
color: 'var(--text-1)',
// Focus ring via globals.css: box-shadow: var(--shadow-focus)
// Label style (UPPERCASE TRACKED):
fontSize: '10.5px', fontWeight: 600, letterSpacing: '0.12em',
textTransform: 'uppercase', color: 'var(--text-3)'
// Submit button: background: var(--accent), color: var(--text-on-accent) = #0B0717
```

**Pitfall:** Check for any `background: var(--bg)` on the page wrapper — replace with `transparent`. The form panel itself gets `--surface-1`, not the page.

---

#### `apps/admin/app/agents/[id]/soul/page.tsx`
**Role:** Soul editor form
**Change type:** TOKEN_ONLY

**Analog:** self (already uses most correct variable names)

**Key target pattern additions:**
```tsx
// Agent name display in header — Fraunces italic coral:
<span style={{
  fontFamily: 'var(--font-display)',
  fontStyle: 'italic',
  fontWeight: 300,
  color: 'var(--accent)',
  fontVariationSettings: '"opsz" 144, "SOFT" 100',
}}>
  {agentName}
</span>

// Textarea fields:
background: 'var(--surface-2)',    // elevated input surface
border: '1px solid var(--border-soft)',
color: 'var(--text-1)',
// Labels:
fontSize: '10.5px', fontWeight: 600, letterSpacing: '0.12em',
textTransform: 'uppercase', color: 'var(--text-3)',
```

**Pitfall:** Search for any `background: var(--surface-2)` used as a PAGE wrapper (vs. form field). Page wrapper = transparent. Form panel wrapper = `--surface-1`. Form fields = `--surface-2`.

---

#### `apps/admin/app/agents/[id]/ingest/page.tsx`
**Role:** Document upload + KB list
**Change type:** TARGETED_EDITS

**Analog:** self — PARSE_STATUS_COLORS rename

**Current PARSE_STATUS_COLORS pattern** (lines 53–59):
```tsx
const PARSE_STATUS_COLORS = {
  complete:   { bg: 'var(--green-bg)', fg: 'var(--green)' },
  parsed:     { bg: 'var(--green-bg)', fg: 'var(--green)' },
  pending:    { bg: 'var(--amber-bg)', fg: 'var(--amber)' },   // ← RENAME
  processing: { bg: 'var(--amber-bg)', fg: 'var(--amber)' },   // ← RENAME
  failed:     { bg: 'var(--red-bg)',   fg: 'var(--red)'   },
}
```

**Target PARSE_STATUS_COLORS:**
```tsx
const PARSE_STATUS_COLORS = {
  complete:   { bg: 'var(--green-bg)', fg: 'var(--green)' },
  parsed:     { bg: 'var(--green-bg)', fg: 'var(--green)' },
  pending:    { bg: 'var(--gold-bg)',  fg: 'var(--gold)'  },   // --amber-bg → --gold-bg
  processing: { bg: 'var(--gold-bg)',  fg: 'var(--gold)'  },
  failed:     { bg: 'var(--red-bg)',   fg: 'var(--red)'   },
}
```

**Target upload zone pattern:**
```tsx
// Upload zone idle:
background: 'var(--surface-1)',
border: '2px dashed var(--border)',
// Upload zone hover:
background: 'var(--accent-dim)',
border: '2px dashed var(--border-hard)',
// Document filename: fontFamily: 'var(--font-mono)', color: 'var(--text-2)'
```

**Pitfall (Pitfall 5):** `--amber-bg` and `--amber` for pending status are the most critical rename in this file — the amber token now means warm building windows, not a warning status.

---

### Wave 5 — Eval + Deploy + Settings

---

#### `apps/admin/app/agents/[id]/eval/page.tsx`
**Role:** Eval dashboard with Recharts chart + scenario table
**Change type:** TARGETED_EDITS

**Analog:** self — Recharts line colours + add glass stat tiles

**Current Recharts line colours** (lines 660–691):
```tsx
<Line dataKey="faithfulness"      stroke="#B8860B" />  // dark gold — invisible on dark bg
<Line dataKey="answer_relevancy"  stroke="#7B1C3A" />  // wine red — was primary accent
<Line dataKey="context_precision" stroke="#4A7C59" />  // forest green — washed out
<Line dataKey="context_recall"    stroke="#4A6080" />  // steel blue — washed out
```

**Target Recharts line colours:**
```tsx
<Line dataKey="faithfulness"      stroke="#F0C674" strokeWidth={2} />  // --gold
<Line dataKey="answer_relevancy"  stroke="#F4748C" strokeWidth={2} />  // --accent (coral)
<Line dataKey="context_precision" stroke="#6FE8AA" strokeWidth={2} />  // --green
<Line dataKey="context_recall"    stroke="#B79AE0" strokeWidth={2} />  // --lilac
```

**scoreColor helper update** (line 107–111):
```tsx
// Was: if (score >= 0.9) return 'var(--green)'
//      if (score >= 0.7) return 'var(--amber)'  ← BREAKS: amber = building warmth now
//      return 'var(--red)'
// New:
function scoreColor(score: number): string {
  if (score >= 0.9) return 'var(--green)'
  if (score >= 0.7) return 'var(--gold)'   // --amber → --gold for status warning
  return 'var(--red)'
}
```

**New glass stat tile pattern** (add above the chart card — approved glass use case):
```tsx
// Aggregate score tiles — glass tiles (4 metrics from latestRun.aggregate_scores)
<div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '24px' }}>
  {Object.entries(latestRun.aggregate_scores).map(([key, value]) => (
    <div key={key} style={{
      background: 'var(--glass-bg)',
      backdropFilter: 'var(--glass-blur)',
      border: '1px solid var(--glass-border)',
      borderRadius: 'var(--radius-sm)',
      padding: '16px 20px',
    }}>
      <div style={{ fontSize: '10.5px', fontWeight: 600, letterSpacing: '0.12em',
                    textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: '8px' }}>
        {key.replace(/_/g, ' ')}
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: '28px',
                    fontWeight: 600, color: scoreColor(value) }}>
        {value.toFixed(2)}
      </div>
    </div>
  ))}
</div>
```

**scheduleStripStyle update** (line 314–325): `background: 'var(--surface-3)'` — already correct in new token system (dark elevated). Confirm `--surface-3` in new system is `#382860`.

**Pitfall (Pitfall 7):** Recharts defaults for `Tooltip` background, `CartesianGrid` stroke, axis tick colours are already overridden in this file using CSS vars — they will auto-update once globals.css replaces the tokens. Only the hardcoded hex `stroke` values on `<Line>` elements need manual replacement.

---

#### `apps/admin/app/agents/[id]/deploy/page.tsx`
**Role:** Widget customiser + deploy approval flow
**Change type:** TARGETED_EDITS

**Analog:** self — DEFAULT_CONFIG dark colours

**Current DEFAULT_CONFIG** (lines 54–73):
```tsx
const DEFAULT_CONFIG: WidgetConfig = {
  colors: {
    widget_bg:          '#FDF9F5',   // parchment light
    header_bg:          '#7B1C3A',   // wine — was primary accent
    header_text:        '#FFFFFF',
    agent_bubble_bg:    '#FDF9F5',
    agent_bubble_text:  '#4A2030',
    user_bubble_bg:     '#7B1C3A',
    user_bubble_text:   '#FFFFFF',
    send_button:        '#7B1C3A',
    input_bg:           '#F7F0EA',
  },
}
```

**Target DEFAULT_CONFIG** (dark defaults matching the new system):
```tsx
const DEFAULT_CONFIG: WidgetConfig = {
  colors: {
    widget_bg:          '#1E1638',   // --surface-1
    header_bg:          '#F4748C',   // --accent (coral)
    header_text:        '#0B0717',   // --text-on-accent
    agent_bubble_bg:    '#2A1E4A',   // --surface-2
    agent_bubble_text:  '#F4EDE5',   // --text-1
    user_bubble_bg:     '#F4748C',   // --accent
    user_bubble_text:   '#0B0717',   // --text-on-accent
    send_button:        '#F4748C',   // --accent
    input_bg:           '#2A1E4A',   // --surface-2
  },
}
```

**Target embed code block pattern:**
```tsx
// Embed code display area:
<pre style={{
  background: 'var(--surface-2)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-xs)',
  padding: '16px',
  fontFamily: 'var(--font-mono)',
  fontSize: '13px',
  color: 'var(--text-2)',
  overflowX: 'auto',
}}>
```

**Pitfall:** The widget customiser color pickers let users choose arbitrary colours — these are NOT updated to tokens; they remain raw hex. Only the DEFAULT_CONFIG starting values change.

---

#### `apps/admin/app/agents/[id]/settings/page.tsx`
**Role:** Settings placeholder form
**Change type:** TOKEN_ONLY

**Analog:** `apps/admin/app/agents/[id]/soul/page.tsx` (same form panel pattern)

The file is minimal (50 lines) — wrapper div uses no background colour, so most updates are automatic via globals.css. Confirm no hardcoded hex. Add `background: 'transparent'` to the page wrapper if needed.

---

### Wave 6 — Auth Pages + Components

---

#### `apps/admin/app/sign-in/[[...sign-in]]/page.tsx`
**Role:** Sign-in auth page
**Change type:** TARGETED_EDITS

**Analog:** `apps/admin/app/sign-up/[[...sign-up]]/page.tsx` (identical pattern)

**Current pattern** (all 9 lines):
```tsx
<main style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
  <SignIn fallbackRedirectUrl="/agents" />
</main>
```

**Target pattern** — transparent main so city shows, logo above the Clerk card:
```tsx
<main style={{
  display: 'flex',
  flexDirection: 'column',
  justifyContent: 'center',
  alignItems: 'center',
  minHeight: '100vh',
  background: 'transparent',   // city is the backdrop — no fill needed
  gap: '24px',
}}>
  {/* Logo above the card */}
  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
    <img src="/logo-mark.svg" alt="W Chats" style={{ width: '30px', height: '30px' }} />
    <img src="/wordmark.svg" alt="w.chats" style={{ height: '20px' }} />
  </div>

  {/* Clerk card — styled by clerkAppearance in layout.tsx; the dark surface-1 card floats over city */}
  <SignIn fallbackRedirectUrl="/agents" />
</main>
```

**Pitfall:** The Clerk card appearance (dark surface-1 background) is controlled by `clerkAppearance` in `layout.tsx` — it is not set on the page. Ensure `layout.tsx` Clerk appearance update (Wave 1) is done first, otherwise the Clerk card renders with light background over the dark city, creating illegible contrast.

---

#### `apps/admin/app/sign-up/[[...sign-up]]/page.tsx`
**Role:** Sign-up auth page
**Change type:** TARGETED_EDITS

**Analog:** `apps/admin/app/sign-in/[[...sign-in]]/page.tsx` — identical target pattern.

Replace `<SignIn>` with `<SignUp>`, fallbackRedirectUrl stays `/agents`. Everything else identical.

---

#### `apps/admin/app/components/UserAvatar.tsx`
**Role:** Clerk user button wrapper with icon overlay
**Change type:** TRIVIAL

**Current pattern** (lines 16–42):
```tsx
<User size={16} color="#ef4444" />  // ← hardcoded red — replace with var(--accent)
```

**Target pattern** — icon colour to accent, add lilac ring on outer wrapper:
```tsx
<div style={{
  position: 'relative',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: '50%',
  border: '1px solid rgba(183, 154, 224, 0.6)',  // --lilac ring
  padding: '1px',
}}>
  <UserButton />
  <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
                justifyContent: 'center', pointerEvents: 'none', zIndex: 1 }}>
    <User size={16} color="var(--accent)" />   {/* was: #ef4444 */}
  </div>
</div>
```

**Pitfall:** Inline style `color="#ef4444"` on Lucide icon — Lucide accepts both `color` prop (string) and CSS variable strings. `color="var(--accent)"` will work correctly as Lucide passes it to the SVG `stroke` attribute, which resolves CSS variables.

---

#### `apps/admin/app/components/SignOutTab.tsx`
**Role:** Fixed sign-out slide tab
**Change type:** TOKEN_ONLY (in globals.css, not the component)

**Current inline usage** (lines 29–30):
```tsx
<LogOut size={18} strokeWidth={2.5} style={{ transform: 'rotate(180deg)', color: '#ef4444' }} />
```

**Current globals.css pattern** (lines 154–207):
```css
.sign-out-tab { background: #1A0A0F; border: 1px solid rgba(239,68,68,0.25); }
.sign-out-tab::before { background: #ef4444; }
```

**Target component change:**
```tsx
<LogOut size={18} strokeWidth={2.5}
  style={{ transform: 'rotate(180deg)', color: 'var(--red)' }}  // was: #ef4444
/>
```

**Target globals.css `.sign-out-tab` pattern:**
```css
.sign-out-tab {
  background: var(--bg-deep);                        /* was: #1A0A0F */
  border: 1px solid rgba(255, 133, 133, 0.25);       /* was: rgba(239,68,68,0.25) — use --red value */
  box-shadow: -2px 0 8px rgba(255, 133, 133, 0.15);  /* was: rgba(239,68,68,0.15) */
}
.sign-out-tab::before { background: var(--red); }   /* was: #ef4444 */
.sign-out-tab:hover { box-shadow: -4px 0 16px rgba(255,133,133,0.25); }
```

**Pitfall:** The component itself references `.sign-out-tab` via `className` — no change to the component HTML structure. Only globals.css and the icon `color` prop change.

---

## Shared Patterns

### Shared Pattern 1: Transparent Layout Wrapper
**Apply to:** ALL page `<main>` and layout wrapper `<div>` elements that previously had `background: 'var(--bg)'`
**Files:** `page.tsx`, `agents/[id]/layout.tsx`, `sign-in/page.tsx`, `sign-up/page.tsx`, `agents/page.tsx` (outer), `agents/new/page.tsx` (outer), `agents/[id]/page.tsx` (outer)
```tsx
// Before: background: 'var(--bg)'  OR  background: 'var(--bg-deep)'
// After:  background: 'transparent'
// Rule: only cards and panels get --surface-1 fill; layout wrappers let the city through
```

### Shared Pattern 2: UPPERCASE TRACKED Micro-Label
**Apply to:** Status chips, step labels, metric labels, nav section dividers
**Files:** `AgentCard.tsx`, `JourneyStepper.tsx`, `eval/page.tsx` (metric tile labels), `agents/page.tsx` (greeting strip label)
```tsx
const microLabelStyle: React.CSSProperties = {
  fontSize: '10.5px',       // --t-micro
  fontWeight: 600,
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
  color: 'var(--text-3)',
}
```

### Shared Pattern 3: Coral Primary Button
**Apply to:** All primary CTAs (Build your agent, Create agent, Save, Run evaluations, Deploy)
**Source:** Used throughout — `page.tsx` `primaryButtonStyle`, `agents/page.tsx` `primaryButtonInline`
```tsx
const primaryButtonStyle: React.CSSProperties = {
  background: 'var(--accent)',
  color: 'var(--text-on-accent)',   // = #0B0717 — dark text on coral fill
  padding: '10px 18px',
  borderRadius: 'var(--radius-sm)',
  fontWeight: 600,
  fontSize: '14px',
  border: 'none',
  cursor: 'pointer',
}
// Sentence case always: "Build your agent", "Create agent", "Save" — never "Create Agent"
```

### Shared Pattern 4: Dark Card / Panel
**Apply to:** Cards, panels, modals, any surface containing dense text
**Source:** `AgentCard.tsx` (target), `StepSubtaskCard.tsx` (target), `eval/page.tsx` chartCardStyle
```tsx
{
  background: 'var(--surface-1)',
  border: '1px solid var(--border-soft)',
  borderRadius: 'var(--radius-md)',      // or radius-sm for smaller panels
  boxShadow: 'var(--shadow-card)',
}
// NEVER use --glass-bg on dense data cards
```

### Shared Pattern 5: Amber → Gold Status Token Rename
**Apply to:** ALL STATUS_COLORS maps and PARSE_STATUS_COLORS maps
**Files:** `AgentCard.tsx`, `agents/[id]/page.tsx`, `ingest/page.tsx`
```tsx
// Everywhere this pattern appears:
{ bg: 'var(--amber-bg)', fg: 'var(--amber)' }   // ← OLD (breaks: amber = building warmth)
// Replace with:
{ bg: 'var(--gold-bg)',  fg: 'var(--gold)' }    // ← NEW (gold = status warning)
```

### Shared Pattern 6: JetBrains Mono for Numbers / IDs / Timestamps
**Apply to:** Agent IDs, timestamps, eval scores, version strings, document filenames, event log lines
**Source:** Already used in `AgentCard.tsx` created date (line 183), `eval/page.tsx` ScoreCell
```tsx
{
  fontFamily: 'var(--font-mono)',
  letterSpacing: '0.01em',
}
```

### Shared Pattern 7: Fraunces Display Heading
**Apply to:** Page hero headings, section titles, agent name (italic-coral variant)
**Source:** `colors_and_type.css` lines 212–231
```tsx
// Upright display heading:
{
  fontFamily: 'var(--font-display)',
  fontWeight: 400,
  fontVariationSettings: '"opsz" 144, "SOFT" 30',
  letterSpacing: '-0.025em',
  lineHeight: 1.1,
}
// Italic coral accent (agent names, emphasis words):
{
  fontFamily: 'var(--font-display)',
  fontStyle: 'italic',
  fontWeight: 300,
  color: 'var(--accent)',
  fontVariationSettings: '"opsz" 144, "SOFT" 100',
}
```

---

## No Analog Found

All files have existing analogs in the codebase. No files require RESEARCH.md-only patterns as the sole reference.

---

## Critical Pitfall Summary (executor checklist)

| Pitfall | Files Affected | Risk |
|---------|---------------|------|
| `--orange` / `--orange-dim` undefined after Wave 1 | `HeroSteps.tsx`, `JourneyStepper.tsx` | HIGH — visible broken UI on step cards |
| `--amber-bg` / `--amber` used as status warning colour | `AgentCard.tsx`, `[id]/page.tsx`, `ingest/page.tsx` | HIGH — chips render warm amber (building colour) instead of gold warning |
| `--green-solid` undefined after Wave 1 | `HeroSteps.tsx`, `JourneyStepper.tsx` | HIGH — done state circles lose fill |
| `--font-pixelify` undefined after Wave 1 | `page.tsx` line 61, `TopNav.tsx` line 37 | HIGH — falls back to system sans on logo |
| `background: var(--bg)` left on layout wrappers | `page.tsx`, `agents/[id]/layout.tsx`, `JourneyStepper.tsx` aside | MEDIUM — blocks city from showing through |
| Recharts line strokes are hardcoded hex | `eval/page.tsx` lines 660–691 | MEDIUM — invisible lines on dark chart bg |
| Clerk appearance uses all Parchment hex values | `layout.tsx` lines 14–75 | HIGH — Clerk card renders light on dark auth page |
| `#ef4444` inline on Lucide icon in UserAvatar | `UserAvatar.tsx` line 39, `SignOutTab.tsx` line 29 | LOW — functional, wrong colour |
| `DEFAULT_CONFIG` widget colours are Parchment values | `deploy/page.tsx` lines 54–73 | LOW — affects only widget preview default appearance |

---

## Metadata

**Design system source:** `.claude/skills/wchats-design/`
**Analog search scope:** `apps/admin/app/` (all files), `apps/admin/app/globals.css`, `.claude/skills/wchats-design/ui_kits/wchats/`
**Files scanned:** 22 component/page files + 3 design system refs
**Pattern extraction date:** 2026-05-26
